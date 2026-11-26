# OOTNS2 Audio & Cutscene Voice Architecture

Verified reverse-engineering findings from *The Legend of Zelda: Ocarina of Time (Switch 2)* (`0400486024DEC000`, Ver. 1.1.1 / `v131072`) RomFS and Longshot testing.

## 1. RomFS Audio Layout

Audio and cutscene trigger data live across four top-level directories:

| Directory | Contents | Count (Ver. 1.1.1) |
|---|---|---|
| `Voice/Resource/` | Cutscene & prompt `.bars.zs` metadata archives (`9` languages × `100`) | `900` |
| `Voice/Resource/Stream/` | Streamed voice `.bwav` files (mostly Nintendo Opus; some stereo Opus & DSP-ADPCM) | `7,726` |
| `Sound/Resource/` | Character/SFX/BGM `.bars.zs` archives (including `224` `NV_*.bars.zs`) | `1,152` |
| `Sound/Resource/Stream/` | Streamed SFX, BGM, `Vo_*` player grunts, and `NV_*` NPC clips | `18,652` |
| `SLink2/` | `slink2.Product.111.bslnk.zs` actor/event sound key mappings | `1` |
| `Event/EventFlow/` | `.bfevfl.zs` cutscene and NPC flowcharts | `914` |

> [!NOTE]
> `Voice/` is present in a complete RomFS unpacked from the `.nup`. Incomplete dumps missing `Voice/` cannot be used to build this mod.

## 2. Cutscene Voice vs. Gameplay Audio Separation

Cutscene spoken dialogue lives in two places, cleanly separated from classic gameplay sounds:

1. **`Voice/Resource/` (`891` cutscene archives + `7,366` cutscene streams):**
   - Cutscene voice archives follow `<lang>_EventFlowMsg-_Dm_<Scene>.bars.zs` (across `9` language prefixes: `EUde`, `EUes`, `EUfr`, `EUit`, `JPja`, `USen`, `USes`, `USfr`, `USpt`) or unprefixed `EventFlowMsg-_Dm_<Scene>.bars.zs`.
   - `Voice/Resource/Stream/` contains `7,366` cutscene dialogue lines (`*EventFlowMsg-_Dm_*.bwav`).
   - **Preserved in `Voice/`:** `9` `*_LayoutMsg-_MessageInGame_00.bars.zs` archives and `360` `*LayoutMsg-_MessageInGame_00--*.bwav` gameplay prompt files (Navi alerts, low-health/combat cues, etc.) do **not** match `_Dm_` and are left untouched.

2. **`Sound/Resource/NV_*` (`672` cutscene clips mixed with `8,250` NPC reactions):**
   - `Sound/Resource/Stream/NV_*` (`8,922` files) and `Sound/Resource/NV_*.bars.zs` (`224` archives) mix **cutscene spoken clips** and **non-cutscene NPC grunts/reactions** (`Greeting00`, `Happy00`, `Angry00`, etc.) in the same directory and inside the same `.bars.zs` archives.
   - Every cutscene clip in `NV_*` (`672/672`) matches `NV_<prefix>_Dm_<scene>_<id>_Sp<nn>.bwav` (across `8` language-prefixed `NV_<lang>_Custom_<Character>` sets plus non-language-prefixed characters such as `NV_Mido`, `NV_KingZora`, and `NV_Talon`).
   - **Preserved in `Sound/`:** All `8,250` non-`Dm_` `NV_*` NPC reactions, all `573` `Vo_*` Link gameplay grunts/efforts, all `169` `Event_Dm_*.bars.zs` / `167` `Dm_*.bwav` cutscene BGM/SFX mixes, and all `SE_*` SFX.

### MSBT Reaction Tags & Cutscene Grunts
Inspection of `Mals/USen.Product.110.sarc.zs` (`EventFlowMsg/Dm_*.msbt`, UTF-8 `MsgStdBn`) explains how cutscenes trigger `NV_*` clips:
- **Fully voiced `_b` text boxes (`757` in `USen`):** Contain **no** `0x0E` reaction/voice tags. Any sighs, gasps, or laughs in those scenes are recorded directly inside the single `Voice/Resource/Stream/*_b.bwav` Opus stream alongside spoken dialogue.
- **Classic-style tagged cutscene text boxes (`484` in `USen`):** Embed `0x0E` group-4 tags inside the message text:
  - Generic reaction tags (`TalkPositive_01`, `Think_00`, `LaughS_01`, `Sad_00`, etc.) trigger the preserved non-`Dm_` `NV_*` character clips.
  - `82` `USen` tags carry `Dm_<scene>_<line>_Sp_<nn>` identifiers that trigger the `NV_*_Dm_*_SpNN.bwav` clips (`102` language-neutral clip IDs total across all characters/scenes, `0.3`–`5.8 s` long; `77` exist in all `8` languages with separate per-language recordings and no shared audio, `14` are unprefixed for Mido/Talon/King Zora, and Impa/Sheik have partial language coverage). Because these `Sp` clips are separate `.bwav` streams, selected non-verbal ones can be kept unmuted via `data/keep_sp_clips.txt` without audio editing.

