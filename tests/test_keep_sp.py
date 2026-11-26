from __future__ import annotations

import struct
import sys
import tempfile
import unittest
import wave
import zlib
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from build_mod import collect_streams, keep_sp_match, load_keep_sp, sp_clip_id  # noqa: E402
from oot_formats import (  # noqa: E402
    decode_dsp_adpcm_bwav,
    make_silent_adpcm_bwav,
    msbt_plain_text,
    msbt_tag_names,
    parse_msbt,
    write_wav,
)


def _build_synthetic_msbt(entries: dict[str, bytes]) -> bytes:
    """Build a minimal little-endian UTF-8 MSBT file with LBL1 and TXT2 sections."""
    items = list(entries.items())
    # LBL1: 1 bucket holding all labels
    lbl_entries = bytearray()
    for idx, (label, _) in enumerate(items):
        enc = label.encode("utf-8")
        lbl_entries.append(len(enc))
        lbl_entries.extend(enc)
        lbl_entries.extend(struct.pack("<I", idx))
    lbl_body = struct.pack("<III", 1, len(items), 12) + bytes(lbl_entries)
    lbl_pad = (-len(lbl_body)) & 0x0F
    lbl_sec = struct.pack("<4sI8x", b"LBL1", len(lbl_body)) + lbl_body + b"\x00" * lbl_pad

    # TXT2
    offsets = []
    txt_data = bytearray()
    base = 4 + 4 * len(items)
    for _, raw in items:
        offsets.append(base + len(txt_data))
        txt_data.extend(raw)
    txt_body = struct.pack(f"<I{len(items)}I", len(items), *offsets) + bytes(txt_data)
    txt_pad = (-len(txt_body)) & 0x0F
    txt_sec = struct.pack("<4sI8x", b"TXT2", len(txt_body)) + txt_body + b"\x00" * txt_pad

    total_size = 0x20 + len(lbl_sec) + len(txt_sec)
    hdr = struct.pack(
        "<8s2sHBBHHI10x",
        b"MsgStdBn",
        b"\xFF\xFE",
        0,     # unknown
        0,     # encoding: 0 = UTF-8
        3,     # version
        2,     # section_count
        0,     # unknown2
        total_size,
    )
    return hdr + lbl_sec + txt_sec


def _build_one_frame_adpcm_bwav(payload_7_bytes: bytes, sample_rate: int = 48000) -> bytes:
    """Build a 14-sample DSP-ADPCM BWAV with zeroed coefs, zeroed yn1/yn2, and ps=0x00."""
    assert len(payload_7_bytes) == 7
    frame = b"\x00" + payload_7_bytes  # ps = 0x00 -> scale = 1, coef_idx = 0
    data_off = 0x80
    crc = zlib.crc32(frame) & 0xFFFFFFFF
    hdr = struct.pack("<4s2sHIHH", b"BWAV", b"\xFF\xFE", 1, crc, 0, 1)
    ch = bytearray(0x4C)
    struct.pack_into("<HHIII", ch, 0x00, 1, 2, sample_rate, 14, 14)
    # coefs at +0x10..+0x30 left as 0
    struct.pack_into("<II", ch, 0x30, data_off, data_off)
    # pred_scale, yn1, yn2 at +0x3C..+0x42 left as 0
    pad = b"\x00" * (data_off - (0x10 + 0x4C))
    return hdr + bytes(ch) + pad + frame


