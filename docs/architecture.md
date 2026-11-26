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
  4. **RESTBL safety:** Embedded `.bars.zs` files grow slightly (`+44` to `+2,476` bytes decompressed). By default, `build_mod.py` patches `System/Resource/ResourceSizeTable.*.rsizetable.zs` (see Layer 3 below) so both Switch 2 emulators / `.nup` patchers and Longshot load cleanly.

- **Stream mode (`--voice-mode stream`):**
  - Provided for environments that support single-slash mod matching on `Voice/Resource//Stream/`.
  - Uses `scripts/opus_silence.py` (`libopus` via `ctypes`) to generate and self-verify a `3`-byte digitally silent CELT frame (`f8 ff fe`, `final_range = 0x01000000`, `960` zero samples), rebuilds mono and stereo Opus `BWAV`s via `silence_opus_bwav()` (keeping exact packet count and `samples_full`), silences any DSP-ADPCM (`codec=1`) streams via `silence_bwav()`, skips damaged non-BWAV vanilla files, and updates the metadata-only `BWAV` `crc32` fields inside `Voice/Resource/*.bars.zs` via `patch_bars_bwav_crcs()`.

### Layer 2: `Sound/Resource/Stream/NV_*_Dm_*_SpNN.bwav` + `Sound/Resource/NV_*.bars.zs`
- Matches only `Sound/Resource/Stream/` files fitting `^NV_.+_Dm_.+_Sp\d+\.bwav$` (`672` files across all languages and non-prefixed characters such as `NV_Mido`, `NV_KingZora`, `NV_Talon`).
- **Allowlist filtering (`--keep-sp`, default `data/keep_sp_clips.txt`):**
  - Before silencing, `collect_streams()` checks each matching `Sp` file against patterns loaded by `load_keep_sp()` (`fnmatch.fnmatchcase` against either the language-neutral clip ID—e.g. `Saria_Dm_Kokiri_0060_0000_Sp00`—or the full filename stem for single-language exceptions).
  - Precedence: `--custom-chars` / `--all-custom` take priority over the allowlist; `--no-keep-sp` disables the allowlist completely.
  - Kept clips need no binary patching: they are simply omitted from `Sound/Resource/Stream/` staging and skipped by `patch_bars_bwavs()`, and `NV_*.bars.zs` archives with zero remaining muted entries are not staged at all. `RESTBL` is unaffected.
- Zeroes each remaining target `.bwav` stream's DSP-ADPCM payload and initial/loop ADPCM context (`0x3C..0x48`) via `silence_bwav()` and recomputes its `BWAV` `crc32`, keeping exact duration and exact byte length (`<=` vanilla).
- Computes `zlib.crc32(stream_stem)` for each silenced stream and calls `patch_bars_bwavs()` on `Sound/Resource/NV_*.bars.zs` to silence only the matching `is_prefetch` BWAV sub-entries inside those mixed archives (`0` archives patched with the default `data/keep_sp_clips.txt`, where all `102` `Sp` clip IDs are enabled; `67` archives patched with `--no-keep-sp`), leaving all non-`Dm_` NPC grunts/reactions and any allowlisted `Sp` clips intact.

### Layer 3: `System/Resource/ResourceSizeTable.*.rsizetable.zs` (`RESTBL`)
- Locates the highest-version vanilla `ResourceSizeTable.*.rsizetable.zs` in `--romfs` via `find_vanilla_restbl()`.
- Checks every modded file against its vanilla `RESTBL` entry (`name_entries` first, then `crc_entries` keyed by `restbl_key()`, which strips leading `/` and trailing `.zs`).
- For files that grow (`mod_raw > vanilla_raw` — all `891` `Voice/Resource/*.bars.zs` in default `--voice-mode embed`), `compute_restbl_required_size()` computes `align32(vanilla_restbl + (mod_raw - vanilla_raw) + 128)`.
- `patch_restbl()` raises only entries where `required_size > vanilla_restbl` (never shrinking any entry, preserving all other `219,077` CRC entries and `10` name-table entries verbatim), recompresses via `ZsContext.compress_like()`, writes the patched table to `System/Resource/`, and runs an inline round-trip verification check.
- When `0` entries need raising (e.g. `--voice-mode stream`), or when `--no-restbl` is passed for Longshot-only / external-merger builds, `System/Resource/` is omitted automatically.

## 2. Safety Guards & Reproducibility

- `ensure_safe_target()` refuses to run if `--out` or `--sync-to` equals, contains, or lies inside the `--romfs` tree.
- Staged builds are cleaned and written to `dist/MuteCutsceneVA/romfs/` first before optional copying to `--sync-to`.
- Every run emits `reports/build_audit.txt` and `reports/build_audit.json` recording:
  - Build mode, cutscene filters, and elapsed time.
  - Counts and per-file byte sizes of patched `Voice/` `.bars.zs` archives, muted `Sound/` `NV_` streams, and patched `Sound/` `.bars.zs` archives (including preserved entry counts per archive).
  - `RESTBL` check/patch counts and raised entry deltas.
  - Preserved `Voice/` and `Sound/NV_*` lists.

## 3. Repository Layout

- `data/keep_sp_clips.txt`: Default allowlist of `Sound/` `Sp` cutscene clip IDs/globs to keep unmuted (all `102` cutscene `Sp` clip IDs enabled by default).
- `scripts/oot_formats.py`: Pure-Python parsers and writers for `ZsDic`, `SARC`, `BARS`, `AMTA`, `BWAV` (DSP-ADPCM & Nintendo Opus), `MSBT`, DSP-ADPCM WAV export, and `RESTBL v1`.
- `scripts/opus_silence.py`: `ctypes` wrapper around system `libopus` for generating and verifying digitally silent Opus frames (`--voice-mode stream`).
- `scripts/build_mod.py`: Main reproducible mod builder (`Stage 1` prototype filter via `--cutscene`, `Stage 2` full-game build, allowlist support, and audit emitter).
- `scripts/01_inventory.py`, `scripts/02_inspect_bars.py`, & `scripts/03_review_sp_clips.py`: Read-only RomFS inventory, `.bars.zs` inspection, and `Sp` clip WAV/MSBT review tools.
- `tests/`: Self-contained unit tests for binary format helpers and safety guards.
- `docs/`: Architecture, reverse-engineering findings, troubleshooting, and Longshot modding reference docs.
