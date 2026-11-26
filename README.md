# MuteCutsceneVA

A RomFS mod builder for *The Legend of Zelda: Ocarina of Time (Switch 2)* (`0400486024DEC000`, Ver. 1.1.1) on **[Longshot](https://git.magicbean.dev/MagicBeanDev/Longshot)**, Switch 2 emulators (e.g., Lichii), and `.nup` patchers that mutes voice-acted cutscene dialogue across all 9 voice languages while keeping classic gameplay audio intact.

## What Is Muted vs. Preserved
- **Muted:**
  - All cutscene dialogue archives (`Voice/Resource/*_EventFlowMsg-_Dm_*.bars.zs`, `891` archives across all languages).
  - Any `Sound/Resource/Stream/NV_*_Dm_*_SpNN.bwav` cutscene clips not matched by `--keep-sp` (`0` muted with the default `data/keep_sp_clips.txt`, where all `102` clip IDs are enabled; `672` clips across `67` `Sound/Resource/NV_*.bars.zs` archives when built with `--no-keep-sp`).
- **Patched for Emulator / `.nup` Compatibility:**
  - `System/Resource/ResourceSizeTable.Product.111.Nin_Ounce_NVN2.rsizetable.zs` (`891` grown `Voice/` `.bars.zs` entries raised; all other `219,077` CRC entries and `10` name-table entries preserved verbatim).
- **Preserved:**
  - Link's gameplay grunts and efforts (`Vo_*`).
  - Non-cutscene NPC reactions and grunts (non-`Dm_` `NV_*` clips).
  - All `672` cutscene `NV_*_Dm_*_SpNN` clips matched by `data/keep_sp_clips.txt` (all `102` clip IDs enabled by default; override with `--keep-sp` or `--no-keep-sp`).
  - In-game Navi / gameplay voice prompts (`Voice/Resource/*_LayoutMsg-_MessageInGame_00.bars.zs`).
  - All cutscene BGM, ambience, and sound effects (`Event_Dm_*`, `SE_*`).

## Requirements
- Python `3.12+` and **[uv](https://docs.astral.sh/uv/)**
- A complete, unpacked OOTNS2 RomFS directory (must include `Voice/`, `Pack/ZsDic.pack.zs`, and `System/Resource/ResourceSizeTable.*.rsizetable.zs`)

## Building

Clone the repository and run `build_mod.py` pointing `--romfs` (or the `OOT_ROMFS` environment variable) at your read-only unpacked RomFS:

```bash
uv run scripts/build_mod.py --romfs /path/to/ootns2/romfs
```

This stages `892` files by default (`891` `Voice/` `.bars.zs` and `1` patched `System/Resource/ResourceSizeTable.Product.111.Nin_Ounce_NVN2.rsizetable.zs`, because all `102` `Sp` clip IDs in `data/keep_sp_clips.txt` are enabled by default) — or `1,631` files with `--no-keep-sp` (adding `67` `Sound/` `NV_*.bars.zs` and `672` `Sound/` `NV_*.bwav`) — to `dist/MuteCutsceneVA/romfs/`, and writes audit reports to `reports/build_audit.txt` and `reports/build_audit.json`.

### Optional Flags
- **Keep selected cutscene grunts/noises (`--keep-sp` / `--no-keep-sp`):**
  `build_mod.py` reads `data/keep_sp_clips.txt` by default (pass `--keep-sp PATH` for a custom file, or `--no-keep-sp` to ignore the allowlist and mute all `Sp` clips).
- **Sync directly into Longshot's `mods/` folder:**
  ```bash
  uv run scripts/build_mod.py \
    --romfs /path/to/ootns2/romfs \
    --sync-to /path/to/longshot_game_dir/mods/MuteCutsceneVA/romfs
  ```
- **Omit `ResourceSizeTable` (Longshot-only or external RESTBL merger):**
  ```bash
  uv run scripts/build_mod.py \
    --romfs /path/to/ootns2/romfs \
    --no-restbl
  ```
- **Test a single cutscene first** (comma-separated substring filter):
  ```bash
  uv run scripts/build_mod.py \
    --romfs /path/to/ootns2/romfs \
    --cutscene Dm_Kokiri_0050,Dm_Kokiri_0060
  ```

## Keeping Cutscene Grunts (`NV_*_Dm_*_SpNN`)
Classic-style cutscenes use `Sound/Resource/Stream/NV_*_Dm_*_SpNN.bwav` clips (`102` language-neutral clip IDs: a mix of non-verbal grunts/laughs/pants/snores and short spoken lines). All `102` clip IDs are enabled by default in [`data/keep_sp_clips.txt`](data/keep_sp_clips.txt); you can review them by ear and comment out any you want muted (or pass `--no-keep-sp` to mute all `672` clips):

1. Generate the WAV review page from your read-only RomFS:
   ```bash
   uv run scripts/03_review_sp_clips.py --romfs /path/to/ootns2/romfs
   ```
2. Open `reports/sp_review/index.html` in a browser to listen to each clip alongside its matching message text.
3. Keep or comment out clip IDs (or use globs like `Talon_*`) in [`data/keep_sp_clips.txt`](data/keep_sp_clips.txt).
4. Rebuild with `uv run scripts/build_mod.py --romfs /path/to/ootns2/romfs` (or pass `--keep-sp <file>` / `--no-keep-sp`). Kept clips are omitted from staging and left untouched inside `Sound/Resource/NV_*.bars.zs`.

## Installing in Longshot
1. Copy `dist/MuteCutsceneVA/` into your Longshot game folder's `mods/` directory so the path is:
   ```text
   <longshot_game_dir>/mods/MuteCutsceneVA/romfs/...
   ```
   (Or pass `--sync-to <longshot_game_dir>/mods/MuteCutsceneVA/romfs` when building.)
2. Launch the game. You can confirm `MuteCutsceneVA` is enabled in Longshot's `Esc` / `F1` -> **Mods** menu.
3. Check `logs/longshot_log.txt` in your game folder for:
   ```text
   mods: MuteCutsceneVA: 1630 RomFS files (1630 replace the game's, 0 added)
   mods: resource size table: 1 tables of mods merged (891 entries), 0 entries raised for mod files (0 added files estimated), served from modcache, ...
   ```
   Longshot automatically merges our patched `ResourceSizeTable` with any other enabled mods (or you can pass `--no-restbl` for a Longshot-only build).

## Installing in Switch 2 Emulators (e.g., Lichii) / `.nup` Patchers
1. Use the staged `dist/MuteCutsceneVA/romfs/` folder directly as your emulator RomFS mod or `.nup` patcher input—it already includes the patched `System/Resource/ResourceSizeTable.Product.111.Nin_Ounce_NVN2.rsizetable.zs`.
2. If you stack multiple RomFS mods that also modify `ResourceSizeTable`, build with `--no-restbl` and merge `ResourceSizeTable` externally (or merge the tables with your mod manager).

## Documentation
- [Architecture & Mod Design](docs/architecture.md) — how `Voice/` embedding, `NV_` prefetch silencing, `BARS`/`AMTA v5`, and `RESTBL` work.
- [Reverse-Engineering Findings](docs/findings.md) — verified OOTNS2 audio layout, binary formats, and Longshot `Voice/Resource//Stream/` notes.
- [Troubleshooting](docs/troubleshooting.md) — common setup, dump completeness, and Longshot log checks.
- [Contributing](CONTRIBUTING.md) — development setup, running unit tests, and PR guidelines.

# AI Disclosure
AI was used to reverse-engineer the ROM and implementation of the scripts. I am responsible for the discovery, planning, decisions, and quality assurance.
Models used: Opus 5.5 & Gemini 4 Neon.

## License
Released under the [MIT License](LICENSE).
