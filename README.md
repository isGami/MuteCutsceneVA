# MuteCutsceneVA

A RomFS mod builder for *The Legend of Zelda: Ocarina of Time (Switch 2)* (`0400486024DEC000`, Ver. 1.1.1) on **[Longshot](https://git.magicbean.dev/MagicBeanDev/Longshot)** that mutes voice-acted cutscene dialogue across all 9 voice languages while keeping classic gameplay audio intact.

## What Is Muted vs. Preserved
- **Muted:**
  - All cutscene dialogue archives (`Voice/Resource/*_EventFlowMsg-_Dm_*.bars.zs`, `891` archives across all languages).
  - All cutscene voice clips mixed into character archives (`Sound/Resource/Stream/NV_*_Dm_*_SpNN.bwav`, `672` clips, and their matching prefetch entries inside `67` `Sound/Resource/NV_*.bars.zs` archives).
- **Preserved:**
  - Link's gameplay grunts and efforts (`Vo_*`).
  - Non-cutscene NPC reactions and grunts (non-`Dm_` `NV_*` clips).
  - In-game Navi / gameplay voice prompts (`Voice/Resource/*_LayoutMsg-_MessageInGame_00.bars.zs`).
  - All cutscene BGM, ambience, and sound effects (`Event_Dm_*`, `SE_*`).

## Requirements
- Python `3.12+` and **[uv](https://docs.astral.sh/uv/)**
- A complete, unpacked OOTNS2 RomFS directory (must include `Voice/` and `Pack/ZsDic.pack.zs`)

## Building

Clone the repository and run `build_mod.py` pointing `--romfs` (or the `OOT_ROMFS` environment variable) at your read-only unpacked RomFS:

```bash
uv run scripts/build_mod.py --romfs /path/to/ootns2/romfs
```

This stages the mod to `dist/MuteCutsceneVA/romfs/` and writes audit reports to `reports/build_audit.txt` and `reports/build_audit.json`.

### Optional Flags
- **Sync directly into Longshot's `mods/` folder:**
  ```bash
  uv run scripts/build_mod.py \
    --romfs /path/to/ootns2/romfs \
    --sync-to /path/to/longshot_game_dir/mods/MuteCutsceneVA/romfs
  ```
- **Test a single cutscene first** (comma-separated substring filter):
  ```bash
  uv run scripts/build_mod.py \
    --romfs /path/to/ootns2/romfs \
    --cutscene Dm_Kokiri_0050,Dm_Kokiri_0060
  ```

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
   ```
   Longshot merges `ResourceSizeTable` (`RESTBL`) entries automatically at boot, so no manual RESTBL patching is required.

## Documentation
- [Architecture & Mod Design](docs/architecture.md) — how `Voice/` embedding, `NV_` prefetch silencing, `BARS`/`AMTA v5`, and `RESTBL` work.
- [Reverse-Engineering Findings](docs/findings.md) — verified OOTNS2 audio layout, binary formats, and Longshot `Voice/Resource//Stream/` notes.
- [Troubleshooting](docs/troubleshooting.md) — common setup, dump completeness, and Longshot log checks.
- [Contributing](CONTRIBUTING.md) — development setup, running unit tests, and PR guidelines.

## License
Released under the [MIT License](LICENSE).
