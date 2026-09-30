"""Fast fixed-format decoder for the current 2x2 PACMAN stream.

This display intentionally targets the Run-3 2x2 data path:
- legacy PACMAN envelope: 8-byte header + N 16-byte words
- Packet_v2 payload semantics (also used by v2b/v2d ASIC configurations)

The hot path batches many small ZMQ messages before NumPy decoding. This avoids
paying NumPy/Python setup costs once per ~5-10-word PACMAN message.
"""
from dataclasses import dataclass
import struct
import numpy as np

HEADER = struct.Struct("<cIxH")
WORD = np.dtype({"names": ["kind", "io", "receipt", "payload"],
                 "formats": ["u1", "u1", "<u4", "<u8"],
                 "offsets": [0, 1, 2, 8], "itemsize": 16})
MAX_MESSAGE = HEADER.size + 65535 * WORD.itemsize

DECODE_COUNTERS = (
    "words", "data_words", "data_hits", "valid_data_hits",
    "bad_parity", "downstream", "upstream", "other_packets",
    "packet_type_0", "packet_type_1", "packet_type_2", "packet_type_3",
    "triggers", "sync", "unknown_words", "nondata_messages",
    "legacy_messages", "malformed",
)


class DecodeError(ValueError):
    """A complete ZMQ message is not a supported legacy PACMAN frame."""


@dataclass
class Hits:
    io_channel: np.ndarray
    chip: np.ndarray
    channel: np.ndarray
    adc: np.ndarray
    timestamp: np.ndarray
    receipt_timestamp: np.ndarray
    counters: dict
    diagnostic: str | None = None


def odd_parity(payload):
    """True for valid LArPix odd parity across all 64 bits, including bit 63."""
    p = payload.copy()
    for shift in (32, 16, 8, 4, 2, 1):
        p ^= p >> np.uint64(shift)
    return (p & np.uint64(1)) == 1


def _empty(counters, diagnostic=None):
    return Hits(
        np.empty(0, dtype=np.uint8),
        np.empty(0, dtype=np.uint8),
        np.empty(0, dtype=np.uint8),
        np.empty(0, dtype=np.uint8),
        np.empty(0, dtype=np.uint32),
        np.empty(0, dtype=np.uint32),
        counters,
        diagnostic,
    )


def _legacy_header(message):
    if not isinstance(message, (bytes, bytearray, memoryview)):
        raise DecodeError("message must be bytes-like")
    if len(message) < HEADER.size or len(message) > MAX_MESSAGE:
        raise DecodeError(f"invalid legacy PACMAN message length {len(message)}")
    kind, _seconds, nwords = HEADER.unpack_from(message)
    if kind not in (b"D", b"?", b"!"):
        raise DecodeError(f"unsupported legacy PACMAN message type {kind!r}")
    expected = HEADER.size + nwords * WORD.itemsize
    if len(message) != expected:
        raise DecodeError(
            f"legacy PACMAN length mismatch: header says {nwords} words "
            f"({expected} bytes total), got {len(message)}"
        )
    return kind, nwords


