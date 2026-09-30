"""Vectorized decoder for the legacy PACMAN envelope and LArPix-v2 payload.

Reference: BrunoGelli/2x2Pacmon @ 2cf0e2c7db056dd205efb7f41616c1795fa9ea67,
pkg/message.go and pkg/packet.go. This is NOT a Rev5/24-byte-word decoder.
"""
from dataclasses import dataclass
import struct
import numpy as np

HEADER = struct.Struct("<cIxH")
WORD = np.dtype({"names": ["kind", "io", "receipt", "payload"],
                 "formats": ["u1", "u1", "<u4", "<u8"],
                 "offsets": [0, 1, 2, 8], "itemsize": 16})
MAX_MESSAGE = 8 + 65535 * 16


class DecodeError(ValueError):
    """A complete ZMQ message does not match the configured wire format."""


@dataclass
class Hits:
    io_channel: np.ndarray
    chip: np.ndarray
    channel: np.ndarray
    adc: np.ndarray
    timestamp: np.ndarray
    receipt_timestamp: np.ndarray
    counters: dict


def odd_parity(payload):
    """True for odd parity across all 64 bits, including bit 63."""
    p = payload.copy()
    for shift in (32, 16, 8, 4, 2, 1):
        p ^= p >> np.uint64(shift)
    return (p & np.uint64(1)) == 1


def decode(message: bytes) -> Hits:
    """Decode a single complete message. No Python objects per word/hit.

    Bad-parity and downstream data are counted but excluded from the display.
    Sync/trigger words are counted separately; they are never treated as hits.
    Timestamps are retained but are not used to align this arrival-time display.
    """
    if len(message) < HEADER.size or len(message) > MAX_MESSAGE:
        raise DecodeError("invalid legacy PACMAN message length")
    kind, _unix_seconds, nwords = HEADER.unpack_from(message)
    if kind != b"D" or len(message) != HEADER.size + nwords * WORD.itemsize:
        raise DecodeError("expected DATA header + exactly N 16-byte words; check wire format")
    words = np.frombuffer(message, dtype=WORD, count=nwords, offset=8)
    kinds = words["kind"]
    data = words[kinds == ord("D")]
    payload = data["payload"]
    is_hit = (payload & np.uint64(3)) == 0
    data = data[is_hit]
    payload = data["payload"]
    parity = odd_parity(payload)
    downstream = ((payload >> np.uint64(62)) & np.uint64(1)) != 0
    accepted = parity & ~downstream
    counters = {
        "words": int(nwords), "data_hits": len(data),
        "bad_parity": int(np.count_nonzero(~parity)),
        "downstream": int(np.count_nonzero(downstream)),
        "other_packets": int(np.count_nonzero(~is_hit)),
        "triggers": int(np.count_nonzero(kinds == ord("T"))),
        "sync": int(np.count_nonzero(kinds == ord("S"))),
        "unknown_words": int(np.count_nonzero(~np.isin(kinds, [ord("D"), ord("T"), ord("S")]))) }
    data = data[accepted]
    p = data["payload"]
    return Hits(data["io"], ((p >> 2) & 255).astype(np.uint8),
                ((p >> 10) & 63).astype(np.uint8),
                ((p >> 48) & 255).astype(np.uint8),
                ((p >> 16) & 0x7fffffff).astype(np.uint32),
                data["receipt"], counters)


def make_message(io, chip, channel, adc=80, timestamp=1234):
    """Build synthetic legacy/v2 upstream hits for tests and offline replay only."""
    io, chip, channel, adc, timestamp = np.broadcast_arrays(io, chip, channel, adc, timestamp)
    if io.size > 65535:
        raise ValueError("one legacy message can hold at most 65535 words")
    if np.any((io < 1) | (io > 32) | (chip < 0) | (chip > 255) |
              (channel < 0) | (channel > 63) | (adc < 0) | (adc > 255)):
        raise ValueError("synthetic field out of range")
    p = ((chip.astype(np.uint64).ravel() << 2) |
         (channel.astype(np.uint64).ravel() << 10) |
         ((timestamp.astype(np.uint64).ravel() & 0x7fffffff) << 16) |
         (adc.astype(np.uint64).ravel() << 48))
    p |= (~odd_parity(p)).astype(np.uint64) << np.uint64(63)
    words = np.zeros(io.size, dtype=WORD)
    words["kind"], words["io"], words["payload"] = ord("D"), io.ravel(), p
    words["receipt"] = 4321
    return HEADER.pack(b"D", 0, io.size) + words.tobytes()
