"""Optional latest-hit/t0 pairs paced by the existing IOG playhead.

Store the exact integer difference (hit_tick - t0_tick), equivalent to retaining
t0 alongside the existing local hit time. Unix/NTP labels never enter here.
"""
import heapq
import struct
import numpy as np
from .trigger_windows import DueLayer

HEADER = struct.Struct('<4sII')
RECORD = np.dtype([('id', '<u4'), ('source', '<u4'), ('hit_s', '<f8'), ('dt_ticks', '<i8')])


class PairedDueLayer(DueLayer):
    def __init__(self, state, drift_ticks, timing, config):
        super().__init__(state, timing, config)
        self.drift_ticks = drift_ticks

    def apply(self, ids, ticks, delta):
        if not len(ids):
            return
        # Lexicographic maximum: greatest hit tick, then greatest qualifying t0.
        # Invalid/pre-window associations lose ties to valid nonnegative drift.
        tie = np.where(delta >= 0, -delta, np.iinfo(np.int64).min)
        order = np.lexsort((tie, ticks, ids))
        keep = order[np.r_[ids[order][1:] != ids[order][:-1], True]]
        ids, ticks, delta = ids[keep], ticks[keep], delta[keep]
        seconds = ticks * self.timing.tick_seconds
        old, old_delta = self.state[ids], self.drift_ticks[ids]
        update = (seconds > old) | ((seconds == old) & (delta >= 0) & ((old_delta < 0) | (delta < old_delta)))
        ids = ids[update]
        self.state[ids], self.drift_ticks[ids] = seconds[update], delta[update]

    def add(self, ids, ticks, delta):
        if not len(ids):
            return
        ids, ticks, delta = np.asarray(ids, np.int32), np.asarray(ticks, np.int64), np.asarray(delta, np.int64)
        due = ticks <= self.cursor_tick
        self.apply(ids[due], ticks[due], delta[due])
        ids, ticks, delta = ids[~due], ticks[~due], delta[~due]
        if not len(ids):
            return
        if self.pending + len(ids) > self.config.max_history_hits or len(self.heap) >= self.config.max_chunks:
            self.dropped_hits += len(ids)
            return
        self._push(ids, ticks, delta)
        self.pending += len(ids)

    def _push(self, ids, ticks, delta):
        self.serial += 1
        heapq.heappush(self.heap, (int(ticks.min()), self.serial, ids, ticks, delta))

    def step(self, cursor):
        if cursor is None:
            return
        self.cursor_tick = int(np.floor(cursor / self.timing.tick_seconds + 1e-5))
        while self.heap and self.heap[0][0] <= self.cursor_tick:
            _, _, ids, ticks, delta = heapq.heappop(self.heap)
            due = ticks <= self.cursor_tick
            self.apply(ids[due], ticks[due], delta[due])
            self.pending -= int(np.count_nonzero(due))
            if not np.all(due):
                self._push(ids[~due], ticks[~due], delta[~due])


def encode_drift_frame(previous, current, sequence):
    """R3D1: 12-byte header, 24-byte records. Source 0=Beam, 1=Light.

    current/previous map source -> (hit_seconds, dt_ticks). Hit seconds are local
    IOG values, dt ticks are int64 differences, never Unix-scale tick numbers.
    Canonical RDP2 owns the ACK. -1 drift means no valid preceding trigger.
    """
    parts = []
    for source_id, name in enumerate(('beam', 'light')):
        hits, delta = current[name]
        prior_hits, prior_delta = previous[name]
        ids = np.flatnonzero((hits != prior_hits) | (delta != prior_delta))
        records = np.empty(len(ids), dtype=RECORD)
        records['id'], records['source'] = ids, source_id
        records['hit_s'], records['dt_ticks'] = hits[ids], delta[ids]
        parts.append(records)
    records = np.concatenate(parts)
    return HEADER.pack(b'R3D1', sequence, len(records)) + records.tobytes()