def decode_batch(messages, *, strict=False):
    """Decode a batch of legacy PACMAN messages as Packet_v2.

    Malformed frames are skipped when strict=False so one bad diagnostic/control
    frame cannot discard good detector data in the same receive batch.

    Valid-parity data are accepted regardless of the downstream marker, matching
    PacMon's ADC/rate path. Direction remains available in counters.
    """
    counters = {name: 0 for name in DECODE_COUNTERS}
    bodies = []
    first_error = None

    for message in messages:
        try:
            kind, nwords = _legacy_header(message)
        except DecodeError as exc:
            if strict:
                raise
            counters["malformed"] += 1
            if first_error is None:
                raw = bytes(message) if isinstance(message, (bytes, bytearray, memoryview)) else b""
                first_error = f"{exc}; len={len(raw)} prefix32={raw[:32].hex()}"
            continue

        counters["legacy_messages"] += 1
        if kind != b"D":
            counters["nondata_messages"] += 1
            continue

        counters["words"] += nwords
        if nwords:
            bodies.append(memoryview(message)[HEADER.size:])

    if not bodies:
        return _empty(counters, first_error)

    body = bodies[0] if len(bodies) == 1 else b"".join(bodies)
    words = np.frombuffer(body, dtype=WORD)
    kinds = words["kind"]

    counters["triggers"] = int(np.count_nonzero(kinds == ord("T")))
    counters["sync"] = int(np.count_nonzero(kinds == ord("S")))
    known = np.isin(kinds, [ord(x) for x in "DTSPWRE"])
    counters["unknown_words"] = int(np.count_nonzero(~known))

    data = words[kinds == ord("D")]
    counters["data_words"] = len(data)
    if not len(data):
        return _empty(counters, first_error)

    payload = data["payload"]
    ptype = (payload & np.uint64(3)).astype(np.uint8)
    for value in range(4):
        counters[f"packet_type_{value}"] = int(np.count_nonzero(ptype == value))

    # Packet_v2 DATA_PACKET == 0. v2b/v2d use the same packet wire layout.
    is_hit = ptype == 0
    counters["data_hits"] = int(np.count_nonzero(is_hit))
    counters["other_packets"] = int(np.count_nonzero(~is_hit))
    data = data[is_hit]
    payload = data["payload"]
    if not len(data):
        return _empty(counters, first_error)

    parity = odd_parity(payload)
    downstream = ((payload >> np.uint64(62)) & np.uint64(1)) != 0
    counters["valid_data_hits"] = int(np.count_nonzero(parity))
    counters["bad_parity"] = int(np.count_nonzero(~parity))
    counters["downstream"] = int(np.count_nonzero(downstream))
    counters["upstream"] = int(np.count_nonzero(~downstream))

    data = data[parity]
    p = data["payload"]
    return Hits(
        data["io"],
        ((p >> np.uint64(2)) & np.uint64(255)).astype(np.uint8),
        ((p >> np.uint64(10)) & np.uint64(63)).astype(np.uint8),
        ((p >> np.uint64(48)) & np.uint64(255)).astype(np.uint8),
        ((p >> np.uint64(16)) & np.uint64(0x7fffffff)).astype(np.uint32),
        data["receipt"],
        counters,
        first_error,
    )


def decode(message):
    """Strict single-message decoder retained for tests and diagnostics."""
    return decode_batch([message], strict=True)


def make_message(io, chip, channel, adc=80, timestamp=1234, *, downstream=False):
    """Build synthetic legacy/Packet_v2 DATA messages for tests/benchmark only."""
    io, chip, channel, adc, timestamp, downstream = np.broadcast_arrays(
        io, chip, channel, adc, timestamp, downstream
    )
    if io.size > 65535:
        raise ValueError("one synthetic legacy message can hold at most 65535 words")
    if np.any((io < 1) | (io > 255) | (chip < 0) | (chip > 255) |
              (channel < 0) | (channel > 63) | (adc < 0) | (adc > 255)):
        raise ValueError("synthetic field out of range")

    p = ((chip.astype(np.uint64).ravel() << np.uint64(2)) |
         (channel.astype(np.uint64).ravel() << np.uint64(10)) |
         ((timestamp.astype(np.uint64).ravel() & np.uint64(0x7fffffff)) << np.uint64(16)) |
         (adc.astype(np.uint64).ravel() << np.uint64(48)) |
         (downstream.astype(np.uint64).ravel() << np.uint64(62)))
    p |= (~odd_parity(p)).astype(np.uint64) << np.uint64(63)

    words = np.zeros(io.size, dtype=WORD)
    words["kind"] = ord("D")
    words["io"] = io.astype(np.uint8).ravel()
    words["receipt"] = 4321
    words["payload"] = p
    return HEADER.pack(b"D", 0, io.size) + words.tobytes()
