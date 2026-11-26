# Troubleshooting

## 1. `Incomplete source RomFS: missing .../Voice/Resource/Stream`
- Your `--romfs` (or `OOT_ROMFS`) directory does not contain `Voice/`.
- Make sure you point `--romfs` at a complete unpacked OOTNS2 RomFS (such as the game folder's `romfs/` unpacked from the `.nup`), not a partial dump.

## 2. Cutscene voices still play in Longshot
Check `logs/longshot_log.txt` inside your Longshot game folder:
1. **Confirm the mod was discovered and enabled:**
   Look for:
   ```text
   mods: MuteCutsceneVA: 1630 RomFS files (1630 replace the game's, 0 added)
   mods: resource size table: 1 tables of mods merged (891 entries), 0 entries raised for mod files (0 added files estimated), served from modcache, ...
   ```
   - If there are no `mods:` lines, verify the folder is placed at `<longshot_game_dir>/mods/MuteCutsceneVA/romfs/...` (with `Voice/`, `Sound/`, and `System/` directly inside `romfs/`).
   - If the log says `off (the Mods tab)` or `starts with _`, enable it in Longshot's `Esc` / `F1` -> **Mods** menu (and ensure the folder name does not start with `_` or `-`), then restart the game.
2. **Confirm you built with `--voice-mode embed` (the default):**
   - `Voice/Resource/*_EventFlowMsg-_Dm_*.bars.zs` must appear in `mods/MuteCutsceneVA/romfs/Voice/Resource/`.
   - Overriding only `Voice/Resource/Stream/*.bwav` (`--voice-mode stream`) will not take effect in Longshot builds affected by the `Voice/Resource//Stream/` doubled-separator lookup issue (see [findings.md](findings.md#4-longshot-voiceresourcestream-behavior-pr-1346)).

## 3. Stale files after switching between `--cutscene` and full builds
- `scripts/build_mod.py` cleans `dist/MuteCutsceneVA/romfs` (and `--sync-to` if specified) on every run. If you copied files manually to Longshot's `mods/` folder, delete the old `mods/MuteCutsceneVA/romfs` folder before copying a new build.

## 4. `uv` or `zstandard` issues
- Run all scripts with `uv run scripts/<script>.py ...` so `pyproject.toml` and `uv.lock` resolve `zstandard>=0.25.0` automatically.
- Only `--voice-mode stream` requires system `libopus` (`libopus.so.0` / `opus.dll`). The default `--voice-mode embed` uses pure Python + `zstandard`.

## 5. Mod load priority & `ResourceSizeTable` stacking
- In Longshot, mods lower in the `Esc` / `F1` -> **Mods** list override earlier ones, and Longshot merges each mod's `ResourceSizeTable` automatically. If another mod replaces the same `.bars.zs` archives, place `MuteCutsceneVA` below it (or check `logs/longshot_log.txt` to see which mod won).
- On Switch 2 emulators (e.g., Lichii) or `.nup` patchers that do not auto-merge `ResourceSizeTable`, if another mod also ships `System/Resource/ResourceSizeTable.*.rsizetable.zs`, merge the RESTBL tables externally (or build `MuteCutsceneVA` with `--no-restbl` and merge).

## 6. `[warn] Unmatched keep-sp pattern(s)` when building
- During a full build (without `--cutscene`), `build_mod.py` warns if any active pattern in `data/keep_sp_clips.txt` (or `--keep-sp`) matched zero `NV_*_Dm_*_SpNN.bwav` files.
- Check for typos, forgotten `_Sp00` suffixes, or non-`Dm_` reaction names (non-`Dm_` `NV_*` clips are already preserved and should not be listed in `keep_sp_clips.txt`). Note that matching is case-sensitive (`fnmatchcase`).