class TestKeepSpAndFormats(unittest.TestCase):
    def test_sp_clip_id(self) -> None:
        self.assertEqual(
            sp_clip_id("NV_USen_Custom_Saria_Dm_Kokiri_0060_0000_Sp00.bwav"),
            "Saria_Dm_Kokiri_0060_0000_Sp00",
        )
        self.assertEqual(
            sp_clip_id("NV_Mido_Dm_Kokiri_0145_0000_Sp00.bwav"),
            "Mido_Dm_Kokiri_0145_0000_Sp00",
        )
        self.assertIsNone(sp_clip_id("NV_USen_Custom_Saria_Angry00.bwav"))

    def test_load_keep_sp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "keep.txt"
            p.write_text(
                "# Header comment\n"
                "\n"
                "  Talon_*   # inline comment\n"
                "   # indented comment\n"
                "Saria_Dm_Kokiri_0060_0000_Sp00\n",
                encoding="utf-8",
            )
            self.assertEqual(load_keep_sp(p), ["Talon_*", "Saria_Dm_Kokiri_0060_0000_Sp00"])

    def test_keep_sp_match(self) -> None:
        patterns = [
            "Talon_*",
            "Saria_Dm_Kokiri_0060_0000_Sp00",
            "NV_JPja_Custom_Darunia_Dm_Goron_0030_0010_Sp00",
        ]
        self.assertEqual(
            keep_sp_match("NV_USen_Custom_Saria_Dm_Kokiri_0060_0000_Sp00.bwav", patterns),
            "Saria_Dm_Kokiri_0060_0000_Sp00",
        )
        self.assertEqual(
            keep_sp_match("NV_Talon_Dm_HrlCastle_0020_0009_Sp00.bwav", patterns),
            "Talon_*",
        )
        self.assertEqual(
            keep_sp_match("NV_JPja_Custom_Darunia_Dm_Goron_0030_0010_Sp00.bwav", patterns),
            "NV_JPja_Custom_Darunia_Dm_Goron_0030_0010_Sp00",
        )
        self.assertIsNone(
            keep_sp_match("NV_USen_Custom_Darunia_Dm_Goron_0030_0010_Sp00.bwav", patterns)
        )

    def test_collect_streams_keep_and_all_custom_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sdir = Path(tmp)
            names = [
                "NV_Talon_Dm_HrlCastle_0020_0009_Sp00.bwav",
                "NV_USen_Custom_Saria_Dm_Kokiri_0060_0000_Sp00.bwav",
                "NV_USen_Custom_Saria_Dm_Kokiri_0060_0004_Sp00.bwav",
                "NV_USen_Custom_Saria_Angry00.bwav",
                "Vo_Link_Jump00.bwav",
            ]
            for n in names:
                (sdir / n).write_bytes(b"")

            targets, preserved, kept = collect_streams(
                sdir,
                cutscene_filters=[],
                custom_chars=set(),
                all_custom=False,
                keep_patterns=["Talon_*", "Saria_Dm_Kokiri_0060_0000_Sp00"],
            )
            self.assertEqual([p.name for p in targets], ["NV_USen_Custom_Saria_Dm_Kokiri_0060_0004_Sp00.bwav"])
            self.assertEqual([p.name for p in preserved], ["NV_USen_Custom_Saria_Angry00.bwav"])
            self.assertEqual(
                [(p.name, cid, pat) for p, cid, pat in kept],
                [
                    ("NV_Talon_Dm_HrlCastle_0020_0009_Sp00.bwav", "Talon_Dm_HrlCastle_0020_0009_Sp00", "Talon_*"),
                    (
                        "NV_USen_Custom_Saria_Dm_Kokiri_0060_0000_Sp00.bwav",
                        "Saria_Dm_Kokiri_0060_0000_Sp00",
                        "Saria_Dm_Kokiri_0060_0000_Sp00",
                    ),
                ],
            )

            # --all-custom overrides allowlist for Custom_ characters
            targets2, preserved2, kept2 = collect_streams(
                sdir,
                cutscene_filters=[],
                custom_chars=set(),
                all_custom=True,
                keep_patterns=["Talon_*", "Saria_Dm_Kokiri_0060_0000_Sp00"],
            )
            self.assertEqual(
                [p.name for p in targets2],
                [
                    "NV_USen_Custom_Saria_Angry00.bwav",
                    "NV_USen_Custom_Saria_Dm_Kokiri_0060_0000_Sp00.bwav",
                    "NV_USen_Custom_Saria_Dm_Kokiri_0060_0004_Sp00.bwav",
                ],
            )
            self.assertEqual(preserved2, [])
            self.assertEqual([p.name for p, _, _ in kept2], ["NV_Talon_Dm_HrlCastle_0020_0009_Sp00.bwav"])

    def test_parse_msbt_and_plain_text(self) -> None:
        tag_id = b"Dm_X_0000_Sp_00"
        tag_params = struct.pack("<H", len(tag_id)) + tag_id + b"\x00"
        tag_bytes = b"\x0E" + struct.pack("<HHH", 4, 0, len(tag_params)) + tag_params
        close_tag = b"\x0F" + struct.pack("<HH", 1, 2)
        raw_entry = tag_bytes + b"Hello" + close_tag + b"!\x00"

        msbt = _build_synthetic_msbt({"Line_0000": raw_entry})
        parsed = parse_msbt(msbt)
        self.assertIn("Line_0000", parsed)
        self.assertEqual(msbt_tag_names(parsed["Line_0000"]), ["Dm_X_0000_Sp_00"])
        self.assertEqual(msbt_plain_text(parsed["Line_0000"]), "[Dm_X_0000_Sp_00]Hello!")

    def test_decode_dsp_adpcm_bwav_and_write_wav(self) -> None:
        silent = make_silent_adpcm_bwav(sample_rate=48000, num_samples=14)
        sr, ch = decode_dsp_adpcm_bwav(silent)
        self.assertEqual(sr, 48000)
        self.assertEqual(ch, [[0] * 14])

        # Nibbles 1..7, -1..-7 (0xF..0x9) packed high-nibble first in 7 bytes
        payload = bytes([0x12, 0x34, 0x56, 0x7F, 0xED, 0xCB, 0xA9])
        bwav = _build_one_frame_adpcm_bwav(payload, sample_rate=32000)
        sr2, ch2 = decode_dsp_adpcm_bwav(bwav)
        self.assertEqual(sr2, 32000)
        self.assertEqual(ch2, [[1, 2, 3, 4, 5, 6, 7, -1, -2, -3, -4, -5, -6, -7]])

        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "out.wav"
            write_wav(wav_path, sr2, ch2)
            with wave.open(str(wav_path), "rb") as wf:
                self.assertEqual(wf.getnchannels(), 1)
                self.assertEqual(wf.getsampwidth(), 2)
                self.assertEqual(wf.getframerate(), 32000)
                self.assertEqual(wf.getnframes(), 14)


if __name__ == "__main__":
    unittest.main()
