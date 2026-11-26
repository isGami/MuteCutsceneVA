#!/usr/bin/env python3
"""Read-only review and allowlist template generator for Sound/ NV_*_Dm_*_SpNN.bwav clips.

Decodes one representative DSP-ADPCM BWAV per language-neutral Sp clip ID to WAV,
pairs each clip with its EventFlowMsg MSBT dialogue text, and writes:
  - <out>/wav/<clip_id>.wav
  - <out>/sp_clips.csv
  - <out>/index.html
  - optionally --write-template <path> (all IDs commented out for manual curation)
"""
from __future__ import annotations

import argparse
import csv
import html
import os
import re
from collections import defaultdict
from pathlib import Path

from build_mod import (
    CUTSCENE_VA_RE,
    DEFAULT_KEEP_SP,
    ensure_safe_target,
    keep_sp_match,
    load_keep_sp,
    sp_clip_id,
)
from oot_formats import (
    ZsContext,
    decode_dsp_adpcm_bwav,
    msbt_plain_text,
    msbt_tag_names,
    parse_msbt,
    parse_sarc,
    write_wav,
)

LANG_PREFIX_RE = re.compile(r"^NV_([A-Z]{2}[a-z]{2})_")
MALS_VERSION_RE = re.compile(r"\.Product\.(\d+)\.")
SP_TAG_NORM_RE = re.compile(r"_Sp_(\d+)$")
CLIP_SCENE_RE = re.compile(r"^(Dm_.+?)_\d{4}_Sp\d+$")


def find_mals_sarc(romfs: Path, text_lang: str) -> Path:
    """Return the highest-version Mals/<text_lang>.Product.*.sarc.zs archive."""
    mals_dir = romfs / "Mals"
    if not mals_dir.is_dir():
        raise SystemExit(f"Mals directory not found: {mals_dir}")
    candidates = sorted(
        mals_dir.glob(f"{text_lang}.Product.*.sarc.zs"),
        key=lambda p: (
            int(m.group(1)) if (m := MALS_VERSION_RE.search(p.name)) else -1,
            p.name,
        ),
    )
    if not candidates:
        raise SystemExit(f"No Mals archive found for text language {text_lang!r} in {mals_dir}")
    return candidates[-1]


def index_sp_msbt_texts(zs_ctx: ZsContext, mals_path: Path) -> dict[str, tuple[str, str, str]]:
    """Map normalized Sp tag suffix (e.g. Dm_Kokiri_0060_0000_Sp00) -> (msbt_name, label, plain_text)."""
    sarc = parse_sarc(zs_ctx.read_file(mals_path))
    index: dict[str, tuple[str, str, str]] = {}
    for msbt_name in sorted(sarc):
        if not msbt_name.startswith("EventFlowMsg/") or not msbt_name.endswith(".msbt"):
            continue
        entries = parse_msbt(sarc[msbt_name])
        for label, raw in entries.items():
            tags = msbt_tag_names(raw)
            if not tags:
                continue
            plain: str | None = None
            for tag in tags:
                norm = SP_TAG_NORM_RE.sub(r"_Sp\1", tag)
                if norm.startswith("Dm_") and "_Sp" in norm and norm not in index:
                    if plain is None:
                        plain = msbt_plain_text(raw)
                    index[norm] = (msbt_name, label, plain)
    return index


def _truncate_snippet(text: str, max_len: int = 80) -> str:
    single = " ".join(text.split())
    if not single:
        return "(no MSBT text)"
    if len(single) <= max_len:
        return single
    return single[: max_len - 3].rstrip() + "..."


