"""Vectorized PACMAN envelope + LArPix payload decoder for the 2x2 display.

Supported PACMAN envelopes:
- legacy: 8-byte header + N 16-byte words
- new-v1: 24-byte header + N 24-byte words (64-bit PACMAN timestamps)

ASIC payload interpretation is explicit: v2/v2b/v2d use Packet_v2 semantics;
v3 uses Packet_v3 semantics. Direction is diagnostic only: valid data packets in
both directions are displayable, matching PacMon's ADC/rate path.
"""
from dataclasses import dataclass
import struct
import numpy as np

LEGACY_HEADER = struct.Struct("<cIxH")
LEGACY_WORD = np.dtype({"names": ["kind", "io", "receipt", "payload"],
                        "formats": ["u1", "u1", "<u4", "<u8"],
                        "offsets": [0, 1, 2, 8], "itemsize": 16})
NEW_HEADER = struct.Struct("<cBBBIQ8x")
NEW_WORD = np.dtype({"names": ["kind", "pacman", "io", "receipt", "payload"],
                     "formats": ["u1", "u1", "<u2", "<u8", "<u8"],
                     "offsets": [0, 1, 2, 8, 16], "itemsize": 24})
# Backward-compatible aliases used by tests/older local scripts.
HEADER, WORD = LEGACY_HEADER, LEGACY_WORD
MAX_MESSAGE = max(LEGACY_HEADER.size + 65535 * LEGACY_WORD.itemsize,
                  NEW_HEADER.size + 65535 * NEW_WORD.itemsize)

DECODE_COUNTERS = (
    "words", "data_words", "cfg_words", "data_hits", "valid_data_hits",
    "bad_parity", "downstream", "upstream", "other_packets",
    "packet_type_0", "packet_type_1", "packet_type_2", "packet_type_3",
    "triggers", "sync", "unknown_words", "nondata_messages",
    "legacy_messages", "new24_messages",
)


class DecodeError(ValueError):
    """A complete ZMQ message does not match a supported PACMAN wire format."""


@dataclass
class Hits:
    io_channel: np.ndarray
    chip: np.ndarray
    channel: np.ndarray
    adc: np.ndarray
    timestamp: np.ndarray
    receipt_timestamp: np.ndarray
    counters: dict
    envelope: str
    asic_version: int


def normalize_asic_version(value):
    """Map CRS config labels onto the two distinct 64-bit packet layouts."""
    s = str(value).strip().lower()
    if s.startswith("v"):
        s = s[1:]
    if s in {"2", "2a", "2b", "2d", "lightpix-1"}:
        return 2
    if s in {"3", "3a"}:
        return 3
    raise ValueError(f"unsupported ASIC version {value!r}; expected 2/2a/2b/2d or 3/3a")


def odd_parity(payload):
    """True for valid LArPix odd parity across all 64 bits, including bit 63."""
    p = payload.copy()
    for shift in (32, 16, 8, 4, 2, 1):
        p ^= p >> np.uint64(shift)
    return (p & np.uint64(1)) == 1


def _empty(counters, envelope, asic_version):
    u8 = np.empty(0, dtype=np.uint8)
    u16 = np.empty(0, dtype=np.uint16)
    u32 = np.empty(0, dtype=np.uint32)
    u64 = np.empty(0, dtype=np.uint64)
    return Hits(u16, u8, u8, u16, u32, u64, counters, envelope, asic_version)


def _classify(message):
    """Return (envelope, message_type, words) using strict, unambiguous lengths."""
    legacy = None
    if len(message) >= LEGACY_HEADER.size:
        kind, _, nwords = LEGACY_HEADER.unpack_from(message)
        if kind in (b"D", b"?", b"!") and len(message) == LEGACY_HEADER.size + nwords * LEGACY_WORD.itemsize:
            legacy = ("legacy16", kind, np.frombuffer(message, dtype=LEGACY_WORD,
                                                       count=nwords, offset=LEGACY_HEADER.size))

    new = None
    if len(message) >= NEW_HEADER.size:
        kind, _pacman, major, minor, nbytes, _timestamp = NEW_HEADER.unpack_from(message)
        expected = NEW_HEADER.size + nbytes
        if kind in (b"D", b"?", b"!", b"S") and major == 1 and minor == 0 and len(message) == expected:
            if kind == b"S":
                new = ("new24", kind, None)
            elif nbytes % NEW_WORD.itemsize == 0:
                new = ("new24", kind, np.frombuffer(message, dtype=NEW_WORD,
                                                      count=nbytes // NEW_WORD.itemsize,
                                                      offset=NEW_HEADER.size))

    if legacy and new:
        raise DecodeError("message is ambiguously valid as both legacy16 and new24")
    if legacy:
        return legacy
    if new:
        return new

    hint = ""
    if len(message) >= NEW_HEADER.size and message[:1] in (b"D", b"?", b"!", b"S"):
        try:
            _, pacman, major, minor, nbytes, _ = NEW_HEADER.unpack_from(message)
            hint = f"; new24-like header pacman={pacman} version={major}.{minor} n_bytes={nbytes}"
        except struct.error:
            pass
    raise DecodeError(f"unsupported PACMAN frame length/type (len={len(message)}){hint}")


