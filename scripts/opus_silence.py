"""Generate and verify a digitally silent Opus packet with the system libopus (ctypes).

The game's Voice/ streams are CELT fullband 20 ms mono frames (TOC 0xF8, 960 samples at 48 kHz) and every
packet is stored with libopus' `final_range`. This module encodes 960 zero samples with libopus
(RESTRICTED_LOWDELAY -> CELT, 48 kHz mono), decodes the result with a fresh decoder, and refuses to continue
unless it decodes to exactly 960 zero samples with the same TOC as the game's packets.
"""
from __future__ import annotations

import ctypes
import ctypes.util

OPUS_APPLICATION_RESTRICTED_LOWDELAY = 2051
OPUS_SET_BITRATE_REQUEST = 4002
OPUS_GET_FINAL_RANGE_REQUEST = 4031
OPUS_RESET_STATE = 4028
FRAME_SAMPLES = 960
EXPECTED_TOC = 0xF8


def _lib() -> ctypes.CDLL:
    name = ctypes.util.find_library("opus") or "libopus.so.0"
    return ctypes.CDLL(name)


class OpusChecker:
    def __init__(self) -> None:
        self.lib = _lib()
        err = ctypes.c_int()
        self.dec = self.lib.opus_decoder_create(48000, 1, ctypes.byref(err))
        if err.value:
            raise RuntimeError(f"opus_decoder_create failed: {err.value}")
        self.pcm = (ctypes.c_int16 * 5760)()

    def decode(self, packet: bytes, reset: bool = True) -> tuple[int, int, list[int]]:
        """Return (samples, final_range, pcm) for one packet."""
        if reset:
            self.lib.opus_decoder_ctl(self.dec, OPUS_RESET_STATE)
        n = self.lib.opus_decode(self.dec, packet, len(packet), self.pcm, 5760, 0)
        rng = ctypes.c_uint32()
        self.lib.opus_decoder_ctl(self.dec, OPUS_GET_FINAL_RANGE_REQUEST, ctypes.byref(rng))
        return n, rng.value, list(self.pcm[: max(n, 0)])

    def check_range_reproduction(self, packets: list[tuple[bytes, int]]) -> int:
        """Decode a real stream sequentially; return how many stored final_range values libopus reproduces."""
        self.lib.opus_decoder_ctl(self.dec, OPUS_RESET_STATE)
        return sum(1 for pkt, rng in packets if self.decode(pkt, reset=False)[1] == rng)


def make_silent_packet(checker: OpusChecker) -> tuple[bytes, int]:
    """Encode one 20 ms frame of digital silence and verify it; return (packet, final_range)."""
    lib = checker.lib
    err = ctypes.c_int()
    enc = lib.opus_encoder_create(48000, 1, OPUS_APPLICATION_RESTRICTED_LOWDELAY, ctypes.byref(err))
    if err.value:
        raise RuntimeError(f"opus_encoder_create failed: {err.value}")
    lib.opus_encoder_ctl(enc, OPUS_SET_BITRATE_REQUEST, ctypes.c_int(64000))
    out = (ctypes.c_ubyte * 1500)()
    zeros = (ctypes.c_int16 * FRAME_SAMPLES)()
    size = lib.opus_encode(enc, zeros, FRAME_SAMPLES, out, 1500)
    lib.opus_encoder_destroy(enc)
    if size <= 0:
        raise RuntimeError(f"opus_encode failed: {size}")
    packet = bytes(out[:size])
    n, final_range, pcm = checker.decode(packet)
    if packet[0] != EXPECTED_TOC or n != FRAME_SAMPLES or any(pcm):
        raise RuntimeError(f"Silent packet failed verification: {packet.hex()} n={n} nonzero={sum(1 for s in pcm if s)}")
    return packet, final_range
