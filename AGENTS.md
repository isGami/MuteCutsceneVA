# AGENTS.md — MuteCutsceneVA (OOTNS2 / Longshot)

## Project Goal
Provide a reproducible RomFS mod builder for *The Legend of Zelda: Ocarina of Time (Switch 2)* (`0400486024DEC000`, Ver. 1.1.1 / `v131072`) running on **Longshot** (MagicBeanDev), Switch 2 emulators (e.g., Lichii), and `.nup` patchers that mutes voice-acted cutscene dialogue across all voice languages while keeping classic gameplay sounds, Link `Vo_*` grunts, `LayoutMsg` gameplay prompts, and non-cutscene NPC reactions intact.

## Repository Layout
- `scripts/oot_formats.py`: Pure-Python parsers and writers for `ZsDic`, `SARC`, `BARS`, `AMTA v5`, `BWAV` (DSP-ADPCM & Nintendo Opus), and `RESTBL v1`.
- `scripts/opus_silence.py`: `libopus` (`ctypes`) silent-frame generator/verifier for `--voice-mode stream`.
- `scripts/build_mod.py`: Main mod builder (reads `--romfs` / `OOT_ROMFS` read-only, stages to `dist/MuteCutsceneVA/romfs/`, patches `ResourceSizeTable` unless `--no-restbl` is passed, emits `reports/build_audit.{txt,json}`, optionally syncs to `--sync-to`).
- `scripts/01_inventory.py` & `scripts/02_inspect_bars.py`: Read-only RomFS inventory and `.bars.zs` inspection tools.
- `tests/`: Self-contained unit tests (`uv run python -m unittest discover -s tests`).
- `docs/`: Technical docs (`architecture.md`, `findings.md`, `troubleshooting.md`, and `longshot/`).

## Critical Safety Rules
1. **Treat the source RomFS (`--romfs` / `OOT_ROMFS`) strictly as read-only.** Never create, edit, or delete files inside it; `scripts/build_mod.py` aborts if `--out` or `--sync-to` overlaps `--romfs`.
2. **Never commit game assets or build outputs** (`dist/`, `reports/` are gitignored).
3. Keep all mod generation reproducible via `scripts/`.

## Environment & Tooling (`uv`)
- Package manager: **`uv`** (`pyproject.toml`, `uv.lock`, Python `>=3.12`, `zstandard>=0.25.0`).
- Run Python scripts and tests via `uv run ...`.

## Documentation & Testing Standards
- Keep `README.md`, `docs/architecture.md`, `docs/findings.md`, `docs/troubleshooting.md`, and `AGENTS.md` in sync with CLI flags, tested behavior, and repository layout.
- Run `uv run python -m unittest discover -s tests -v` after modifying `scripts/`.
