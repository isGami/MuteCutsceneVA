# MuteCutsceneVA Architecture

This document explains how [`scripts/build_mod.py`](../scripts/build_mod.py) and [`scripts/oot_formats.py`](../scripts/oot_formats.py) construct the RomFS mod without modifying the source RomFS or breaking gameplay audio or cutscene pacing.

## 1. Two-Layer Muting Pipeline

OOTNS2 cutscene dialogue spans both `Voice/` and `Sound/`. `build_mod.py` handles both layers in one pass:

### Layer 1: `Voice/Resource/*_EventFlowMsg-_Dm_*.bars.zs`
- **Default (`--voice-mode embed`):**
  1. Matches all `Voice/Resource/(?:<lang>_)?EventFlowMsg-_Dm_*.bars.zs` archives (`891` in full-game mode; `9` `*_LayoutMsg-_MessageInGame_00.bars.zs` prompt archives are skipped and preserved).
  2. Rewrites each archive via `embed_silent_bwavs_in_bars()`:
     - Sets `AMTA v5` flags at `+0x2c` to `0x02` (`is_stream=0`, `has_embedded_data=1`, `is_opus=0`).
     - Appends a minimal `136`-byte (`14`-sample) silent DSP-ADPCM `BWAV` (`make_silent_adpcm_bwav()`) aligned to `0x40` bytes, updates each entry's `asset_offset` and the `BARS` header `filesize`, and recompresses with the vanilla archive's Zstd dictionary ID (`ZsContext.compress_like()`).
  3. Why embed instead of overriding `Voice/Resource/Stream/`? Longshot's RomFS mod loader does not collapse `//` in OOTNS2's `Voice/Resource//Stream/<name>.bwav` opens (see [findings.md](findings.md#4-longshot-voiceresourcestream-behavior-pr-1346)). Setting `is_stream=0` causes the game to play the embedded silent DSP-ADPCM wave directly from the `.bars.zs` and never open `Voice/Resource//Stream/`.
  4. **RESTBL safety:** Embedded `.bars.zs` files grow slightly (`~10–15%` decompressed). Longshot automatically merges and raises `ResourceSizeTable` entries at boot for mod files larger than vanilla (see [Longshot Mods Spec](longshot/mods.md)).

- **Stream mode (`--voice-mode stream`):**
  - Provided for environments that support single-slash mod matching on `Voice/Resource//Stream/`.
  - Uses `scripts/opus_silence.py` (`libopus` via `ctypes`) to generate and self-verify a `3`-byte digitally silent CELT frame (`f8 ff fe`, `final_range = 0x01000000`, `960` zero samples), rebuilds mono and stereo Opus `BWAV`s via `silence_opus_bwav()` (keeping exact packet count and `samples_full`), silences any DSP-ADPCM (`codec=1`) streams via `silence_bwav()`, skips damaged non-BWAV vanilla files, and updates the metadata-only `BWAV` `crc32` fields inside `Voice/Resource/*.bars.zs` via `patch_bars_bwav_crcs()`.

### Layer 2: `Sound/Resource/Stream/NV_*_Dm_*_SpNN.bwav` + `Sound/Resource/NV_*.bars.zs`
- Matches only `Sound/Resource/Stream/` files fitting `^NV_.+_Dm_.+_Sp\d+\.bwav$` (`672` files across all languages and non-prefixed characters such as `NV_Mido`, `NV_KingZora`, `NV_Talon`).
- Zeroes each `.bwav` stream's DSP-ADPCM payload and initial/loop ADPCM context (`0x3C..0x48`) via `silence_bwav()` and recomputes its `BWAV` `crc32`, keeping exact duration and exact byte length (`<=` vanilla).
- Computes `zlib.crc32(stream_stem)` for each silenced stream and calls `patch_bars_bwavs()` on `Sound/Resource/NV_*.bars.zs` to silence only the matching `is_prefetch` BWAV sub-entries inside those mixed archives (`67` archives patched in full-game mode), leaving all non-`Dm_` NPC grunts/reactions intact.

## 2. Safety Guards & Reproducibility

- `ensure_safe_target()` refuses to run if `--out` or `--sync-to` equals, contains, or lies inside the `--romfs` tree.
- Staged builds are cleaned and written to `dist/MuteCutsceneVA/romfs/` first before optional copying to `--sync-to`.
- Every run emits `reports/build_audit.txt` and `reports/build_audit.json` recording:
  - Build mode, cutscene filters, and elapsed time.
  - Counts and per-file byte sizes of patched `Voice/` `.bars.zs` archives, muted `Sound/` `NV_` streams, and patched `Sound/` `.bars.zs` archives (including preserved entry counts per archive).
  - Preserved `Voice/` and `Sound/NV_*` lists.

## 3. Repository Layout

- `scripts/oot_formats.py`: Pure-Python parsers and writers for `ZsDic`, `SARC`, `BARS`, `AMTA`, and `BWAV` (DSP-ADPCM & Nintendo Opus).
- `scripts/opus_silence.py`: `ctypes` wrapper around system `libopus` for generating and verifying digitally silent Opus frames (`--voice-mode stream`).
- `scripts/build_mod.py`: Main reproducible mod builder (`Stage 1` prototype filter via `--cutscene`, `Stage 2` full-game build, and audit emitter).
- `scripts/01_inventory.py` & `scripts/02_inspect_bars.py`: Read-only RomFS inventory and `.bars.zs` inspection utilities.
- `tests/`: Self-contained unit tests for binary format helpers and safety guards.
- `docs/`: Architecture, reverse-engineering findings, troubleshooting, and Longshot modding reference docs.