def _scene_group(clip_id: str, msbt_name: str) -> str:
    if msbt_name:
        return Path(msbt_name).stem
    dm_part = clip_id.split("_Dm_", 1)[1] if "_Dm_" in clip_id else clip_id
    dm_part = f"Dm_{dm_part}" if not dm_part.startswith("Dm_") else dm_part
    m = CLIP_SCENE_RE.match(dm_part)
    if m:
        return m.group(1)
    return dm_part.rsplit("_Sp", 1)[0]


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = [
        "clip_id",
        "character",
        "msbt",
        "label",
        "audio_lang",
        "available_langs",
        "duration_sec",
        "kept",
        "wav",
        "text",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_html(path: Path, rows: list[dict[str, str]], audio_lang: str, text_lang: str) -> None:
    kept_count = sum(1 for r in rows if r["kept"] == "yes")
    trs = []
    for r in rows:
        kept_badge = '<span class="badge kept">KEPT</span>' if r["kept"] == "yes" else '<span class="badge muted">MUTED</span>'
        trs.append(
            "<tr>"
            f'<td><code>{html.escape(r["clip_id"])}</code></td>'
            f'<td>{html.escape(r["character"])}</td>'
            f'<td>{r["duration_sec"]}s</td>'
            f'<td>{html.escape(r["audio_lang"])} <small>({html.escape(r["available_langs"])})</small></td>'
            f"<td>{kept_badge}</td>"
            f'<td><audio controls preload="none" src="{html.escape(r["wav"])}"></audio></td>'
            f'<td><small>{html.escape(r["msbt"])} :: {html.escape(r["label"])}</small><br>{html.escape(r["text"])}</td>'
            "</tr>"
        )

    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>MuteCutsceneVA Sp Clip Review ({len(rows)} clips)</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 1.5rem; background: #111; color: #eee; }}
  h1 {{ margin-bottom: 0.25rem; }}
  .meta {{ color: #aaa; margin-bottom: 1rem; }}
  input[type=search] {{ padding: 0.4rem 0.6rem; width: 22rem; margin-bottom: 1rem; background: #222; color: #eee; border: 1px solid #444; border-radius: 4px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.9rem; }}
  th, td {{ border: 1px solid #2c2c2c; padding: 0.45rem 0.6rem; text-align: left; vertical-align: middle; }}
  th {{ background: #1c1c1c; position: sticky; top: 0; }}
  tr:nth-child(even) {{ background: #171717; }}
  audio {{ height: 32px; width: 230px; }}
  small {{ color: #999; }}
  .badge {{ padding: 0.15rem 0.45rem; border-radius: 3px; font-size: 0.75rem; font-weight: 600; }}
  .badge.kept {{ background: #1b4d2e; color: #9ef0b8; }}
  .badge.muted {{ background: #3b2222; color: #f0a8a8; }}
</style>
</head>
<body>
<h1>Cutscene Sp Clip Review</h1>
<p class="meta">
  {len(rows)} language-neutral clip ID(s) | audio fallback preference: <code>{html.escape(audio_lang)}</code> |
  text: <code>{html.escape(text_lang)}</code> | currently kept by allowlist: <strong>{kept_count}</strong>
</p>
<input id="filter" type="search" placeholder="Filter by clip ID, character, scene, or text..." oninput="filterRows()">
<table id="clips">
<thead>
  <tr><th>Clip ID</th><th>Character</th><th>Dur</th><th>Langs</th><th>Status</th><th>Audio</th><th>Message Text</th></tr>
</thead>
<tbody>
{"".join(trs)}
</tbody>
</table>
<script>
function filterRows() {{
  const q = document.getElementById('filter').value.toLowerCase();
  for (const tr of document.querySelectorAll('#clips tbody tr')) {{
    tr.style.display = tr.textContent.toLowerCase().includes(q) ? '' : 'none';
  }}
}}
</script>
</body>
</html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc, encoding="utf-8")


def write_template(path: Path, rows: list[dict[str, str]]) -> None:
    lines = [
        "# MuteCutsceneVA Sound/ Sp Clip Allowlist",
        "#",
        "# Lines in this file list Sound/Resource/Stream/NV_*_Dm_*_SpNN.bwav clips to KEEP",
        "# unmuted (for non-verbal grunts, laughs, pants, snores, etc.).",
        "#",
        "# Syntax:",
        "#   - One pattern per line; '#' starts a comment, blank lines are ignored.",
        "#   - Exact clip ID (applies to all languages):  Saria_Dm_Kokiri_0060_0000_Sp00",
        "#   - Wildcard / glob pattern:                  Talon_*",
        "#   - Full filename stem (single language):     NV_JPja_Custom_Saria_Dm_Kokiri_0060_0000_Sp00",
        "#",
        "# Uncomment entries below after listening in reports/sp_review/index.html, then rebuild:",
        "#   uv run scripts/build_mod.py --romfs /path/to/romfs",
        "",
    ]
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        grouped[(r["character"], _scene_group(r["clip_id"], r["msbt"]))].append(r)

    for (character, scene), items in sorted(grouped.items()):
        lines.append(f"# --- {character} / {scene} ---")
        for r in items:
            snippet = _truncate_snippet(r["text"])
            lines.append(f"# {r['clip_id']:<56} # {float(r['duration_sec']):5.2f}s | {snippet}")
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export WAVs and MSBT dialogue text for Sound/ NV_*_Dm_*_SpNN.bwav clips."
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
        default=Path("reports/sp_review"),
        help="Output review directory (default: reports/sp_review)",
    )
    parser.add_argument(
        "--lang",
        default="USen",
        help="Preferred voice language for WAV export (default: USen; falls back to unprefixed '--' then first available)",
    )
    parser.add_argument(
        "--text-lang",
        default="USen",
        help="Mals language archive for dialogue text lookup (default: USen)",
    )
    parser.add_argument(
        "--keep-sp",
        type=Path,
        default=DEFAULT_KEEP_SP,
        help=f"Allowlist file used to mark current kept status (default: {DEFAULT_KEEP_SP})",
    )
    parser.add_argument(
        "--write-template",
        type=Path,
        default=None,
        help="Optional path to write a commented-out keep_sp_clips.txt template",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite --write-template if it already exists",
    )
    args = parser.parse_args()

    if args.romfs is None:
        raise SystemExit("Pass --romfs <path/to/romfs> or set the OOT_ROMFS environment variable.")
    romfs: Path = args.romfs
    if not romfs.is_dir():
        raise SystemExit(f"Source RomFS not found: {romfs}")

    ensure_safe_target(args.out, romfs, "--out")
    if args.write_template is not None:
        ensure_safe_target(args.write_template, romfs, "--write-template")
        if args.write_template.exists() and not args.force:
            raise SystemExit(
                f"Template target already exists: {args.write_template} (pass --force to overwrite)"
            )

    stream_dir = romfs / "Sound" / "Resource" / "Stream"
    if not stream_dir.is_dir():
        raise SystemExit(f"Stream directory not found: {stream_dir}")

    keep_patterns = load_keep_sp(args.keep_sp) if args.keep_sp and args.keep_sp.is_file() else []

    by_id: dict[str, dict[str, Path]] = defaultdict(dict)
    for p in sorted(stream_dir.iterdir()):
        if not p.is_file() or p.suffix != ".bwav" or not CUTSCENE_VA_RE.match(p.name):
            continue
        cid = sp_clip_id(p.name)
        if cid is None:
            continue
        m = LANG_PREFIX_RE.match(p.name)
        lang_code = m.group(1) if m else "--"
        by_id[cid][lang_code] = p

    zs_ctx = ZsContext(romfs)
    mals_path = find_mals_sarc(romfs, args.text_lang)
    msbt_index = index_sp_msbt_texts(zs_ctx, mals_path)

    wav_dir = args.out / "wav"
    rows: list[dict[str, str]] = []
    missing_texts: list[str] = []

    for cid in sorted(by_id):
        lang_files = by_id[cid]
        if args.lang in lang_files:
            audio_lang = args.lang
        elif "--" in lang_files:
            audio_lang = "--"
        else:
            audio_lang = sorted(lang_files)[0]
        chosen_path = lang_files[audio_lang]

        sr, channels = decode_dsp_adpcm_bwav(chosen_path.read_bytes())
        dur_sec = round(len(channels[0]) / sr, 3) if sr and channels else 0.0
        wav_rel = f"wav/{cid}.wav"
        write_wav(wav_dir / f"{cid}.wav", sr, channels)

        character = cid.split("_Dm_", 1)[0] if "_Dm_" in cid else cid
        dm_suffix = f"Dm_{cid.split('_Dm_', 1)[1]}" if "_Dm_" in cid else cid
        hit = msbt_index.get(dm_suffix)
        if hit is not None:
            msbt_name, label, plain_text = hit
        else:
            msbt_name, label, plain_text = "", "", ""
            missing_texts.append(cid)

        kept = any(keep_sp_match(p.name, keep_patterns) is not None for p in lang_files.values())
        rows.append({
            "clip_id": cid,
            "character": character,
            "msbt": msbt_name,
            "label": label,
            "audio_lang": audio_lang,
            "available_langs": ",".join(sorted(lang_files)),
            "duration_sec": f"{dur_sec:.3f}",
            "kept": "yes" if kept else "no",
            "wav": wav_rel,
            "text": plain_text,
        })

    csv_path = args.out / "sp_clips.csv"
    html_path = args.out / "index.html"
    write_csv(csv_path, rows)
    write_html(html_path, rows, args.lang, args.text_lang)

    if args.write_template is not None:
        write_template(args.write_template, rows)
        print(f"Wrote allowlist template: {args.write_template}")

    print(f"Clip IDs:      {len(rows)}")
    print(f"WAVs written:  {len(rows)} -> {wav_dir}")
    print(f"Review CSV:    {csv_path}")
    print(f"Review HTML:   {html_path}")
    print(f"Missing texts: {len(missing_texts)}")
    if missing_texts:
        for cid in missing_texts[:20]:
            print(f"  - {cid}")
        if len(missing_texts) > 20:
            print(f"  ... ({len(missing_texts) - 20} more)")


if __name__ == "__main__":
    main()
