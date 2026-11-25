#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
import os
import time
from pathlib import Path
from oot_formats import ZsContext, parse_bars


def log(msg: str) -> None:
    print(msg, flush=True)


def find_bars_fast(base: Path, romfs: Path, filt: str) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    stack = [base]
    scanned = 0
    last_print = time.monotonic()
    while stack:
        current = stack.pop()
        with os.scandir(current) as it:
            for entry in it:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    scanned += 1
                    if scanned % 1000 == 0 or (time.monotonic() - last_print) >= 2.0:
                        log(f"  scanned {scanned} files in {base.name}/ (found {len(found)} .bars so far)...")
                        last_print = time.monotonic()
                    if ".bars" in entry.name:
                        p = Path(entry.path)
                        rel = p.relative_to(romfs).as_posix()
                        if not filt or filt in rel.lower():
                            found.append((rel, p))
    found.sort(key=lambda x: x[0])
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect .bars / .bars.zs archives in OOTNS2 RomFS.")
    parser.add_argument("--romfs", default=os.environ.get("OOT_ROMFS"), help="Path to extracted romfs directory")
    parser.add_argument("--filter", default="", help="Substring filter on relative path (e.g. Voice, Dm_, Kokiri, NV_)")
    parser.add_argument("--limit", type=int, default=20, help="Max BARS files to inspect (0 = unlimited)")
    parser.add_argument("--out", default="reports/bars_inspection.json", help="JSON output path")
    args = parser.parse_args()

    if not args.romfs:
        raise SystemExit("Pass --romfs /path/to/romfs or set OOT_ROMFS")

    t0 = time.monotonic()
    romfs = Path(args.romfs)
    log(f"Loading ZsDic from {romfs / 'Pack' / 'ZsDic.pack.zs'}...")
    zs_ctx = ZsContext(romfs)
    log(f"Loaded {len(zs_ctx.dicts)} ZsDic dictionaries.")

    filt = args.filter.lower()
    bars_files: list[tuple[str, Path]] = []
    for sub in ("Voice", "Sound"):
        base = romfs / sub
        if not base.is_dir():
            continue
        log(f"Searching {sub}/ for .bars files...")
        found = find_bars_fast(base, romfs, filt)
        log(f"  found {len(found)} matching .bars files in {sub}/.")
        bars_files.extend(found)

    if args.limit > 0:
        bars_files = bars_files[: args.limit]

    total = len(bars_files)
    log(f"Inspecting {total} BARS archive(s)...")
    results = []
    for idx, (rel, p) in enumerate(bars_files, 1):
        log(f"[{idx}/{total}] {rel}")
        try:
            raw = zs_ctx.read_file(p)
            entries = parse_bars(raw)
            results.append({"path": rel, "asset_count": len(entries), "entries": entries})
            log(f"  -> {len(entries)} assets")
            for e in entries[:15]:
                am = e["amta"]
                name = am.get("name", "?")
                atype = am.get("type", "?")
                dur = am.get("duration_sec", "?")
                vols = [t["volume"] for t in am.get("tracks", [])]
                log(f"     [{e['index']:03d}] {name:40s} type={atype:6s} dur={dur}s magic={e['asset_magic']} vol={vols}")
            if len(entries) > 15:
                log(f"     ... ({len(entries) - 15} more; see {args.out})")
        except Exception as exc:
            results.append({"path": rel, "error": str(exc)})
            log(f"  [error] {rel}: {exc}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2))
    log(f"\nSaved full inspection to {out_path} in {round(time.monotonic() - t0, 2)}s")


if __name__ == "__main__":
    main()
