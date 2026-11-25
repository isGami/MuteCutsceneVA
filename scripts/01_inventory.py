#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
import os
import time
from collections import Counter
from pathlib import Path
from oot_formats import ZsContext, parse_bwav


def log(msg: str) -> None:
    print(msg, flush=True)


def walk_fast(root_path: Path, romfs: Path, label: str, with_sizes: bool, progress_every: int):
    stack = [root_path]
    count = 0
    last_print = time.monotonic()
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        count += 1
                        size = entry.stat(follow_symlinks=False).st_size if with_sizes else None
                        rel = Path(entry.path).relative_to(romfs).as_posix()
                        if count % progress_every == 0 or (time.monotonic() - last_print) >= 2.0:
                            log(f"  [{label}] {count} files... ({rel})")
                            last_print = time.monotonic()
                        yield entry.name, Path(entry.path), rel, size
        except PermissionError as exc:
            log(f"  [warn] PermissionError on {current}: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Inventory Voice/Sound/SLink2/Event files in OOTNS2 RomFS.")
    parser.add_argument("--romfs", default=os.environ.get("OOT_ROMFS"), help="Path to extracted romfs directory")
    parser.add_argument("--out-dir", default="reports", help="Directory for output reports")
    parser.add_argument("--with-sizes", action="store_true", help="Also stat() every file size (slower on mounted/network filesystems)")
    parser.add_argument("--progress-every", type=int, default=500, help="Print progress every N files")
    args = parser.parse_args()

    if not args.romfs:
        raise SystemExit("Pass --romfs /path/to/romfs or set OOT_ROMFS")

    romfs = Path(args.romfs)
    if not romfs.is_dir():
        raise SystemExit(f"RomFS directory not found: {romfs}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    log(f"Starting inventory on: {romfs}")
    top_dirs = sorted([e.name for e in os.scandir(romfs) if e.is_dir(follow_symlinks=False)])
    log(f"Found {len(top_dirs)} top-level dirs: {', '.join(top_dirs)}")

    target_roots = ["Voice", "Sound", "SLink2", "Event", "GameParameter", "System"]
    present_targets = [d for d in target_roots if (romfs / d).is_dir()]
    log(f"Target roots present: {', '.join(present_targets)}")

    zs_ctx = None
    log("Loading Pack/ZsDic.pack.zs...")
    try:
        zs_ctx = ZsContext(romfs)
        log(f"Loaded {len(zs_ctx.dicts)} ZsDic dictionaries: {list(zs_ctx.dicts.keys())}")
    except Exception as exc:
        log(f"[warn] Could not load ZsDic ({exc}); continuing without .zs decompression.")

    files_by_root: dict[str, list[dict]] = {}
    ext_counts: Counter[str] = Counter()
    lang_counts: Counter[str] = Counter()
    bwav_samples: list[dict] = []

    for idx, root_name in enumerate(present_targets, 1):
        root_path = romfs / root_name
        log(f"[{idx}/{len(present_targets)}] Scanning {root_name}/ ...")
        entries = []
        for name, full_path, rel, size in walk_fast(root_path, romfs, root_name, args.with_sizes, args.progress_every):
            ext = "".join(Path(name).suffixes) or "(none)"
            ext_counts[f"{root_name}:{ext}"] += 1
            if root_name == "Voice" and "_" in name:
                lang_counts[name.split("_", 1)[0]] += 1
            entry_obj: dict = {"path": rel}
            if size is not None:
                entry_obj["size"] = size
            entries.append(entry_obj)

            if name.endswith(".bwav") and len(bwav_samples) < 10:
                try:
                    with full_path.open("rb") as f:
                        hdr = f.read(4096)
                    info = parse_bwav(hdr)
                    bwav_samples.append({"path": rel, "bwav": info})
                except Exception as exc:
                    bwav_samples.append({"path": rel, "error": str(exc)})

        entries.sort(key=lambda x: x["path"])
        files_by_root[root_name] = entries
        log(f"  [{root_name}] done: {len(entries)} files.")

    log("Writing JSON and text reports...")
    summary = {
        "romfs": str(romfs),
        "top_level_dirs": top_dirs,
        "zsdic_loaded_ids": list(zs_ctx.dicts.keys()) if zs_ctx else [],
        "root_file_counts": {k: len(v) for k, v in files_by_root.items()},
        "ext_counts": dict(ext_counts.most_common()),
        "voice_filename_prefixes": dict(lang_counts.most_common()),
        "sample_bwav_headers": bwav_samples,
    }

    (out_dir / "inventory_summary.json").write_text(json.dumps(summary, indent=2))
    (out_dir / "inventory_paths.json").write_text(json.dumps(files_by_root, indent=2))

    lines = [
        f"RomFS: {romfs}",
        f"Top-level dirs ({len(top_dirs)}): {', '.join(top_dirs)}",
        f"ZsDic dict IDs loaded: {summary['zsdic_loaded_ids']}",
        "",
        "File counts by scanned root:",
    ]
    for k, count in summary["root_file_counts"].items():
        lines.append(f"  {k:16s}: {count}")
    lines.append("")
    lines.append("Extensions by root:")
    for k, count in summary["ext_counts"].items():
        lines.append(f"  {k:32s}: {count}")
    if lang_counts:
        lines.append("")
        lines.append("Voice/ filename prefixes:")
        for k, count in summary["voice_filename_prefixes"].items():
            lines.append(f"  {k:16s}: {count}")
    if bwav_samples:
        lines.append("")
        lines.append("Sample BWAV headers (first 10):")
        for s in bwav_samples:
            lines.append(f"  {s['path']} -> {s.get('bwav') or s.get('error')}")

    report_text = "\n".join(lines)
    (out_dir / "inventory_summary.txt").write_text(report_text)
    elapsed = round(time.monotonic() - t0, 2)
    log(f"\nFinished in {elapsed}s! Summary:\n")
    log(report_text)


if __name__ == "__main__":
    main()
