from __future__ import annotations
import io
import struct
import wave
import zlib
from pathlib import Path
from typing import Any

try:
    import zstandard as zstd
except ImportError:
    zstd = None


def detect_bom(data: bytes, offset: int) -> str:
    bom = data[offset : offset + 2]
    if bom == b"\xFF\xFE":
        return "<"
    if bom == b"\xFE\xFF":
        return ">"
    raise ValueError(f"Unknown BOM at {offset:#x}: {bom!r}")


def parse_sarc(data: bytes) -> dict[str, bytes]:
    if data[:4] != b"SARC":
        raise ValueError(f"Not a SARC archive (magic={data[:4]!r})")
    end = detect_bom(data, 6)
    hdr_size, = struct.unpack_from(end + "H", data, 0x04)
    data_offset, version = struct.unpack_from(end + "IH", data, 0x0C)
    if hdr_size != 0x14 or version != 0x100:
        raise ValueError(f"Unexpected SARC header: size={hdr_size:#x}, ver={version:#x}")

    sfat_off = hdr_size
    if data[sfat_off : sfat_off + 4] != b"SFAT":
        raise ValueError("Missing SFAT")
    sfat_hdr_size, node_count, _hash_mult = struct.unpack_from(end + "HHI", data, sfat_off + 4)
    nodes_off = sfat_off + sfat_hdr_size

    sfnt_off = nodes_off + node_count * 0x10
    if data[sfnt_off : sfnt_off + 4] != b"SFNT":
        raise ValueError("Missing SFNT")
    sfnt_hdr_size, = struct.unpack_from(end + "H", data, sfnt_off + 4)
    str_table_off = sfnt_off + sfnt_hdr_size

    entries: dict[str, bytes] = {}
    for i in range(node_count):
        off = nodes_off + i * 0x10
        name_hash, name_attr, start, end_off = struct.unpack_from(end + "IIII", data, off)
        if name_attr == 0:
            name = f"__hash_{name_hash:08x}"
        else:
            str_rel = (name_attr & 0x00FFFFFF) * 4
            s_start = str_table_off + str_rel
            s_end = data.index(b"\x00", s_start)
            name = data[s_start:s_end].decode("utf-8", errors="replace")
        entries[name] = data[data_offset + start : data_offset + end_off]
    return entries


class ZsContext:
    """Decompresses .zs files using dictionaries from Pack/ZsDic.pack.zs."""

    def __init__(self, romfs_dir: Path | None = None) -> None:
        self.dicts: dict[int, Any] = {}
        if romfs_dir is not None:
            zsdic_path = romfs_dir / "Pack" / "ZsDic.pack.zs"
            if zsdic_path.exists():
                self.load_zsdic(zsdic_path)

    def load_zsdic(self, zsdic_path: Path) -> None:
        if zstd is None:
            raise RuntimeError("zstandard is not installed. Run: uv add zstandard")
        raw = zstd.ZstdDecompressor().stream_reader(io.BytesIO(zsdic_path.read_bytes())).read()
        files = parse_sarc(raw)
        for _name, dic_bytes in files.items():
            cdict = zstd.ZstdCompressionDict(dic_bytes)
            did = cdict.dict_id()
            if did:
                self.dicts[did] = cdict

    def decompress(self, data: bytes) -> bytes:
        if data[:4] != b"\x28\xb5\x2f\xfd":
            return data
        if zstd is None:
            raise RuntimeError("zstandard is not installed. Run: uv add zstandard")
        params = zstd.get_frame_parameters(data)
        cdict = self.dicts.get(params.dict_id) if params.dict_id else None
        if params.dict_id and cdict is None:
            raise KeyError(f"Missing Zstd dictionary ID {params.dict_id} (loaded: {list(self.dicts)})")
        dctx = zstd.ZstdDecompressor(dict_data=cdict)
        with dctx.stream_reader(io.BytesIO(data)) as reader:
            return reader.read()

    def read_file(self, path: Path) -> bytes:
        raw = path.read_bytes()
        if path.suffix == ".zs" or raw[:4] == b"\x28\xb5\x2f\xfd":
            return self.decompress(raw)
        return raw

    def compress_like(self, original_bytes: bytes, new_raw: bytes, level: int = 12) -> bytes:
        """Compress new_raw using the same Zstd frame dictionary (if any) as original_bytes."""
        if original_bytes[:4] != b"\x28\xb5\x2f\xfd":
            return new_raw
        if zstd is None:
            raise RuntimeError("zstandard is not installed. Run: uv add zstandard")
        params = zstd.get_frame_parameters(original_bytes)
        cdict = self.dicts.get(params.dict_id) if params.dict_id else None
        if params.dict_id and cdict is None:
            raise KeyError(f"Missing Zstd dictionary ID {params.dict_id} (loaded: {list(self.dicts)})")
        cctx = zstd.ZstdCompressor(level=level, dict_data=cdict)
        return cctx.compress(new_raw)


