"""Opt-in, bounded diagnostics from the EXISTING collector. No ZMQ or disk I/O.

The observation clock is the collector's batch-consumption clock, NOT ASIC time.
Rows use the exact publication boundaries of the phosphor state (nominal 100 ms).
An HTTP reader can lose diagnostic rows; it must never hold up the collector.
"""
from dataclasses import dataclass
import os
import time
import uuid
import numpy as np

CAPACITY = 600
SCALARS = ("seq", "begin_monotonic", "end_monotonic", "cpu_seconds",
           "max_loop_gap_s", "skipped_rows_total")
SOURCE_FIELDS = ("messages", "bytes", "data_hits", "valid_data_hits", "mapped_hits",
                 "unmapped_hits", "bad_parity", "malformed", "sync", "triggers",
                 "batches", "full_drains", "max_drain_s", "max_decode_map_s")
WIDTH = len(SCALARS) + 64 + 64 + 8 * len(SOURCE_FIELDS)


@dataclass
class RateRing:
    rows: object
    latest: object
    lock: object
    boot_id: str


def make_rate_ring(ctx):
    if os.environ.get("RAW_DISPLAY_RATE_AUDIT", "0") != "1":
        return None
    return RateRing(ctx.RawArray("d", CAPACITY * WIDTH), ctx.RawValue("q", 0),
                    ctx.Lock(), uuid.uuid4().hex)


class RateAudit:
    def __init__(self, geometry, ring, metric_columns):
        self.ring = ring
        self.cols = metric_columns
        px = geometry.pixels
        self.tile_code = (px["iog"].astype(np.int64)-1)*8 + px["tile"].astype(np.int64)-1
        self.active = np.zeros(len(px), dtype=bool)
        self.counts = np.zeros(64, dtype=np.float64)
        self.extra = np.zeros((8, 4), dtype=np.float64)
        self.previous = np.zeros((9, len(metric_columns)), dtype=np.float64)
        self.begin = self.loop_at = time.monotonic()
        self.cpu_at = time.process_time()
        self.max_gap = 0.0
        self.seq = self.skipped = 0

    def loop(self, now):
        self.max_gap = max(self.max_gap, now-self.loop_at)
        self.loop_at = now

    def record(self, iog, ids, nframes, batch_limit, drain_s, decode_map_s):
        # Each mapped packet counts, including repeated hits on one pixel.
        ids = ids[ids >= 0]
        self.counts += np.bincount(self.tile_code[ids], minlength=64)
        self.active[ids] = True
        x = self.extra[iog-1]
        x[0] += 1
        x[1] += nframes >= batch_limit  # A full drain is NOT a measured queue length.
        x[2] = max(x[2], drain_s)
        x[3] = max(x[3], decode_map_s)

    def publish(self, now, stats):
        """Called at exactly the existing display publication boundary."""
        if now <= self.begin:
            return
        self.seq += 1
        cpu = time.process_time()
        unique = np.bincount(self.tile_code[self.active], minlength=64)
        source = np.zeros((8, len(SOURCE_FIELDS)), dtype=np.float64)
        for j, name in enumerate(SOURCE_FIELDS[:-4]):
            source[:, j] = stats[1:, self.cols[name]]-self.previous[1:, self.cols[name]]
        source[:, -4:] = self.extra
        row = np.concatenate(([self.seq, self.begin, now, cpu-self.cpu_at,
                               self.max_gap, self.skipped], self.counts, unique, source.ravel()))
        # Deliberately skip a diagnostics row rather than delay the SUB collector.
        if self.ring.lock.acquire(False):
            try:
                dest = np.frombuffer(self.ring.rows).reshape(CAPACITY, WIDTH)
                dest[(self.seq-1) % CAPACITY] = row
                self.ring.latest.value = self.seq
            finally:
                self.ring.lock.release()
        else:
            self.skipped += 1
        self.begin, self.cpu_at = now, cpu
        self.previous[:] = stats
        self.max_gap = 0.0
        self.counts.fill(0)
        self.active.fill(False)
        self.extra.fill(0)


def read_rate_ring(ring, after=0, limit=100):
    if ring is None:
        return {"enabled": False, "hint": "Start the existing serve command with RAW_DISPLAY_RATE_AUDIT=1"}
    after, limit = int(after), int(limit)
    if after < 0 or not 1 <= limit <= 100:
        raise ValueError("after must be nonnegative; limit must be 1..100")
    if not ring.lock.acquire(timeout=0.05):
        return {"enabled": True, "boot_id": ring.boot_id, "busy": True, "samples": []}
    try:
        src = np.frombuffer(ring.rows).reshape(CAPACITY, WIDTH)
        mask = src[:, 0] > after
        found = np.flatnonzero(mask)
        found = found[np.argsort(src[found, 0])][:limit]
        rows = src[found].copy()
        latest = ring.latest.value
        seqs = src[src[:, 0] > 0, 0]
        oldest = int(seqs.min()) if len(seqs) else 0
    finally:
        ring.lock.release()
    return {"enabled": True, "boot_id": ring.boot_id, "latest": latest,
            "oldest": oldest, "capacity": CAPACITY,
            "scalars": list(SCALARS), "source_fields": list(SOURCE_FIELDS),
            "tile_order": "index=(io_group-1)*8+local_tile-1",
            "clock": "host batch consumption; NOT ASIC or FPGA event time",
            "binning": "exact collector display-publication intervals; nominal 0.1 s; use actual end-begin",
            "samples": rows.tolist()}