## 3. Binary Format Specifications

### Zstd Dictionaries (`Pack/ZsDic.pack.zs`)
- `.bars.zs`, `.bfevfl.zs`, `.bslnk.zs`, and `.rsizetable.zs` files use Zstd compression, with shared dictionaries stored in `Pack/ZsDic.pack.zs` (a plain Zstd-compressed `SARC` archive).
- `ZsContext` in `scripts/oot_formats.py` loads all dictionaries by `dict_id` and recompresses patched archives using the same `dict_id` as the vanilla file.

### `BARS` Archives & `AMTA v5` (`0x0500`)
- Header (`0x10` bytes): `BARS`, `u32 filesize`, `u16 BOM`, `u16 version` (`0x0102`), `u32 count`, followed by `count` sorted `u32` CRC32 name hashes and `count` `(u32 amta_offset, i32 asset_offset)` pairs.
- Every BARS entry hash matches `zlib.crc32(stream_stem.encode("utf-8")) & 0xFFFFFFFF` (`100%` 1-to-1 across all `672` `NV_` cutscene streams and `Voice/` streams).
- **`AMTA v5` (`0x0500`) layout:** Unlike older `AMTA` versions, v5 omits the separate `STRG` chunk; instead:
  - `+0x00`: `AMTA`, `+0x04`: BOM (`ff fe`), `+0x06`: `u16 version` (`0x0500`), `+0x08`: `u32 filesize`.
  - `+0x24`: relative offset from `+0x24` to the null-terminated asset name string.
  - `+0x28`: `u32 crc32` of the asset name.
  - `+0x2c`: `u32 flags`:
    - Bit `0` (`0x01`): `is_stream` (opens external `.bwav` from `Stream/`)
    - Bit `1` (`0x02`): `has_embedded_data` (full embedded Wave when `is_stream=0`; prefetch when `is_stream=1`)
    - Bit `2` (`0x04`): `is_looped`
    - Bit `4` (`0x10`): `is_opus`
  - `+0x38..+0x47`: four `f32` loudness/RMS metrics (no gain/volume float is present in `AMTA v5`, so track-volume zeroing is not viable).
  - `+0x48..`: lip-sync / amplitude envelope `(f32 amp, u32 sample_pos)` pairs at `4800`-sample (`100 ms`) steps.
- **Observed flag values:**
  - `0x11` (`Voice/Resource/*.bars.zs`): streamed Opus, metadata-only (`is_prefetch=False`, `samples_here=0`).
  - `0x03` (`Sound/Resource/NV_*.bars.zs`): streamed DSP-ADPCM with embedded `14,336`-sample prefetch (`is_prefetch=True`).
  - `0x02` (`511` vanilla `Sound/Resource/*.bars.zs` entries such as `Animal_Cow.bars.zs`): embedded non-streamed DSP-ADPCM Wave (`is_prefetch=False`, `samples_here == samples_full`).

### `BWAV` Audio (`BWAV` magic)
- `0x10`-byte header (`BWAV`, BOM, `u16 version`, `u32 crc32`, `u16 is_prefetch`, `u16 num_channels`) + `ChannelInfo[num_channels]` (`0x4C` bytes each) + `0x40`-aligned channel payloads.
- Header `crc32` at `+0x08` is `zlib.crc32(bwav[first_data_offset:]) & 0xFFFFFFFF`.
- **DSP-ADPCM (`codec = 1`):** Used by all `672` `NV_*` cutscene streams and `48` `Voice/` streams. Zeroing the ADPCM payload (`8` bytes per `14` samples) and initial/loop ADPCM context (`ChannelInfo +0x3C..+0x48`) yields digital silence at identical byte size.
- **Nintendo Opus (`codec = 2`):** Used by `7,677` `Voice/` streams (`7,621` mono, `56` stereo). Each channel contains an `OPUS_INFO_CHUNK` (`0x80000001`) and `OPUS_DATA_CHUNK` (`0x80000004`) of `[u32be packet_size][u32be final_range][CELT packet]` frames (`960` samples, TOC `0xF8`, pre-skip `120`). Note: one vanilla file (`EUde_EventFlowMsg-_Dm_TimeTemple_0050--Dm_TimeTemple_0050_Text_001_b.bwav`) has an all-zero header in base game data and is skipped in stream mode.

## 4. Longshot `Voice/Resource//Stream/` Behavior (PR #1346)