def parse_amta(data: bytes) -> dict[str, Any]:
    if data[:4] != b"AMTA":
        raise ValueError(f"Not AMTA: {data[:4]!r}")
    end = detect_bom(data, 4)
    version, _filesize, data_off, _mark_off = struct.unpack_from(end + "HIII", data, 0x06)
    if version >= 0x0300:
        _ext_off, strg_off = struct.unpack_from(end + "II", data, 0x14)
    else:
        strg_off, = struct.unpack_from(end + "I", data, 0x14)

    if data[strg_off : strg_off + 4] != b"STRG":
        raise ValueError("Missing STRG in AMTA")
    strg_body_off = strg_off + 8

    def read_str(rel_off: int) -> str:
        s = strg_body_off + rel_off
        if s >= len(data):
            return ""
        e = data.find(b"\x00", s)
        if e == -1:
            e = len(data)
        return data[s:e].decode("utf-8", errors="replace")

    if data[data_off : data_off + 4] != b"DATA":
        raise ValueError("Missing DATA in AMTA")
    body = data_off + 8
    name_off, _out_samples_48k = struct.unpack_from(end + "II", data, body)
    asset_type, channels, num_tracks, flags = struct.unpack_from("BBBB", data, body + 0x08)
    _rms, sample_rate, _loop_start, num_samples, loudness = struct.unpack_from(
        end + "fIIIf", data, body + 0x0C
    )
    tracks = []
    for t in range(min(num_tracks, 8)):
        t_ch, t_vol = struct.unpack_from(end + "If", data, body + 0x20 + t * 8)
        tracks.append({"channels": t_ch, "volume": round(t_vol, 4)})

    return {
        "version": f"{version:#06x}",
        "name": read_str(name_off),
        "type": "Stream" if asset_type == 1 else "Wave",
        "channels": channels,
        "flags": {
            "is_stream": bool(flags & 1),
            "prefetch": bool(flags & 2),
            "is_looped": bool(flags & 4),
            "is_opus": bool(flags & 16),
        },
        "sample_rate": sample_rate,
        "num_samples": num_samples,
        "duration_sec": round(num_samples / sample_rate, 3) if sample_rate else 0.0,
        "loudness_lkfs": round(loudness, 2),
        "tracks": tracks,
    }


def parse_bwav(data: bytes) -> dict[str, Any]:
    if data[:4] != b"BWAV":
        raise ValueError(f"Not BWAV: {data[:4]!r}")
    end = detect_bom(data, 0x04)
    version, crc32_hash, is_prefetch, num_channels = struct.unpack_from(end + "HIHH", data, 0x06)
    channels = []
    for i in range(num_channels):
        off = 0x10 + i * 0x4C
        if off + 0x38 > len(data):
            break
        codec, pan, sr, samples_full, samples_here = struct.unpack_from(end + "HHIII", data, off)
        _start_full, start_here = struct.unpack_from(end + "II", data, off + 0x30)
        channels.append({
            "codec": codec,
            "pan": pan,
            "sample_rate": sr,
            "samples_full": samples_full,
            "samples_here": samples_here,
            "duration_sec": round(samples_full / sr, 3) if sr else 0.0,
            "data_offset": start_here,
        })
    return {
        "version": version,
        "crc32": f"{crc32_hash:08x}",
        "is_prefetch": bool(is_prefetch),
        "num_channels": num_channels,
        "channels": channels,
    }


def parse_bars(data: bytes) -> list[dict[str, Any]]:
    if data[:4] != b"BARS":
        raise ValueError(f"Not BARS: {data[:4]!r}")
    end = detect_bom(data, 0x08)
    _filesize, = struct.unpack_from(end + "I", data, 0x04)
    _version, count = struct.unpack_from(end + "HI", data, 0x0A)
    hashes = struct.unpack_from(end + f"{count}I", data, 0x10)
    offsets_base = 0x10 + count * 4
    entries = []
    for i in range(count):
        amta_off, asset_off = struct.unpack_from(end + "Ii", data, offsets_base + i * 8)
        try:
            amta = parse_amta(data[amta_off:])
        except Exception as exc:
            amta = {"error": str(exc)}
        asset_magic = None
        bwav_info = None
        if asset_off != -1 and asset_off < len(data):
            asset_magic = data[asset_off : asset_off + 4].decode("ascii", errors="replace")
            if asset_magic == "BWAV":
                try:
                    bwav_info = parse_bwav(data[asset_off:])
                except Exception as exc:
                    bwav_info = {"error": str(exc)}
        entries.append({
            "index": i,
            "crc32": f"{hashes[i]:08x}",
            "amta_offset": amta_off,
            "asset_offset": asset_off,
            "asset_magic": asset_magic,
            "amta": amta,
            "bwav": bwav_info,
        })
    return entries


