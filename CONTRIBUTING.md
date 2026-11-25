# Contributing to MuteCutsceneVA

Thank you for your interest in contributing!

## Development Setup
1. Install Python `3.12+` and [uv](https://docs.astral.sh/uv/).
2. Run the self-contained unit test suite (requires no game files):
   ```bash
   uv run python -m unittest discover -s tests -v
   ```

## Repository Rules
1. **Never commit game assets or copyrighted files.** Do not commit anything from a game RomFS, `dist/`, or `reports/` (all of which are gitignored).
2. **Never modify a source RomFS.** All scripts must treat `--romfs` / `OOT_ROMFS` strictly as read-only.
3. **Keep paths system-agnostic.** Never hardcode user-specific directories, drive letters, or local paths in scripts, tests, or documentation.
4. **Update documentation with code changes.** If you change CLI flags, file formats, or build behavior, update `README.md` and the relevant pages in `docs/`.

## Submitting Changes
- Keep commits focused and descriptive.
- Ensure `uv run python -m unittest discover -s tests -v` passes before opening a pull request.
- For changes affecting binary audio formats (`scripts/oot_formats.py`) or build output (`scripts/build_mod.py`), describe how the generated mod was verified in Longshot.
