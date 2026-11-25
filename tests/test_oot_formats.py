from __future__ import annotations

import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from build_mod import ensure_safe_target, find_vanilla_restbl  # noqa: E402
from oot_formats import (  # noqa: E402
    OPUS_DATA_CHUNK,
    OPUS_INFO_CHUNK,
    compute_restbl_required_size,
    embed_silent_bwavs_in_bars,
    make_silent_adpcm_bwav,
    parse_bars,
    parse_bwav,
    parse_opus_bwav,
    parse_restbl,
    patch_bars_bwav_crcs,
    patch_bars_bwavs,
    patch_restbl,
    restbl_key,
    silence_bwav,
    silence_opus_bwav,
)


def _make_synthetic_adpcm_bwav(samples: int = 28, fill: bytes = b"\x5A") -> bytes:
    payload_len = ((samples + 13) // 14) * 8
    payload = fill * payload_len
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    hdr = struct.pack("<4s2sHIHH", b"BWAV", b"\xFF\xFE", 1, crc, 0, 1)
    ch = bytearray(0x4C)
    struct.pack_into("<HHIII", ch, 0x00, 1, 2, 48000, samples, samples)
    struct.pack_into("<IIIi", ch, 0x30, 0x80, 0x80, 1, -1)
    ch[0x3C:0x48] = b"\xAB" * 12
    pad = b"\x00" * (0x80 - 0x10 - 0x4C)
    return hdr + bytes(ch) + pad + payload


def _make_synthetic_opus_bwav(num_channels: int = 1, pkts_per_ch: int = 3) -> bytes:
    first_data = 0x80 if num_channels == 1 else 0xC0
    out = bytearray(first_data)
    struct.pack_into("<4s2sHIHH", out, 0x00, b"BWAV", b"\xFF\xFE", 1, 0, 0, num_channels)
    for ch_idx in range(num_channels):
        if ch_idx > 0:
            out.extend(b"\x00" * ((-len(out)) & 0x3F))
        ch_start = len(out)
        info_payload = bytearray(24)
        struct.pack_into("<I", info_payload, 0x14, 120)
        out.extend(struct.pack("<II", OPUS_INFO_CHUNK, len(info_payload)) + info_payload)
        pkt_payload = (struct.pack(">II", 4, 0x12345678) + b"\xF8\x01\x02\x03") * pkts_per_ch
        out.extend(struct.pack("<II", OPUS_DATA_CHUNK, len(pkt_payload)) + pkt_payload)
        ch_off = 0x10 + ch_idx * 0x4C
        samples = pkts_per_ch * 960
        struct.pack_into("<HHIII", out, ch_off, 2, ch_idx, 48000, samples, samples)
        struct.pack_into("<II", out, ch_off + 0x30, ch_start, ch_start)
    struct.pack_into("<I", out, 0x08, zlib.crc32(out[first_data:]) & 0xFFFFFFFF)
    return bytes(out)


def _make_synthetic_bars(entries: list[tuple[int, int, bytes]]) -> bytes:
    """Build a minimal BARS archive from [(name_crc32, amta_flags, asset_bytes), ...]."""
    count = len(entries)
    amta_start = 0x10 + count * 12
    amta_size = 0x40
    assets_start = amta_start + count * amta_size
    assets_start += (-assets_start) & 0x3F

    buf = bytearray(assets_start)
    struct.pack_into("<4sI2sHI", buf, 0x00, b"BARS", 0, b"\xFF\xFE", 0x0102, count)

    for i, (crc, flags, asset) in enumerate(entries):
        struct.pack_into("<I", buf, 0x10 + i * 4, crc)
        amta_off = amta_start + i * amta_size
        buf.extend(b"\x00" * ((-len(buf)) & 0x3F))
        asset_off = len(buf)
        buf.extend(asset)

        struct.pack_into("<Ii", buf, 0x10 + count * 4 + i * 8, amta_off, asset_off)
        struct.pack_into("<4s2sHI", buf, amta_off, b"AMTA", b"\xFF\xFE", 0x0500, amta_size)
        struct.pack_into("<II", buf, amta_off + 0x28, crc, flags)

    struct.pack_into("<I", buf, 0x04, len(buf))
    return bytes(buf)


def _make_synthetic_restbl(
    crc_entries: dict[int, int],
    name_entries: dict[str, int],
    str_block_size: int = 160,
) -> bytes:
    out = bytearray(
        struct.pack("<6sIIII", b"RESTBL", 1, str_block_size, len(crc_entries), len(name_entries))
    )
    for h in sorted(crc_entries):
        out.extend(struct.pack("<II", h, crc_entries[h]))
    for name in sorted(name_entries):
        raw_name = name.encode("utf-8")
        out.extend(raw_name + b"\x00" * (str_block_size - len(raw_name)))
        out.extend(struct.pack("<I", name_entries[name]))
    return bytes(out)


class TestOotFormats(unittest.TestCase):
    def test_make_and_silence_adpcm_bwav(self) -> None:
        bw = make_silent_adpcm_bwav(sample_rate=48000, num_samples=14)
        self.assertEqual(len(bw), 136)
        info = parse_bwav(bw)
        self.assertFalse(info["is_prefetch"])
        self.assertEqual(info["channels"][0]["codec"], 1)
        self.assertEqual(info["channels"][0]["samples_full"], 14)

        noisy = _make_synthetic_adpcm_bwav(samples=28, fill=b"\x7F")
        silenced = silence_bwav(noisy)
        self.assertEqual(len(silenced), len(noisy))
        self.assertEqual(silenced[0x80:], b"\x00" * 16)
        self.assertEqual(silenced[0x10 + 0x3C : 0x10 + 0x48], b"\x00" * 12)
        expected_crc = zlib.crc32(silenced[0x80:]) & 0xFFFFFFFF
        self.assertEqual(struct.unpack_from("<I", silenced, 0x08)[0], expected_crc)

    def test_patch_bars_bwavs_selective(self) -> None:
        e1 = _make_synthetic_adpcm_bwav(samples=14, fill=b"\x11")
        e2 = _make_synthetic_adpcm_bwav(samples=14, fill=b"\x22")
        bars = _make_synthetic_bars([(0x11111111, 0x03, e1), (0x22222222, 0x03, e2)])
        patched, audit = patch_bars_bwavs(bars, {0x11111111})
        self.assertEqual(len(patched), len(bars))
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["status"], "silenced")

        parsed = parse_bars(patched)
        off1 = parsed[0]["asset_offset"]
        off2 = parsed[1]["asset_offset"]
        self.assertEqual(patched[off1 + 0x80 : off1 + 0x88], b"\x00" * 8)
        self.assertEqual(patched[off2 + 0x80 : off2 + 0x88], b"\x22" * 8)

    def test_embed_silent_bwavs_in_bars(self) -> None:
        stub = _make_synthetic_adpcm_bwav(samples=0, fill=b"")
        bars = _make_synthetic_bars([(0xAAAAAAAA, 0x11, stub), (0xBBBBBBBB, 0x11, stub)])
        rebuilt, audit = embed_silent_bwavs_in_bars(bars, target_crcs=None)
        self.assertEqual(len(audit), 2)
        self.assertEqual(struct.unpack_from("<I", rebuilt, 0x04)[0], len(rebuilt))
        parsed = parse_bars(rebuilt)
        for entry in parsed:
            flags = struct.unpack_from("<I", rebuilt, entry["amta_offset"] + 0x2C)[0]
            self.assertEqual(flags, 0x02)
            self.assertEqual(entry["bwav"]["channels"][0]["samples_here"], 14)
            self.assertEqual(entry["bwav"]["channels"][0]["codec"], 1)

    def test_patch_bars_bwav_crcs(self) -> None:
        stub = _make_synthetic_adpcm_bwav(samples=0, fill=b"")
        bars = _make_synthetic_bars([(0xCAFEBABE, 0x11, stub)])
        patched, audit = patch_bars_bwav_crcs(bars, {0xCAFEBABE: 0xDEADBEEF})
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["new_bwav_crc32"], "deadbeef")
        self.assertEqual(parse_bars(patched)[0]["bwav"]["crc32"], "deadbeef")

    def test_silence_opus_bwav_mono_and_stereo(self) -> None:
        for n_ch in (1, 2):
            opus = _make_synthetic_opus_bwav(num_channels=n_ch, pkts_per_ch=4)
            sil = silence_opus_bwav(opus, b"\xF8\xFF\xFE", 0x01000000)
            self.assertLess(len(sil), len(opus))
            parsed = parse_opus_bwav(sil)
            self.assertEqual(parsed["bwav"]["num_channels"], n_ch)
            for ch in parsed["channels_opus"]:
                self.assertEqual(len(ch["packets"]), 4)
                for pkt, rng in ch["packets"]:
                    self.assertEqual(pkt, b"\xF8\xFF\xFE")
                    self.assertEqual(rng, 0x01000000)

    def test_ensure_safe_target_blocks_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            romfs = Path(tmp) / "romfs"
            romfs.mkdir()
            with self.assertRaises(SystemExit):
                ensure_safe_target(romfs / "Voice", romfs, "--out")
            with self.assertRaises(SystemExit):
                ensure_safe_target(Path(tmp), romfs, "--out")
            ensure_safe_target(Path(tmp) / "dist", romfs, "--out")

    def test_restbl_key(self) -> None:
        self.assertEqual(
            restbl_key("Voice/Resource/USen_EventFlowMsg-_Dm_Kokiri_0050.bars.zs"),
            "Voice/Resource/USen_EventFlowMsg-_Dm_Kokiri_0050.bars",
        )
        self.assertEqual(
            restbl_key("/Sound\\Resource\\Stream\\NV_Mido_00.bwav"),
            "Sound/Resource/Stream/NV_Mido_00.bwav",
        )

    def test_compute_restbl_required_size(self) -> None:
        # Unchanged / shrunk mod files keep vanilla RESTBL size
        self.assertEqual(compute_restbl_required_size(3240, 2652, 2652), 3240)
        self.assertEqual(compute_restbl_required_size(3240, 2652, 2000), 3240)
        # Grown mod files preserve vanilla overhead + 128-byte pad, aligned up to 32
        req = compute_restbl_required_size(936, 348, 392)
        self.assertEqual(req, 1120)
        self.assertEqual(req % 32, 0)
        self.assertGreaterEqual(req, 936 + (392 - 348) + 128)

    def test_parse_and_patch_restbl(self) -> None:
        key_a = "Voice/Resource/A.bars"
        key_b = "Voice/Resource/B.bars"
        key_named = "Actor/Test.bgyml"
        crc_a = zlib.crc32(key_a.encode("utf-8")) & 0xFFFFFFFF
        crc_b = zlib.crc32(key_b.encode("utf-8")) & 0xFFFFFFFF
        raw = _make_synthetic_restbl(
            crc_entries={crc_a: 1000, crc_b: 2000},
            name_entries={key_named: 5000},
        )
        parsed = parse_restbl(raw)
        self.assertEqual(parsed["version"], 1)
        self.assertEqual(parsed["crc_count"], 2)
        self.assertEqual(parsed["name_count"], 1)

        patched, audit = patch_restbl(
            raw,
            {
                key_a + ".zs": 1500,  # raise
                key_b + ".zs": 1800,  # smaller -> must NOT shrink
                key_named: 6400,      # raise in name table
            },
        )
        self.assertEqual(len(patched), len(raw))
        self.assertEqual(len(audit), 2)
        reparsed = parse_restbl(patched)
        self.assertEqual(reparsed["crc_entries"][crc_a], 1500)
        self.assertEqual(reparsed["crc_entries"][crc_b], 2000)
        self.assertEqual(reparsed["name_entries"][key_named], 6400)

        with self.assertRaises(ValueError):
            parse_restbl(b"BAD!" + raw[4:])
        with self.assertRaises(ValueError):
            parse_restbl(raw[:-4])

    def test_find_vanilla_restbl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            romfs = Path(tmp) / "romfs"
            self.assertIsNone(find_vanilla_restbl(romfs))
            sys_res = romfs / "System" / "Resource"
            sys_res.mkdir(parents=True)
            (sys_res / "ResourceSizeTable.Product.100.Nin_Ounce_NVN2.rsizetable.zs").write_bytes(b"100")
            (sys_res / "ResourceSizeTable.Product.111.Nin_Ounce_NVN2.rsizetable.zs").write_bytes(b"111")
            found = find_vanilla_restbl(romfs)
            self.assertIsNotNone(found)
            self.assertEqual(found.name, "ResourceSizeTable.Product.111.Nin_Ounce_NVN2.rsizetable.zs")


if __name__ == "__main__":
    unittest.main()