def _bwav_channel_byte_len(codec: int, samples_here: int) -> int:
    if codec == 0:  # PCM16
        return samples_here * 2
    if codec == 1:  # DSP-ADPCM: 14 samples per 8-byte frame
        return ((samples_here + 13) // 14) * 8
    raise ValueError(f"Unsupported BWAV codec for in-place silencing: {codec}")


def silence_bwav(data: bytes, bwav_size: int | None = None) -> bytes:
    """Return a copy of a BWAV with its sample payload zeroed and header CRC32 updated.

    Preserves exact byte length, sample counts, sample rate, and channel layout.
    """
    if data[:4] != b"BWAV":
        raise ValueError(f"Not BWAV: {data[:4]!r}")
    end = detect_bom(data, 0x04)
    _version, _crc32_hash, _is_prefetch, num_channels = struct.unpack_from(end + "HIHH", data, 0x06)
    if num_channels == 0:
        return data[:bwav_size] if bwav_size is not None else data

    channels = []
    for i in range(num_channels):
        off = 0x10 + i * 0x4C
        codec, _pan, _sr, _samples_full, samples_here = struct.unpack_from(end + "HHIII", data, off)
        _start_full, start_here = struct.unpack_from(end + "II", data, off + 0x30)
        ch_bytes = _bwav_channel_byte_len(codec, samples_here)
        channels.append((off, codec, start_here, ch_bytes))

    first_data_off = min(c[2] for c in channels)
    end_off = bwav_size if bwav_size is not None else len(data)
    buf = bytearray(data[:end_off])

    for off, codec, start_here, ch_bytes in channels:
        if codec == 1:
            # Clear DSP-ADPCM initial & loop context (pred_scale, yn1, yn2) at 0x3C..0x48
            buf[off + 0x3C : off + 0x48] = b"\x00" * 12
        buf[start_here : start_here + ch_bytes] = b"\x00" * ch_bytes

    new_crc = zlib.crc32(buf[first_data_off:end_off]) & 0xFFFFFFFF
    struct.pack_into(end + "I", buf, 0x08, new_crc)
    return bytes(buf)


def patch_bars_bwavs(bars_data: bytes, target_crcs: set[int] | None) -> tuple[bytes, list[dict[str, Any]]]:
    """Silence matching BWAV entries in-place inside a decompressed BARS archive.

    If target_crcs is None, all BWAV entries in the archive are silenced.
    """
    if bars_data[:4] != b"BARS":
        raise ValueError(f"Not BARS: {bars_data[:4]!r}")
    end = detect_bom(bars_data, 0x08)
    _filesize, = struct.unpack_from(end + "I", bars_data, 0x04)
    _version, count = struct.unpack_from(end + "HI", bars_data, 0x0A)
    hashes = struct.unpack_from(end + f"{count}I", bars_data, 0x10)
    offsets_base = 0x10 + count * 4

    entry_offsets = [
        struct.unpack_from(end + "Ii", bars_data, offsets_base + i * 8)
        for i in range(count)
    ]
    valid_asset_offs = sorted({a_off for _, a_off in entry_offsets if a_off != -1})

    buf = bytearray(bars_data)
    audit: list[dict[str, Any]] = []
    for i, (amta_off, asset_off) in enumerate(entry_offsets):
        crc = hashes[i]
        if target_crcs is not None and crc not in target_crcs:
            continue
        if asset_off == -1 or asset_off >= len(buf):
            audit.append({"index": i, "crc32": f"{crc:08x}", "status": "skipped_no_embedded_asset"})
            continue
        if buf[asset_off : asset_off + 4] != b"BWAV":
            audit.append({"index": i, "crc32": f"{crc:08x}", "status": "skipped_not_bwav"})
            continue

        idx_in_sorted = valid_asset_offs.index(asset_off)
        next_off = valid_asset_offs[idx_in_sorted + 1] if idx_in_sorted + 1 < len(valid_asset_offs) else len(buf)
        span = next_off - asset_off
        silenced = silence_bwav(bytes(buf[asset_off:next_off]), bwav_size=span)
        if len(silenced) != span:
            raise RuntimeError(f"Silenced BWAV size mismatch: {len(silenced)} != {span}")
        buf[asset_off:next_off] = silenced
        audit.append({
            "index": i,
            "crc32": f"{crc:08x}",
            "asset_offset": asset_off,
            "span_bytes": span,
            "status": "silenced",
        })

    return bytes(buf), audit


# ---------------------------------------------------------------------------
# Nintendo Opus BWAV (codec 2), as used by Voice/Resource/Stream/*.bwav
#   0x00  BWAV header (0x10) + ChannelInfo (0x4C) + padding -> sample data at data_offset (0x80)
#   data_offset: chunk 0x80000001 (info: ver u8, channels u8, frame_size u16, sample_rate u32,
#                data_offset u32, ..., pre_skip u32)
#   then         chunk 0x80000004 (data): repeated [u32be packet_size][u32be final_range][packet]
# ---------------------------------------------------------------------------
OPUS_INFO_CHUNK = 0x80000001
OPUS_DATA_CHUNK = 0x80000004


def parse_opus_bwav(data: bytes) -> dict[str, Any]:
    """Return channel info plus Opus chunks and packets for all channels of a codec-2 BWAV."""
    info = parse_bwav(data)
    if info["num_channels"] == 0 or any(c["codec"] != 2 for c in info["channels"]):
        raise ValueError(f"Not an Opus BWAV: {info}")

    channels_opus: list[dict[str, Any]] = []
    num_channels = info["num_channels"]
    for i, ch in enumerate(info["channels"]):
        ch_start = ch["data_offset"]
        ch_end = info["channels"][i + 1]["data_offset"] if i + 1 < num_channels else len(data)
        off = ch_start
        info_chunk: bytes | None = None
        pre_skip: int | None = None
        packets: list[tuple[bytes, int]] | None = None
        data_chunk_offset: int | None = None

        while off + 8 <= ch_end:
            cid, csz = struct.unpack_from("<II", data, off)
            if cid == OPUS_INFO_CHUNK:
                info_chunk = data[off : off + 8 + csz]
                pre_skip = struct.unpack_from("<I", data, off + 8 + 0x14)[0]
                off += 8 + csz
            elif cid == OPUS_DATA_CHUNK:
                data_chunk_offset = off
                packets = []
                q, end = off + 8, off + 8 + csz
                while q + 8 <= end:
                    size, final_range = struct.unpack_from(">II", data, q)
                    packets.append((data[q + 8 : q + 8 + size], final_range))
                    q += 8 + size
                if q != end or end > ch_end:
                    raise ValueError(f"Opus ch{i} data chunk overrun (q={q}, end={end}, ch_end={ch_end})")
                if any(data[end:ch_end]):
                    raise ValueError(f"Non-zero trailing bytes after Opus ch{i} data chunk")
                break
            else:
                raise ValueError(f"Unknown Opus chunk {cid:#x} at {off:#x} (ch{i})")

        if info_chunk is None or packets is None or data_chunk_offset is None:
            raise ValueError(f"Incomplete Opus chunks in ch{i}")
        channels_opus.append({
            "info_chunk": info_chunk,
            "pre_skip": pre_skip,
            "data_chunk_offset": data_chunk_offset,
            "packets": packets,
        })

    return {
        "bwav": info,
        "pre_skip": channels_opus[0]["pre_skip"],
        "data_chunk_offset": channels_opus[0]["data_chunk_offset"],
        "packets": channels_opus[0]["packets"],
        "channels_opus": channels_opus,
    }


def silence_opus_bwav(data: bytes, silent_packet: bytes, silent_final_range: int) -> bytes:
    """Rebuild a mono or multi-channel codec-2 BWAV with every Opus packet replaced by `silent_packet`.

    Keeps the BWAV header, ChannelInfo (samples_full / sample rate), info chunks and packet COUNT per
    channel, updating channel data offsets (0x40-aligned) and BWAV CRC32.
    """
    parsed = parse_opus_bwav(data)
    end_bom = detect_bom(data, 0x04)
    first_data = parsed["bwav"]["channels"][0]["data_offset"]
    out = bytearray(data[:first_data])
    body = struct.pack(">II", len(silent_packet), silent_final_range) + silent_packet

    for i, ch_opus in enumerate(parsed["channels_opus"]):
        if i > 0:
            pad = (-len(out)) & 0x3F
            out.extend(b"\x00" * pad)
        new_off = len(out)
        ch_info_off = 0x10 + i * 0x4C
        struct.pack_into(end_bom + "II", out, ch_info_off + 0x30, new_off, new_off)

        out.extend(ch_opus["info_chunk"])
        payload = body * len(ch_opus["packets"])
        out.extend(struct.pack("<II", OPUS_DATA_CHUNK, len(payload)))
        out.extend(payload)

    struct.pack_into(end_bom + "I", out, 0x08, zlib.crc32(out[first_data:]) & 0xFFFFFFFF)
    return bytes(out)


def patch_bars_bwav_crcs(
    bars_data: bytes,
    entry_crc_to_new_bwav_crc: dict[int, int],
) -> tuple[bytes, list[dict[str, Any]]]:
    """Update the BWAV header CRC32 (offset 0x08) in-place for matching entries in a BARS archive."""
    if bars_data[:4] != b"BARS":
        raise ValueError(f"Not BARS: {bars_data[:4]!r}")
    end = detect_bom(bars_data, 0x08)
    _filesize, = struct.unpack_from(end + "I", bars_data, 0x04)
    _version, count = struct.unpack_from(end + "HI", bars_data, 0x0A)
    hashes = struct.unpack_from(end + f"{count}I", bars_data, 0x10)
    offsets_base = 0x10 + count * 4

    buf = bytearray(bars_data)
    audit: list[dict[str, Any]] = []
    for i in range(count):
        crc = hashes[i]
        new_bwav_crc = entry_crc_to_new_bwav_crc.get(crc)
        if new_bwav_crc is None:
            continue
        _amta_off, asset_off = struct.unpack_from(end + "Ii", buf, offsets_base + i * 8)
        if asset_off == -1 or asset_off + 0x10 > len(buf):
            audit.append({"index": i, "crc32": f"{crc:08x}", "status": "skipped_no_embedded_asset"})
            continue
        if buf[asset_off : asset_off + 4] != b"BWAV":
            audit.append({"index": i, "crc32": f"{crc:08x}", "status": "skipped_not_bwav"})
            continue
        bw_end = detect_bom(buf, asset_off + 0x04)
        struct.pack_into(bw_end + "I", buf, asset_off + 0x08, new_bwav_crc)
        audit.append({
            "index": i,
            "crc32": f"{crc:08x}",
            "new_bwav_crc32": f"{new_bwav_crc:08x}",
            "status": "crc_updated",
        })

    return bytes(buf), audit


def make_silent_adpcm_bwav(sample_rate: int = 48000, num_samples: int = 14) -> bytes:
    """Build a minimal valid 1-channel non-prefetch DSP-ADPCM BWAV of digital silence."""
    payload = b"\x00" * (((num_samples + 13) // 14) * 8)
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    hdr = struct.pack("<4s2sHIHH", b"BWAV", b"\xFF\xFE", 1, crc, 0, 1)
    ch = bytearray(0x4C)
    struct.pack_into("<HHIII", ch, 0x00, 1, 2, sample_rate, num_samples, num_samples)
    struct.pack_into("<IIIi", ch, 0x30, 0x80, 0x80, 1, -1)
    pad = b"\x00" * (0x80 - 0x10 - 0x4C)
    return hdr + bytes(ch) + pad + payload


def embed_silent_bwavs_in_bars(
    bars_data: bytes,
    target_crcs: set[int] | None = None,
    sample_rate: int = 48000,
    num_samples: int = 14,
) -> tuple[bytes, list[dict[str, Any]]]:
    """Replace matching entries in a BARS archive with embedded non-streamed silent DSP-ADPCM BWAVs.

    Updates AMTA v5 flags at +0x2C to 0x02 (embedded non-streamed Wave), aligns each BWAV to 0x40,
    updates entry asset offsets, and updates the BARS header filesize at +0x04.
    """
    if bars_data[:4] != b"BARS":
        raise ValueError(f"Not BARS: {bars_data[:4]!r}")
    end = detect_bom(bars_data, 0x08)
    _filesize, = struct.unpack_from(end + "I", bars_data, 0x04)
    _version, count = struct.unpack_from(end + "HI", bars_data, 0x0A)
    hashes = struct.unpack_from(end + f"{count}I", bars_data, 0x10)
    offsets_base = 0x10 + count * 4
    entry_offsets = [
        struct.unpack_from(end + "Ii", bars_data, offsets_base + i * 8)
        for i in range(count)
    ]
    valid_asset_offs = sorted({a_off for _, a_off in entry_offsets if a_off != -1})
    if not valid_asset_offs:
        raise ValueError("BARS archive has no asset offsets")

    first_asset_off = valid_asset_offs[0]
    out = bytearray(bars_data[:first_asset_off])
    silent_bwav = make_silent_adpcm_bwav(sample_rate=sample_rate, num_samples=num_samples)
    audit: list[dict[str, Any]] = []

    for i, (amta_off, orig_asset_off) in enumerate(entry_offsets):
        crc = hashes[i]
        replace = target_crcs is None or crc in target_crcs
        if replace:
            if out[amta_off : amta_off + 4] != b"AMTA":
                raise ValueError(f"Expected AMTA at {amta_off:#x}")
            amta_end = detect_bom(out, amta_off + 4)
            amta_ver, = struct.unpack_from(amta_end + "H", out, amta_off + 6)
            if amta_ver != 0x0500:
                raise ValueError(f"Unsupported AMTA version {amta_ver:#x} at {amta_off:#x}")
            struct.pack_into(amta_end + "I", out, amta_off + 0x2C, 0x02)

            pad = (-len(out)) & 0x3F
            out.extend(b"\x00" * pad)
            new_asset_off = len(out)
            out.extend(silent_bwav)
            struct.pack_into(end + "i", out, offsets_base + i * 8 + 4, new_asset_off)
            audit.append({
                "index": i,
                "crc32": f"{crc:08x}",
                "new_asset_offset": new_asset_off,
                "status": "embedded_silent_adpcm",
            })
        else:
            if orig_asset_off != -1 and orig_asset_off < len(bars_data):
                idx_s = valid_asset_offs.index(orig_asset_off)
                next_off = valid_asset_offs[idx_s + 1] if idx_s + 1 < len(valid_asset_offs) else len(bars_data)
                pad = (-len(out)) & 0x3F
                out.extend(b"\x00" * pad)
                new_asset_off = len(out)
                out.extend(bars_data[orig_asset_off:next_off])
                struct.pack_into(end + "i", out, offsets_base + i * 8 + 4, new_asset_off)

    struct.pack_into(end + "I", out, 0x04, len(out))
    return bytes(out), audit


# ---------------------------------------------------------------------------
# ResourceSizeTable (RESTBL v1)
#   0x00  "RESTBL" (6 bytes)
#   0x06  u32 version (1)
#   0x0A  u32 str_block_size (160)
#   0x0E  u32 crc_count
#   0x12  u32 name_count
#   0x16  crc_table[crc_count]:  (u32 crc32, u32 size) sorted by crc32
#   ...   name_table[name_count]: (char[str_block_size] NUL-padded UTF-8, u32 size)
# ---------------------------------------------------------------------------
RESTBL_MAGIC = b"RESTBL"
RESTBL_HEADER_SIZE = 22


def restbl_key(rel_path: str) -> str:
    """Normalize a RomFS relative path to its RESTBL lookup key (strip leading / and trailing .zs)."""
    key = rel_path.replace("\\", "/").lstrip("/")
    if key.endswith(".zs"):
        key = key[:-3]
    return key


def _read_restbl_header(data: bytes) -> tuple[int, int, int, int]:
    if len(data) < RESTBL_HEADER_SIZE or data[:6] != RESTBL_MAGIC:
        raise ValueError(f"Not a RESTBL table (len={len(data)}, magic={data[:6]!r})")
    _magic, version, str_block_size, crc_count, name_count = struct.unpack_from("<6sIIII", data, 0)
    if version != 1:
        raise ValueError(f"Unsupported RESTBL version: {version}")
    expected_len = RESTBL_HEADER_SIZE + crc_count * 8 + name_count * (str_block_size + 4)
    if len(data) != expected_len:
        raise ValueError(f"RESTBL size mismatch: got {len(data)}, expected {expected_len}")
    return version, str_block_size, crc_count, name_count


def parse_restbl(data: bytes) -> dict[str, Any]:
    """Parse a decompressed RESTBL v1 resource size table."""
    version, str_block_size, crc_count, name_count = _read_restbl_header(data)

    crc_entries: dict[int, int] = {}
    crc_off = RESTBL_HEADER_SIZE
    for i in range(crc_count):
        h, sz = struct.unpack_from("<II", data, crc_off + i * 8)
        crc_entries[h] = sz

    name_entries: dict[str, int] = {}
    name_off = crc_off + crc_count * 8
    stride = str_block_size + 4
    for i in range(name_count):
        base = name_off + i * stride
        raw_name = data[base : base + str_block_size].split(b"\x00", 1)[0]
        name = raw_name.decode("utf-8", errors="replace")
        sz, = struct.unpack_from("<I", data, base + str_block_size)
        name_entries[name] = sz

    return {
        "version": version,
        "str_block_size": str_block_size,
        "crc_count": crc_count,
        "name_count": name_count,
        "crc_entries": crc_entries,
        "name_entries": name_entries,
    }


def compute_restbl_required_size(
    vanilla_restbl: int,
    vanilla_raw: int,
    mod_raw: int,
    extra_pad: int = 128,
    align: int = 32,
) -> int:
    """Compute a safe RESTBL entry size preserving vanilla heap overhead plus a padded alignment."""
    if mod_raw <= vanilla_raw:
        return vanilla_restbl
    needed = vanilla_restbl + (mod_raw - vanilla_raw) + extra_pad
    aligned = (needed + align - 1) & ~(align - 1)
    return max(vanilla_restbl, aligned)


def patch_restbl(data: bytes, updates: dict[str, int]) -> tuple[bytes, list[dict[str, Any]]]:
    """Raise RESTBL entries where required_size > current size without shrinking any entry."""
    _version, str_block_size, crc_count, name_count = _read_restbl_header(data)

    crc_off = RESTBL_HEADER_SIZE
    crc_offsets: dict[int, int] = {}
    for i in range(crc_count):
        entry_off = crc_off + i * 8
        h, = struct.unpack_from("<I", data, entry_off)
        crc_offsets[h] = entry_off + 4

    name_off = crc_off + crc_count * 8
    stride = str_block_size + 4
    name_offsets: dict[str, int] = {}
    for i in range(name_count):
        base = name_off + i * stride
        raw_name = data[base : base + str_block_size].split(b"\x00", 1)[0]
        name = raw_name.decode("utf-8", errors="replace")
        name_offsets[name] = base + str_block_size

    buf = bytearray(data)
    audit: list[dict[str, Any]] = []
    for rel_path, required_size in sorted(updates.items()):
        key = restbl_key(rel_path)
        crc = zlib.crc32(key.encode("utf-8")) & 0xFFFFFFFF

        if key in name_offsets:
            sz_off = name_offsets[key]
            old_size, = struct.unpack_from("<I", buf, sz_off)
            if required_size > old_size:
                struct.pack_into("<I", buf, sz_off, required_size)
                audit.append({
                    "rel_path": rel_path,
                    "restbl_key": key,
                    "crc32": f"{crc:08x}",
                    "table": "name",
                    "vanilla_restbl_bytes": old_size,
                    "new_restbl_bytes": required_size,
                    "delta_bytes": required_size - old_size,
                })

        if crc in crc_offsets:
            sz_off = crc_offsets[crc]
            old_size, = struct.unpack_from("<I", buf, sz_off)
            if required_size > old_size:
                struct.pack_into("<I", buf, sz_off, required_size)
                audit.append({
                    "rel_path": rel_path,
                    "restbl_key": key,
                    "crc32": f"{crc:08x}",
                    "table": "crc",
                    "vanilla_restbl_bytes": old_size,
                    "new_restbl_bytes": required_size,
                    "delta_bytes": required_size - old_size,
                })

    return bytes(buf), audit


# ---------------------------------------------------------------------------
# MSBT message files (Mals/*.sarc.zs -> EventFlowMsg/*.msbt)
# ---------------------------------------------------------------------------
def parse_msbt(data: bytes) -> dict[str, bytes]:
    """Parse a UTF-8 MSBT file and return {label: raw_txt2_bytes}."""
    if data[:8] != b"MsgStdBn":
        raise ValueError(f"Not an MSBT file (magic={data[:8]!r})")
    end = detect_bom(data, 0x08)
    encoding = data[0x0C]
    if encoding != 0:
        raise ValueError(f"Unsupported MSBT encoding {encoding} (expected 0 = UTF-8)")
    section_count, = struct.unpack_from(end + "H", data, 0x0E)

    labels: dict[int, str] = {}
    texts: list[bytes] = []
    off = 0x20
    for _ in range(section_count):
        if off + 0x10 > len(data):
            raise ValueError("Truncated MSBT section header")
        magic = data[off : off + 4]
        size, = struct.unpack_from(end + "I", data, off + 4)
        body = off + 0x10
        if body + size > len(data):
            raise ValueError(f"Truncated MSBT section {magic!r}")

        if magic == b"LBL1":
            bucket_count, = struct.unpack_from(end + "I", data, body)
            for i in range(bucket_count):
                cnt, bucket_off = struct.unpack_from(end + "II", data, body + 4 + i * 8)
                p = body + bucket_off
                for _ in range(cnt):
                    name_len = data[p]
                    name = data[p + 1 : p + 1 + name_len].decode("utf-8", errors="replace")
                    idx, = struct.unpack_from(end + "I", data, p + 1 + name_len)
                    labels[idx] = name
                    p += 1 + name_len + 4
        elif magic == b"TXT2":
            txt_count, = struct.unpack_from(end + "I", data, body)
            offs = [struct.unpack_from(end + "I", data, body + 4 + i * 4)[0] for i in range(txt_count)]
            offs.append(size)
            texts = [data[body + offs[i] : body + offs[i + 1]] for i in range(txt_count)]

        off = body + ((size + 15) & ~15)

    return {labels[i]: texts[i] for i in sorted(labels) if i < len(texts)}


def _extract_msbt_tag_name(params: bytes) -> str | None:
    """Return an ASCII identifier carried in an MSBT 0x0E tag's params, if present."""
    if len(params) < 4:
        return None
    str_len, = struct.unpack_from("<H", params, 0)
    if str_len == 0 or 2 + str_len > len(params):
        return None
    raw_id = params[2 : 2 + str_len]
    trailing = params[2 + str_len :]
    if trailing not in (b"", b"\x00"):
        return None
    if not all(0x21 <= b < 0x7F for b in raw_id):
        return None
    return raw_id.decode("ascii")


def _parse_msbt_txt_parts(raw: bytes) -> tuple[str, list[str]]:
    """Decode raw UTF-8 TXT2 bytes into plain text with [tag] markers and a tag-name list."""
    parts: list[str] = []
    tags: list[str] = []
    text_buf = bytearray()
    i = 0
    n = len(raw)

    def flush_text() -> None:
        if text_buf:
            parts.append(text_buf.decode("utf-8", errors="replace"))
            text_buf.clear()

    while i < n:
        b = raw[i]
        if b == 0x0E and i + 7 <= n:
            flush_text()
            _group, _tag_type, param_size = struct.unpack_from("<HHH", raw, i + 1)
            end_tag = min(n, i + 7 + param_size)
            tag_name = _extract_msbt_tag_name(raw[i + 7 : end_tag])
            if tag_name is not None:
                tags.append(tag_name)
                parts.append(f"[{tag_name}]")
            i = end_tag
        elif b == 0x0F and i + 5 <= n:
            flush_text()
            i += 5
        elif b == 0x00:
            i += 1
        else:
            text_buf.append(b)
            i += 1

    flush_text()
    return "".join(parts), tags


def msbt_plain_text(raw: bytes) -> str:
    """Decode raw MSBT TXT2 bytes to plain text, formatting named 0x0E tags as [tag]."""
    text, _ = _parse_msbt_txt_parts(raw)
    return text


def msbt_tag_names(raw: bytes) -> list[str]:
    """Return the list of named 0x0E tag identifiers found in raw MSBT TXT2 bytes."""
    _, tags = _parse_msbt_txt_parts(raw)
    return tags


# ---------------------------------------------------------------------------
# DSP-ADPCM BWAV decoding & WAV export (read-only review utility)
# ---------------------------------------------------------------------------
def decode_dsp_adpcm_bwav(data: bytes) -> tuple[int, list[list[int]]]:
    """Decode a DSP-ADPCM (codec 1) BWAV into (sample_rate, per_channel_int16_samples)."""
    if data[:4] != b"BWAV":
        raise ValueError(f"Not BWAV: {data[:4]!r}")
    end = detect_bom(data, 0x04)
    _version, _crc32_hash, _is_prefetch, num_channels = struct.unpack_from(end + "HIHH", data, 0x06)
    if num_channels == 0:
        raise ValueError("BWAV has 0 channels")

    sample_rate = 0
    decoded_channels: list[list[int]] = []
    for ch_idx in range(num_channels):
        off = 0x10 + ch_idx * 0x4C
        if off + 0x4C > len(data):
            raise ValueError(f"Truncated BWAV ChannelInfo at channel {ch_idx}")
        codec, _pan, sr, _samples_full, samples_here = struct.unpack_from(end + "HHIII", data, off)
        if codec != 1:
            raise ValueError(f"Unsupported BWAV codec {codec} (expected DSP-ADPCM codec 1)")
        if ch_idx == 0:
            sample_rate = sr
        coefs = struct.unpack_from(end + "16h", data, off + 0x10)
        _start_full, start_here = struct.unpack_from(end + "II", data, off + 0x30)
        _pred_scale, yn1, yn2 = struct.unpack_from(end + "Hhh", data, off + 0x3C)

        out: list[int] = []
        pos = start_here
        while len(out) < samples_here:
            if pos + 8 > len(data):
                raise ValueError(f"Truncated DSP-ADPCM frame at {pos:#x}")
            ps = data[pos]
            pos += 1
            scale = 1 << (ps & 0x0F)
            coef_idx = ps >> 4
            if coef_idx >= 8:
                raise ValueError(f"Invalid DSP-ADPCM coef index {coef_idx} at {pos - 1:#x}")
            c1 = coefs[coef_idx * 2]
            c2 = coefs[coef_idx * 2 + 1]

            for byte_val in data[pos : pos + 7]:
                for nib in ((byte_val >> 4) & 0x0F, byte_val & 0x0F):
                    if len(out) >= samples_here:
                        break
                    signed_nib = nib - 16 if nib >= 8 else nib
                    sample = (((signed_nib * scale) << 11) + 1024 + c1 * yn1 + c2 * yn2) >> 11
                    if sample > 32767:
                        sample = 32767
                    elif sample < -32768:
                        sample = -32768
                    yn2 = yn1
                    yn1 = sample
                    out.append(sample)
                if len(out) >= samples_here:
                    break
            pos += 7

        decoded_channels.append(out)

    return sample_rate, decoded_channels


def write_wav(path: Path, sample_rate: int, channels: list[list[int]]) -> None:
    """Write 16-bit signed PCM channels to a WAV file."""
    if not channels:
        raise ValueError("Cannot write WAV with 0 channels")
    n_ch = len(channels)
    n_samples = min(len(ch) for ch in channels)
    if n_ch == 1:
        interleaved = channels[0][:n_samples]
    else:
        interleaved = [channels[c][i] for i in range(n_samples) for c in range(n_ch)]
    pcm_bytes = struct.pack(f"<{n_samples * n_ch}h", *interleaved)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(n_ch)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