def decode(message: bytes, asic_version=2) -> Hits:
    """Decode one complete PACMAN message with no Python object per hit.

    Valid-parity data packets are accepted regardless of upstream/downstream marker.
    The marker remains in counters for diagnostics, matching PacMon's rate/ADC path.
    """
    if not isinstance(message, (bytes, bytearray, memoryview)):
        raise DecodeError("message must be bytes-like")
    if len(message) > MAX_MESSAGE:
        raise DecodeError(f"PACMAN frame exceeds safety limit ({len(message)} bytes)")
    asic_version = normalize_asic_version(asic_version)
    envelope, msg_type, words = _classify(message)
    counters = {name: 0 for name in DECODE_COUNTERS}
    counters["legacy_messages" if envelope == "legacy16" else "new24_messages"] = 1

    if msg_type != b"D":
        counters["nondata_messages"] = 1
        return _empty(counters, envelope, asic_version)

    nwords = len(words)
    counters["words"] = nwords
    kinds = words["kind"]
    counters["triggers"] = int(np.count_nonzero(kinds == ord("T")))
    counters["sync"] = int(np.count_nonzero(kinds == ord("S")))
    counters["cfg_words"] = int(np.count_nonzero(kinds == ord("C")))
    known = np.isin(kinds, [ord(x) for x in "DCTSPWRE"])
    counters["unknown_words"] = int(np.count_nonzero(~known))

    data = words[kinds == ord("D")]
    counters["data_words"] = len(data)
    if not len(data):
        return _empty(counters, envelope, asic_version)

    payload = data["payload"]
    ptype = (payload & np.uint64(3)).astype(np.uint8)
    for value in range(4):
        counters[f"packet_type_{value}"] = int(np.count_nonzero(ptype == value))

    expected_type = 0 if asic_version == 2 else 1
    is_hit = ptype == expected_type
    counters["data_hits"] = int(np.count_nonzero(is_hit))
    counters["other_packets"] = int(np.count_nonzero(~is_hit))
    data = data[is_hit]
    payload = data["payload"]
    if not len(data):
        return _empty(counters, envelope, asic_version)

    parity = odd_parity(payload)
    downstream = ((payload >> np.uint64(62)) & np.uint64(1)) != 0
    counters["valid_data_hits"] = int(np.count_nonzero(parity))
    counters["bad_parity"] = int(np.count_nonzero(~parity))
    counters["downstream"] = int(np.count_nonzero(downstream))
    counters["upstream"] = int(np.count_nonzero(~downstream))

    # Match PacMon: direction is recorded, not rejected. Only parity gates display hits.
    data = data[parity]
    p = data["payload"]
    if asic_version == 2:
        adc = ((p >> np.uint64(48)) & np.uint64(0xff)).astype(np.uint16)
        timestamp = ((p >> np.uint64(16)) & np.uint64(0x7fffffff)).astype(np.uint32)
    else:
        adc = ((p >> np.uint64(46)) & np.uint64(0x3ff)).astype(np.uint16)
        timestamp = ((p >> np.uint64(16)) & np.uint64(0x0fffffff)).astype(np.uint32)
    return Hits(data["io"].astype(np.uint16, copy=False),
                ((p >> np.uint64(2)) & np.uint64(255)).astype(np.uint8),
                ((p >> np.uint64(10)) & np.uint64(63)).astype(np.uint8),
                adc, timestamp, data["receipt"].astype(np.uint64, copy=False),
                counters, envelope, asic_version)


def make_message(io, chip, channel, adc=80, timestamp=1234, *,
                 asic_version=2, envelope="legacy16", downstream=False):
    """Build synthetic data messages for tests/benchmark only."""
    asic_version = normalize_asic_version(asic_version)
    io, chip, channel, adc, timestamp, downstream = np.broadcast_arrays(
        io, chip, channel, adc, timestamp, downstream)
    if io.size > 65535:
        raise ValueError("one synthetic message can hold at most 65535 words")
    adc_max = 255 if asic_version == 2 else 1023
    if np.any((io < 1) | (io > 65535) | (chip < 0) | (chip > 255) |
              (channel < 0) | (channel > 63) | (adc < 0) | (adc > adc_max)):
        raise ValueError("synthetic field out of range")
    ptype = 0 if asic_version == 2 else 1
    p = (np.uint64(ptype) |
         (chip.astype(np.uint64).ravel() << np.uint64(2)) |
         (channel.astype(np.uint64).ravel() << np.uint64(10)))
    if asic_version == 2:
        p |= ((timestamp.astype(np.uint64).ravel() & np.uint64(0x7fffffff)) << np.uint64(16))
        p |= (adc.astype(np.uint64).ravel() << np.uint64(48))
    else:
        p |= ((timestamp.astype(np.uint64).ravel() & np.uint64(0x0fffffff)) << np.uint64(16))
        p |= (adc.astype(np.uint64).ravel() << np.uint64(46))
    p |= downstream.astype(np.uint64).ravel() << np.uint64(62)
    p |= (~odd_parity(p)).astype(np.uint64) << np.uint64(63)

    if envelope == "legacy16":
        if np.any(io > 255):
            raise ValueError("legacy PACMAN io_channel is one byte")
        words = np.zeros(io.size, dtype=LEGACY_WORD)
        words["kind"], words["io"], words["payload"] = ord("D"), io.ravel(), p
        words["receipt"] = 4321
        return LEGACY_HEADER.pack(b"D", 0, io.size) + words.tobytes()
    if envelope == "new24":
        words = np.zeros(io.size, dtype=NEW_WORD)
        words["kind"], words["pacman"], words["io"] = ord("D"), 0, io.ravel()
        words["receipt"], words["payload"] = np.uint64(2**40 + 4321), p
        return NEW_HEADER.pack(b"D", 0, 1, 0, words.nbytes, 0) + words.tobytes()
    raise ValueError("envelope must be 'legacy16' or 'new24'")