OOTNS2 opens streamed `Voice/` assets with a doubled path separator (`content://Voice/Resource//Stream/<name>.bwav`). As documented in [Longshot PR #1346](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/1346), Longshot's `mods.c` `lower_slash` helper preserves `//` in its lookup key (`voice/resource//stream/...`), so RomFS mod files placed under `mods/<ModName>/romfs/Voice/Resource/Stream/*.bwav` never match in Longshot.

Because `Voice/Resource/*.bars.zs` uses a single slash and loads reliably from mods, converting cutscene entries in `Voice/Resource/*_EventFlowMsg-_Dm_*.bars.zs` from `flags = 0x11` (external Opus stream) to `flags = 0x02` (embedded `14`-sample silent DSP-ADPCM `BWAV`) prevents the engine from opening `Voice/Resource//Stream/` at all. Longshot automatically merges and raises `ResourceSizeTable` (`RESTBL`) entries at boot; for Switch 2 emulators and `.nup` patchers, `build_mod.py` also patches `RESTBL` directly.

## 5. `ResourceSizeTable` (`RESTBL v1`) Format, Margins & Emulator Compatibility

Inspected from vanilla `System/Resource/ResourceSizeTable.Product.111.Nin_Ounce_NVN2.rsizetable.zs` (Ver. 1.1.1):

- **Zstd Frame Parameters:** Plain Zstd (`dict_id = 0`, `content_size = 1,761,406`, `window_size = 1,761,406`, `has_checksum = False`; `1,395,286` bytes compressed). `ZsContext.compress_like()` reproduces all frame parameters identically.
- **Binary Layout (`RESTBL v1`):**
  - `22`-byte header (`<6sIIII`): `magic = b"RESTBL"`, `version = 1`, `str_block_size = 160`, `crc_count = 219,968`, `name_count = 10` (`22 + 219,968*8 + 10*164 = 1,761,406` bytes exact).
  - **CRC Table:** `219,968` sorted, unique `(u32 crc32, u32 size)` pairs keyed by normalized RomFS relative path with `.zs` stripped (e.g., `Voice/Resource/USen_EventFlowMsg-_Dm_Kokiri_0050.bars`).
  - **Name Table:** `10` sorted `(160-byte NUL-padded UTF-8 path, u32 size)` entries (`5` CRC32 collision pairs).
- **Vanilla Margins & Why All `891` Voice `.bars.zs` Exceed Budget:**
  - OOTNS2's `RESTBL` stores `decompressed_file_size + resource_class_overhead` rather than raw file size alone. For `.bars` archives, this overhead is `+584..+608` bytes (`+588` on `882/891` cutscene `Voice/` archives and `+608` on `9/891`; pure `flags = 0x02` archives such as `Npc.bars.zs` also carry `+584..+608` bytes).
  - Because that `~588`-byte margin is runtime `BARS` resource-object heap overhead rather than spare file capacity, **all `891/891`** embedded `Voice/Resource/*.bars.zs` archives exceed their vanilla RESTBL budget (`mod_raw > vanilla_raw` by `+44` to `+2,476` bytes; `369/891` even exceed `vanilla_restbl` in raw file size alone by up to `+1,888` bytes). Longshot's own `modcache/rsizetable.zs` likewise raised all `891` entries.
  - `Sound/Resource/NV_*.bars.zs` (`67` patched): `0/67` grow (`mod_raw == vanilla_raw`).
  - `Sound/Resource/Stream/NV_*.bwav` (`672` silenced): streamed `.bwav` files are not tracked in `RESTBL` (and remain `==` vanilla size).
- **Why Zero-Conflict (No `RESTBL`) Is Not Safely Achievable in `--voice-mode embed`:**
  - Even if multi-entry archives shared a single `136`-byte silent `BWAV`, `126/891` cutscene archives contain only `1` entry and still grow by `+44` bytes (`136`-byte embedded ADPCM `BWAV`—matching Nintendo's own `136`-byte `DummyBlank` in `Npc.bars.zs`—versus the `92`-byte vanilla stream stub).
  - All `35,395` vanilla `BARS` `asset_offset`s and all `6,719` vanilla embedded `BWAV` `data_offset`s are `0x40`-aligned, and trimming `AMTA v5` is unsafe (`+0x48` is non-uniform across archives, and `9` single-entry archives have `0`-byte alignment gaps).
- **Patching Formula & Verification:**
  - `compute_restbl_required_size()` raises only entries with `mod_raw > vanilla_raw` to `align32(vanilla_restbl + (mod_raw - vanilla_raw) + 128)`, preserving each file's vanilla heap overhead plus a `128`-byte cushion aligned up to `32` bytes, and never shrinks any entry (`0` shrunk, `891` CRC entries raised, `10` name-table entries untouched).
  - Verified in Longshot (`logs/longshot_log.txt`): `mods: resource size table: 1 tables of mods merged (891 entries), 0 entries raised for mod files (0 added files estimated)`.
