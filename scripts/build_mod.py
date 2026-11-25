#!/usr/bin/env python3
"""Build the MuteCutsceneVA RomFS mod for OOTNS2 (Switch 2 / Longshot).

Mutes voice-acted cutscene dialogue across two audio layers:
1. `Voice/Resource/Stream/*EventFlowMsg-_Dm_*.bwav` (Opus codec 2, 9 languages +
   shared unprefixed streams): replaces every Opus packet with a libopus-verified
   silent CELT packet while keeping packet count, `samples_full`, and header
   intact (all outputs <= vanilla size; `Voice/Resource/*.bars.zs` are metadata-only
   and left untouched).
2. `Sound/Resource/Stream/NV_*_Dm_*_SpNN.bwav` and their matching `is_prefetch`
   BWAV entries inside `Sound/Resource/NV_*.bars.zs` (DSP-ADPCM codec 1): zeroes
   the sample payload and ADPCM history in-place (<= vanilla size).

All non-cutscene audio (Link `Vo_*` grunts, `Voice/` `LayoutMsg-_MessageInGame_00`
prompts, non-`Dm_` `NV_*` NPC reactions/grunts, BGM, and SFX) is left untouched.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import time
import zlib
from pathlib import Path
from typing import Any

from oot_formats import (
    ZsContext,
    embed_silent_bwavs_in_bars,
    parse_bars,
    parse_bwav,
    parse_opus_bwav,
    patch_bars_bwav_crcs,
    patch_bars_bwavs,
    silence_bwav,
    silence_opus_bwav,
)
from opus_silence import OpusChecker, make_silent_packet

CUTSCENE_VA_RE = re.compile(r"^NV_.+_Dm_.+_Sp\d+\.bwav$")
VOICE_CUTSCENE_RE = re.compile(r"^(?:[A-Za-z]{4}_)?EventFlowMsg-_Dm_.+\.bwav$")
VOICE_BARS_CUTSCENE_RE = re.compile(r"^(?:[A-Za-z]{4}_)?EventFlowMsg-_Dm_.+\.bars\.zs$")
CUSTOM_CHAR_RE = re.compile(r"^NV_[A-Za-z]{4}_Custom_(.+?)(?:_\d+.*)?$")
DEFAULT_OUT = Path("dist/MuteCutsceneVA/romfs")
DEFAULT_REPORT_DIR = Path("reports")


def log(msg: str) -> None:
    print(msg, flush=True)


def ensure_safe_target(target: Path, romfs: Path, label: str) -> None:
    """Abort if target equals, contains, or lies inside the read-only source RomFS tree."""
    res_target = target.resolve()
    res_romfs = romfs.resolve()
    if res_target == res_romfs or res_romfs in res_target.parents or res_target in res_romfs.parents:
        raise SystemExit(
            f"SAFETY ABORT: {label} ({res_target}) overlaps read-only source RomFS ({res_romfs})."
        )


def matches_custom_chars(name: str, custom_chars: set[str], all_custom: bool) -> bool:
    if not name.startswith("NV_"):
        return False
    parts = name.split("_")
    # NV_<lang>_Custom_<Char>...
    if len(parts) >= 4 and parts[2] == "Custom":
        if all_custom:
            return True
        return any(name.startswith(f"NV_{parts[1]}_Custom_{c}_") or name.startswith(f"NV_{parts[1]}_Custom_{c}.") for c in custom_chars)
    return False


def collect_streams(
    stream_dir: Path,
    cutscene_filters: list[str],
    custom_chars: set[str],
    all_custom: bool,
) -> tuple[list[Path], list[Path]]:
    """Return (target_sound_bwavs, preserved_nv_bwavs) from Sound/Resource/Stream/."""
    target_va: list[Path] = []
    preserved_nv: list[Path] = []
    for p in sorted(stream_dir.iterdir()):
        if not p.is_file() or p.suffix != ".bwav":
            continue
        name = p.name
        is_cutscene = bool(CUTSCENE_VA_RE.match(name)) and (
            not cutscene_filters or any(f in name for f in cutscene_filters)
        )
        is_custom = matches_custom_chars(name, custom_chars, all_custom)
        if is_cutscene or is_custom:
            target_va.append(p)
        elif name.startswith("NV_"):
            preserved_nv.append(p)
    return target_va, preserved_nv


def collect_voice_streams(
    voice_stream_dir: Path,
    cutscene_filters: list[str],
) -> tuple[list[Path], list[Path]]:
    """Return (target_voice_bwavs, preserved_voice_bwavs) from Voice/Resource/Stream/."""
    target_voice: list[Path] = []
    preserved_voice: list[Path] = []
    for p in sorted(voice_stream_dir.iterdir()):
        if not p.is_file() or p.suffix != ".bwav":
            continue
        name = p.name
        if VOICE_CUTSCENE_RE.match(name) and (
            not cutscene_filters or any(f in name for f in cutscene_filters)
        ):
            target_voice.append(p)
        else:
            preserved_voice.append(p)
    return target_voice, preserved_voice


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build MuteCutsceneVA RomFS mod (Stage 1 prototype or Stage 2 full build)."
    )
    parser.add_argument(
        "--romfs",
        type=Path,
        default=Path(os.environ["OOT_ROMFS"]) if os.environ.get("OOT_ROMFS") else None,
        help="Read-only path to extracted OOTNS2 romfs (or set OOT_ROMFS env var)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="Staged mod romfs output directory (default: dist/MuteCutsceneVA/romfs)",
    )
    parser.add_argument(
        "--cutscene",
        default=None,
        help="Comma-separated cutscene substring filter(s) for Stage 1 prototype (e.g. Dm_Kokiri_0050,Dm_Kokiri_0060). Omit for all cutscenes.",
    )
    parser.add_argument(
        "--custom-chars",
        default="",
        help="Comma-separated NV_<lang>_Custom_<Char> names to mute completely (e.g. Saria,Navi).",
    )
    parser.add_argument(
        "--all-custom",
        action="store_true",
        help="Mute all NV_<lang>_Custom_* character voice archives in addition to NV_*_Dm_*_SpNN.",
    )
    parser.add_argument(
        "--voice-mode",
        choices=("embed", "stream"),
        default="embed",
        help=(
            "How to mute Voice/ cutscenes: 'embed' (default) embeds silent non-streamed DSP-ADPCM BWAVs inside "
            "Voice/Resource/*.bars.zs to bypass Longshot's Voice/Resource//Stream/ mod-matching bug; "
            "'stream' silences Voice/Resource/Stream/*.bwav Opus streams directly."
        ),
    )
    parser.add_argument(
        "--sync-to",
        type=Path,
        default=None,
        help="Optional Longshot mod romfs path to sync after staging (e.g. <longshot_game_dir>/mods/MuteCutsceneVA/romfs)",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=DEFAULT_REPORT_DIR,
        help="Directory for build_audit.json and build_audit.txt (default: reports)",
    )
    args = parser.parse_args()

    if args.romfs is None:
        raise SystemExit("Pass --romfs <path/to/romfs> or set the OOT_ROMFS environment variable.")

    romfs: Path = args.romfs
    out_romfs: Path = args.out
    if not romfs.is_dir():
        raise SystemExit(f"Source RomFS not found: {romfs}")

    voice_stream_dir = romfs / "Voice" / "Resource" / "Stream"
    if not voice_stream_dir.is_dir():
        raise SystemExit(
            f"Incomplete source RomFS: missing {voice_stream_dir}. "
            "Provide a complete unpacked OOTNS2 RomFS that includes Voice/."
        )

    ensure_safe_target(out_romfs, romfs, "--out")
    if args.sync_to is not None:
        ensure_safe_target(args.sync_to, romfs, "--sync-to")

    t0 = time.monotonic()
    cutscene_filters = [c.strip() for c in (args.cutscene or "").split(",") if c.strip()]
    custom_chars = {c.strip() for c in args.custom_chars.split(",") if c.strip()}
    mode_parts = []
    if cutscene_filters:
        mode_parts.append(f"cutscene={','.join(cutscene_filters)}")
    if custom_chars:
        mode_parts.append(f"custom_chars={','.join(sorted(custom_chars))}")
    if args.all_custom:
        mode_parts.append("all_custom")
    mode_label = " ".join(mode_parts) if mode_parts else "full"
    log(f"Building MuteCutsceneVA ({mode_label})")
    log(f"  OOT_ROMFS (read-only): {romfs}")
    log(f"  Staged output:         {out_romfs}")

    stream_dir = romfs / "Sound" / "Resource" / "Stream"
    voice_res_dir = romfs / "Voice" / "Resource"
    target_voice_bars = [
        p for p in sorted(voice_res_dir.glob("*.bars.zs"))
        if VOICE_BARS_CUTSCENE_RE.match(p.name)
        and (not cutscene_filters or any(f in p.name for f in cutscene_filters))
    ]
    preserved_voice_bars_count = len(list(voice_res_dir.glob("*.bars.zs"))) - len(target_voice_bars)
    target_voice_va, preserved_voice_va = collect_voice_streams(voice_stream_dir, cutscene_filters)
    target_sound_va, preserved_nv = collect_streams(stream_dir, cutscene_filters, custom_chars, args.all_custom)
    if args.voice_mode == "embed":
        if not target_voice_bars and not target_sound_va:
            raise SystemExit(f"No matching cutscene VA archives/streams found (filter={args.cutscene!r}).")
        log(
            f"Found {len(target_voice_bars)} target Voice/ .bars.zs archive(s) ({preserved_voice_bars_count} preserved) "
            f"and {len(target_sound_va)} target Sound/ NV_ stream(s) ({len(preserved_nv)} NV_ preserved)."
        )
    else:
        if not target_voice_va and not target_sound_va:
            raise SystemExit(f"No matching cutscene VA streams found (filter={args.cutscene!r}).")
        log(
            f"Found {len(target_voice_va)} target Voice/ stream(s) ({len(preserved_voice_va)} preserved) and "
            f"{len(target_sound_va)} target Sound/ NV_ stream(s) ({len(preserved_nv)} NV_ preserved)."
        )

    # Clean staged output so stale files from previous runs do not linger
    if out_romfs.exists():
        shutil.rmtree(out_romfs)
    out_romfs.mkdir(parents=True, exist_ok=True)

    zs_ctx = ZsContext(romfs)

    # 1. Mute Voice/ cutscenes (either embed silent DSP-ADPCM BWAVs in Voice/Resource/*.bars.zs or silence Stream/*.bwav)
    muted_voice_audit: list[dict[str, Any]] = []
    skipped_damaged_voice: list[str] = []
    patched_voice_bars_audit: list[dict[str, Any]] = []
    if args.voice_mode == "embed" and target_voice_bars:
        out_voice_res_dir = out_romfs / "Voice" / "Resource"
        out_voice_res_dir.mkdir(parents=True, exist_ok=True)
        for bars_path in target_voice_bars:
            orig_zs = bars_path.read_bytes()
            orig_raw = zs_ctx.decompress(orig_zs)
            patched_raw, entry_audit = embed_silent_bwavs_in_bars(orig_raw, target_crcs=None)
            patched_zs = zs_ctx.compress_like(orig_zs, patched_raw)
            (out_voice_res_dir / bars_path.name).write_bytes(patched_zs)

            patched_voice_bars_audit.append({
                "rel_path": f"Voice/Resource/{bars_path.name}",
                "total_entries": len(entry_audit),
                "updated_entries": len(entry_audit),
                "vanilla_raw_bytes": len(orig_raw),
                "mod_raw_bytes": len(patched_raw),
                "vanilla_zs_bytes": len(orig_zs),
                "mod_zs_bytes": len(patched_zs),
                "entries": entry_audit,
            })
        log(
            f"Embedded silent DSP-ADPCM BWAVs into {len(patched_voice_bars_audit)} "
            f"Voice/Resource/*.bars.zs archive(s)."
        )
    elif args.voice_mode == "stream" and target_voice_va:
        checker = OpusChecker()
        silent_pkt, silent_rng = make_silent_packet(checker)

        # Find first valid Opus (codec 2) Voice/ stream for range reproduction sanity check
        sanity_checked = False
        for src in target_voice_va:
            raw_hdr = src.read_bytes()
            if raw_hdr[:4] != b"BWAV":
                continue
            info_hdr = parse_bwav(raw_hdr)
            if info_hdr["channels"] and info_hdr["channels"][0]["codec"] == 2:
                first_parsed = parse_opus_bwav(raw_hdr)
                repro = checker.check_range_reproduction(first_parsed["packets"])
                if repro != len(first_parsed["packets"]):
                    raise RuntimeError(
                        f"libopus range reproduction sanity check failed on {src.name}: "
                        f"{repro}/{len(first_parsed['packets'])}"
                    )
                log(
                    f"Verified libopus silent packet ({silent_pkt.hex()}, final_range={silent_rng:#010x}) and "
                    f"100% range reproduction ({repro}/{len(first_parsed['packets'])}) on {src.name}."
                )
                sanity_checked = True
                break
        if not sanity_checked:
            log(f"Verified libopus silent packet ({silent_pkt.hex()}, final_range={silent_rng:#010x}).")

        out_voice_stream_dir = out_romfs / "Voice" / "Resource" / "Stream"
        out_voice_stream_dir.mkdir(parents=True, exist_ok=True)
        voice_crc_to_new_bwav_crc: dict[int, int] = {}
        voice_crc_to_name: dict[int, str] = {}
        for src in target_voice_va:
            vanilla_bytes = src.read_bytes()
            if vanilla_bytes[:4] != b"BWAV":
                rel = f"Voice/Resource/Stream/{src.name}"
                log(f"  [warn] Skipping non-BWAV/damaged vanilla file: {rel}")
                skipped_damaged_voice.append(rel)
                continue

            info = parse_bwav(vanilla_bytes)
            codecs = {ch["codec"] for ch in info["channels"]}
            if codecs == {2}:
                silenced_bytes = silence_opus_bwav(vanilla_bytes, silent_pkt, silent_rng)
            elif codecs == {1}:
                silenced_bytes = silence_bwav(vanilla_bytes)
            else:
                raise ValueError(f"Unsupported BWAV codec set {codecs} in {src.name}")

            if len(silenced_bytes) > len(vanilla_bytes):
                raise RuntimeError(
                    f"Size regression on {src.name}: {len(silenced_bytes)} > {len(vanilla_bytes)}"
                )
            (out_voice_stream_dir / src.name).write_bytes(silenced_bytes)
            stem = src.stem
            stem_crc = zlib.crc32(stem.encode("utf-8")) & 0xFFFFFFFF
            new_bwav_crc = struct.unpack_from("<I", silenced_bytes, 0x08)[0]
            voice_crc_to_new_bwav_crc[stem_crc] = new_bwav_crc
            voice_crc_to_name[stem_crc] = stem
            muted_voice_audit.append({
                "rel_path": f"Voice/Resource/Stream/{src.name}",
                "codec": next(iter(codecs)),
                "channels": info["num_channels"],
                "crc32": f"{stem_crc:08x}",
                "new_bwav_crc32": f"{new_bwav_crc:08x}",
                "vanilla_bytes": len(vanilla_bytes),
                "mod_bytes": len(silenced_bytes),
            })
        log(f"Silenced {len(muted_voice_audit)} Voice/ .bwav stream(s) (skipped damaged: {len(skipped_damaged_voice)}).")

        voice_res_dir = romfs / "Voice" / "Resource"
        out_voice_res_dir = out_romfs / "Voice" / "Resource"
        out_voice_res_dir.mkdir(parents=True, exist_ok=True)
        matched_voice_crcs: set[int] = set()
        for bars_path in sorted(voice_res_dir.glob("*.bars.zs")):
            if cutscene_filters and not any(f in bars_path.name for f in cutscene_filters):
                continue
            orig_zs = bars_path.read_bytes()
            orig_raw = zs_ctx.decompress(orig_zs)
            patched_raw, entry_audit = patch_bars_bwav_crcs(orig_raw, voice_crc_to_new_bwav_crc)
            if not entry_audit:
                continue
            if len(patched_raw) > len(orig_raw):
                raise RuntimeError(
                    f"Decompressed size regression on Voice/{bars_path.name}: {len(patched_raw)} > {len(orig_raw)}"
                )
            patched_zs = zs_ctx.compress_like(orig_zs, patched_raw)
            (out_voice_res_dir / bars_path.name).write_bytes(patched_zs)

            all_entries = parse_bars(orig_raw)
            updated_entries = []
            for item in entry_audit:
                c_int = int(item["crc32"], 16)
                matched_voice_crcs.add(c_int)
                item["name"] = voice_crc_to_name.get(c_int, "?")
                updated_entries.append(item)

            patched_voice_bars_audit.append({
                "rel_path": f"Voice/Resource/{bars_path.name}",
                "total_entries": len(all_entries),
                "updated_entries": len(updated_entries),
                "vanilla_raw_bytes": len(orig_raw),
                "mod_raw_bytes": len(patched_raw),
                "vanilla_zs_bytes": len(orig_zs),
                "mod_zs_bytes": len(patched_zs),
                "entries": updated_entries,
            })

        log(
            f"Updated BWAV CRC32s in {len(patched_voice_bars_audit)} Voice/Resource/*.bars.zs archive(s) "
            f"({len(matched_voice_crcs)}/{len(voice_crc_to_new_bwav_crc)} streams matched)."
        )

    # 2. Silence target Sound/Resource/Stream/*.bwav and patch matching Sound/Resource/NV_*.bars.zs prefetches
    muted_sound_audit: list[dict[str, Any]] = []
    patched_bars_audit: list[dict[str, Any]] = []
    if target_sound_va or custom_chars or args.all_custom:
        out_stream_dir = out_romfs / "Sound" / "Resource" / "Stream"
        out_stream_dir.mkdir(parents=True, exist_ok=True)

        target_crc_to_name: dict[int, str] = {}
        for src in target_sound_va:
            vanilla_bytes = src.read_bytes()
            silenced_bytes = silence_bwav(vanilla_bytes)
            if len(silenced_bytes) > len(vanilla_bytes):
                raise RuntimeError(f"Size regression on {src.name}: {len(silenced_bytes)} > {len(vanilla_bytes)}")

            (out_stream_dir / src.name).write_bytes(silenced_bytes)

            stem = src.stem
            crc = zlib.crc32(stem.encode("utf-8")) & 0xFFFFFFFF
            target_crc_to_name[crc] = stem
            muted_sound_audit.append({
                "rel_path": f"Sound/Resource/Stream/{src.name}",
                "crc32": f"{crc:08x}",
                "vanilla_bytes": len(vanilla_bytes),
                "mod_bytes": len(silenced_bytes),
            })

        log(f"Silenced {len(muted_sound_audit)} Sound/ NV_ .bwav stream(s).")

        res_dir = romfs / "Sound" / "Resource"
        out_res_dir = out_romfs / "Sound" / "Resource"
        out_res_dir.mkdir(parents=True, exist_ok=True)

        target_crcs = set(target_crc_to_name.keys())
        matched_crcs: set[int] = set()

        nv_bars_files = sorted(res_dir.glob("NV_*.bars.zs"))
        for bars_path in nv_bars_files:
            orig_zs = bars_path.read_bytes()
            orig_raw = zs_ctx.decompress(orig_zs)
            mute_entire_archive = matches_custom_chars(bars_path.name, custom_chars, args.all_custom)
            patched_raw, entry_audit = patch_bars_bwavs(
                orig_raw,
                None if mute_entire_archive else target_crcs,
            )
            if not entry_audit:
                continue

            if len(patched_raw) > len(orig_raw):
                raise RuntimeError(
                    f"Decompressed size regression on {bars_path.name}: {len(patched_raw)} > {len(orig_raw)}"
                )

            patched_zs = zs_ctx.compress_like(orig_zs, patched_raw)
            (out_res_dir / bars_path.name).write_bytes(patched_zs)

            all_entries = parse_bars(orig_raw)
            silenced_entries = []
            for item in entry_audit:
                c_int = int(item["crc32"], 16)
                matched_crcs.add(c_int)
                item["name"] = target_crc_to_name.get(c_int, "?")
                silenced_entries.append(item)

            patched_bars_audit.append({
                "rel_path": f"Sound/Resource/{bars_path.name}",
                "total_entries": len(all_entries),
                "muted_entries": len(silenced_entries),
                "preserved_entries": len(all_entries) - len(silenced_entries),
                "vanilla_raw_bytes": len(orig_raw),
                "mod_raw_bytes": len(patched_raw),
                "vanilla_zs_bytes": len(orig_zs),
                "mod_zs_bytes": len(patched_zs),
                "entries": silenced_entries,
            })

        unmatched_crcs = target_crcs - matched_crcs
        if unmatched_crcs:
            missing = [target_crc_to_name[c] for c in sorted(unmatched_crcs)]
            raise RuntimeError(f"Some target streams had no matching BARS prefetch entry: {missing[:10]}")

        log(f"Patched {len(patched_bars_audit)} NV_*.bars.zs archive(s) (100% BARS prefetch coverage).")

    # 3. Write audit reports
    args.report_dir.mkdir(parents=True, exist_ok=True)
    audit_data = {
        "mode": mode_label,
        "cutscene_filters": cutscene_filters,
        "elapsed_sec": round(time.monotonic() - t0, 2),
        "summary": {
            "muted_voice_streams": len(muted_voice_audit),
            "skipped_damaged_voice_streams": len(skipped_damaged_voice),
            "patched_voice_bars_archives": len(patched_voice_bars_audit),
            "preserved_voice_streams": len(preserved_voice_va),
            "muted_sound_streams": len(muted_sound_audit),
            "patched_bars_archives": len(patched_bars_audit),
            "preserved_nv_streams": len(preserved_nv),
        },
        "muted_voice_streams": muted_voice_audit,
        "skipped_damaged_voice_streams": skipped_damaged_voice,
        "patched_voice_bars_archives": patched_voice_bars_audit,
        "preserved_voice_streams": [f"Voice/Resource/Stream/{p.name}" for p in preserved_voice_va],
        "patched_bars_archives": patched_bars_audit,
        "muted_sound_streams": muted_sound_audit,
        "preserved_nv_streams": [f"Sound/Resource/Stream/{p.name}" for p in preserved_nv],
    }
    json_path = args.report_dir / "build_audit.json"
    json_path.write_text(json.dumps(audit_data, indent=2))

    txt_lines = [
        f"MuteCutsceneVA Build Audit ({mode_label})",
        f"Elapsed: {audit_data['elapsed_sec']}s",
        f"Muted Voice/ .bwav streams: {len(muted_voice_audit)} (skipped damaged: {len(skipped_damaged_voice)}, preserved: {len(preserved_voice_va)})",
        f"Patched Voice/ .bars.zs archives: {len(patched_voice_bars_audit)}",
        f"Muted Sound/ NV_ .bwav streams: {len(muted_sound_audit)} (preserved: {len(preserved_nv)})",
        f"Patched Sound/ NV_*.bars.zs archives: {len(patched_bars_audit)}",
        "",
        "Patched Voice/ .bars.zs archives (first 30):",
    ]
    for b in patched_voice_bars_audit[:30]:
        txt_lines.append(
            f"  {b['rel_path']}: updated {b['updated_entries']}/{b['total_entries']}, "
            f"raw={b['mod_raw_bytes']} (vanilla {b['vanilla_raw_bytes']})"
        )
    if len(patched_voice_bars_audit) > 30:
        txt_lines.append(f"  ... ({len(patched_voice_bars_audit) - 30} more in build_audit.json)")
    txt_lines.extend([
        "",
        "Patched Sound/ .bars.zs archives:",
    ])
    for b in patched_bars_audit:
        txt_lines.append(
            f"  {b['rel_path']}: muted {b['muted_entries']}/{b['total_entries']} "
            f"(preserved {b['preserved_entries']}), raw={b['mod_raw_bytes']}<={b['vanilla_raw_bytes']}"
        )
    txt_lines.append("")
    txt_lines.append("Muted Voice/ .bwav streams (first 50):")
    for s in muted_voice_audit[:50]:
        txt_lines.append(f"  {s['rel_path']} ({s['mod_bytes']} <= {s['vanilla_bytes']} bytes)")
    if len(muted_voice_audit) > 50:
        txt_lines.append(f"  ... ({len(muted_voice_audit) - 50} more in build_audit.json)")
    txt_lines.append("")
    txt_lines.append("Muted Sound/ .bwav streams (first 50):")
    for s in muted_sound_audit[:50]:
        txt_lines.append(f"  {s['rel_path']} ({s['mod_bytes']} bytes)")
    if len(muted_sound_audit) > 50:
        txt_lines.append(f"  ... ({len(muted_sound_audit) - 50} more in build_audit.json)")

    txt_path = args.report_dir / "build_audit.txt"
    txt_path.write_text("\n".join(txt_lines) + "\n")
    log(f"Wrote audit reports: {txt_path}, {json_path}")

    # 4. Optional sync to Longshot mods folder
    if args.sync_to is not None:
        if args.sync_to.exists():
            shutil.rmtree(args.sync_to)
        shutil.copytree(out_romfs, args.sync_to)
        log(f"Synced staged build to Longshot mod path: {args.sync_to}")


if __name__ == "__main__":
    main()
