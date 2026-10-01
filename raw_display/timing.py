"""Streaming ASIC-time unrolling and bounded, detector-time phosphor playback.

Rollover arithmetic follows ndlar_flow RawEventBuilder.unroll_timestamps() at
commit a0eb2f364e35340d67fd73dc09a8e8f847211a58. Unlike the file-local helper,
this object retains the cumulative offset and previous SYNC across batches.
Each instance is ONE IO group. These relative epochs are NOT an absolute
cross-IOG/trigger alignment calibration.
"""
from dataclasses import dataclass
import heapq
import numpy as np
from .post_sync import display_mask, validate_min_raw_timestamp


@dataclass(frozen=True)
class TimingConfig:
    tick_seconds: float = 1e-7
    rollover_ticks: int = 10_000_000
    sync_type: int = 83             # ASCII S, NOT heartbeat H (72)
    playback_delay: float = 1.25
    max_pending_hits: int = 500_000 # per IOG, bounded even if UI/clock stalls
    max_pending_chunks: int = 8192
    min_raw_timestamp: int = 10   # raw ASIC ticks 0..9 are display-vetoed; 0 disables

    def __post_init__(self):
        validate_min_raw_timestamp(self.min_raw_timestamp)
        if not np.isfinite(self.tick_seconds) or self.tick_seconds <= 0:
            raise ValueError('tick_seconds must be finite and positive')
        if not 1 <= self.rollover_ticks <= 2**31:
            raise ValueError('rollover_ticks must be in 1..2**31')
        if not 0 <= self.sync_type <= 255:
            raise ValueError('sync_type must be a byte')
        if not np.isfinite(self.playback_delay) or not 0.05 <= self.playback_delay <= 10:
            raise ValueError('playback_delay must be 0.05..10 seconds')
        if not 1 <= self.max_pending_hits <= 5_000_000 or not 1 <= self.max_pending_chunks <= 65536:
            raise ValueError('pending-buffer limits are out of range')


class TimestampUnroller:
    def __init__(self, config=None):
        self.config = config or TimingConfig()
        self.offset = 0
        self.last_step = self.config.rollover_ticks
        self.initial_tick = None
        self.frontier_tick = None
        self.last_hit_tick = -1
        self.stats = dict(pps_syncs=0, ignored_syncs=0, invalid_syncs=0,
                          missed_periods=0, boundary_corrected=0, warmup_hits=0,
                          invalid_times=0, out_of_order_hits=0)

    def consume(self, hits):
        """Return int64 hit ticks and a timing-valid mask in accepted-hit order.

        Called also on SYNC-only batches. The wire buffer is read, never sorted
        or rewritten before determining which SYNC precedes each hit.
        """
        words = hits.words
        if words is None:
            return np.empty(0, dtype=np.int64), np.empty(0, dtype=bool)
        sync_indices = np.flatnonzero(words['kind'] == ord('S'))
        selected = words['io'][sync_indices] == self.config.sync_type
        self.stats['ignored_syncs'] += int(np.count_nonzero(~selected))
        sync_indices = sync_indices[selected]
        # SYNC timestamp occupies word bytes 4..7, not DATA receipt bytes 2..5.
        aux = np.ndarray((len(words),), dtype='<u4', buffer=words, offset=4,
                         strides=(words.dtype.itemsize,)) if len(words) else np.empty(0, dtype='<u4')
        return self.consume_arrays(hits.timestamp, hits.receipt_timestamp,
                                   hits.word_indices, sync_indices, aux[sync_indices])

    def consume_arrays(self, timestamp, receipt, hit_indices, sync_indices, sync_timestamp):
        """Array API for file validation; indices must share the same wire order."""
        raw = np.asarray(timestamp, dtype=np.int64)
        receipt = np.asarray(receipt, dtype=np.int64)
        hi = np.asarray(hit_indices, dtype=np.int64)
        si = np.asarray(sync_indices, dtype=np.int64)
        st = np.asarray(sync_timestamp, dtype=np.int64)
        if not (len(raw) == len(receipt) == len(hi)) or len(si) != len(st):
            raise ValueError('inconsistent timing array lengths')
        period = self.config.rollover_ticks
        steps = (np.rint(st / period).astype(np.int64) * period)
        # Zero/implausibly phased SYNC is not silently a valid PPS. Fail visible.
        good_sync = (steps > 0) & (np.abs(st - steps) <= max(1, period // 20))
        self.stats['invalid_syncs'] += int(np.count_nonzero(~good_sync))
        si, steps = si[good_sync], steps[good_sync]
        if len(si) > 1 and np.any(np.diff(si) <= 0):
            raise ValueError('SYNC indices must be strictly ordered')
        prefix = np.r_[self.offset, self.offset + np.cumsum(steps, dtype=np.int64)]
        previous_steps = np.r_[self.last_step, steps]
        before = np.searchsorted(si, hi, side='left')
        ready = np.full(len(raw), self.initial_tick is not None, dtype=bool) | (before > 0)
        offsets = prefix[before]
        crossed = receipt < raw
        ticks = raw % period + offsets - crossed * previous_steps[before]
        receipt_ticks = offsets + receipt
        # A corrupted receipt word must not propel playback hundreds of seconds.
        valid = ready & (ticks >= 0) & (receipt_ticks >= ticks) & (receipt < 32 * period)
        self.stats['warmup_hits'] += int(np.count_nonzero(~ready))
        self.stats['invalid_times'] += int(np.count_nonzero(ready & ~valid))
        self.stats['boundary_corrected'] += int(np.count_nonzero(valid & crossed))
        tv = ticks[valid]
        if len(tv):
            prior_max = np.maximum.accumulate(np.r_[self.last_hit_tick, tv])[:-1]
            self.stats['out_of_order_hits'] += int(np.count_nonzero(tv < prior_max))
            self.last_hit_tick = max(self.last_hit_tick, int(tv.max()))
            frontier = int(receipt_ticks[valid].max())
            self.frontier_tick = max(self.frontier_tick or 0, frontier)
        if len(steps):
            if self.initial_tick is None:
                self.initial_tick = int(prefix[1])
            self.offset = int(prefix[-1])
            self.last_step = int(steps[-1])
            self.frontier_tick = max(self.frontier_tick or 0, self.offset)
            self.stats['pps_syncs'] += len(steps)
            self.stats['missed_periods'] += int(np.sum(steps // period - 1))
        return ticks, valid


class DetectorPlayback:
    """Pace an IOG at 1x ASIC time, buffering future hits and aging late hits.

    The monotonic host clock paces presentation ONLY. Never assign its value to
    a hit. Never advance the playhead just because a new receive batch arrives.
    State holds seconds on the IOG-relative detector clock; -1 means unseen.
    """
    def __init__(self, state, config=None):
        self.config = config or TimingConfig()
        self.state = state
        self.unroller = TimestampUnroller(self.config)
        self.cursor = None
        self.running = False
        self.last_wall = None
        self.heap = []
        self.serial = 0
        self.pending_hits = 0
        self.stats = dict(late_hits=0, buffer_dropped_hits=0, underruns=0,
                          pacing_stalls=0, peak_pending_hits=0,
                          pre_cut_late_hits=0, timing_eligible_hits=0,
                          post_sync_filtered_hits=0, display_selected_hits=0)

    @property
    def frontier(self):
        tick = self.unroller.frontier_tick
        return None if tick is None else tick * self.config.tick_seconds

    def ingest(self, ids, hits):
        ticks, valid = self.unroller.consume(hits)
        if len(ids) != len(ticks):
            raise ValueError('geometry IDs and timing must have identical order')
        valid &= np.asarray(ids) >= 0
        # Unroll ALL accepted hits first. A display veto must not change PPS state,
        # receipt frontier, boundary corrections, or the unfiltered late diagnostic.
        self.stats['timing_eligible_hits'] += int(np.count_nonzero(valid))
        if self.cursor is not None:
            limit = int(np.floor(self.cursor / self.config.tick_seconds))
            self.stats['pre_cut_late_hits'] += int(np.count_nonzero(valid & (ticks <= limit)))
        selected = valid & display_mask(ids, hits.timestamp, self.config.min_raw_timestamp)
        self.stats['post_sync_filtered_hits'] += int(np.count_nonzero(valid & ~selected))
        self.stats['display_selected_hits'] += int(np.count_nonzero(selected))
        self.enqueue(np.asarray(ids)[selected], ticks[selected])

    def enqueue(self, ids, ticks):
        """Queue already validated detector ticks; no unbounded waiting/queueing."""
        if not len(ids):
            return
        ids = np.asarray(ids, dtype=np.int32)
        ticks = np.asarray(ticks, dtype=np.int64)
        if self.cursor is not None:
            late = ticks <= int(np.floor(self.cursor / self.config.tick_seconds))
            self.stats['late_hits'] += int(np.count_nonzero(late))
            self._apply(ids[late], ticks[late])
            ids, ticks = ids[~late], ticks[~late]
        if not len(ids):
            return
        if (self.pending_hits + len(ids) > self.config.max_pending_hits or
                len(self.heap) >= self.config.max_pending_chunks):
            self.stats['buffer_dropped_hits'] += len(ids)
            return
        self._push(ids, ticks)
        self.pending_hits += len(ids)
        self.stats['peak_pending_hits'] = max(self.stats['peak_pending_hits'], self.pending_hits)

    def _push(self, ids, ticks):
        self.serial += 1
        heapq.heappush(self.heap, (int(ticks.min()), self.serial, ids, ticks))

    def _apply(self, ids, ticks):
        # Handles both repeated pixels and out-of-order packets correctly.
        np.maximum.at(self.state, ids, ticks * self.config.tick_seconds)

    def step(self, now):
        dt = 0.0 if self.last_wall is None else max(0., now - self.last_wall)
        self.last_wall = now
        frontier = self.frontier
        if frontier is None:
            return
        if self.cursor is None:
            origin = self.unroller.initial_tick * self.config.tick_seconds
            if frontier - origin < self.config.playback_delay:
                return
            self.cursor = origin
            self.running = True
            dt = 0.0
        elif not self.running:
            if frontier - self.cursor < self.config.playback_delay:
                return
            self.running = True
            dt = 0.0
        if dt > 0.5:
            # Never compress a host pause into an apparent simultaneous flash.
            self.stats['pacing_stalls'] += 1
            dt = 0.0
        desired = self.cursor + dt
        if desired > frontier + 1e-9:
            self.cursor = frontier
            self.running = False
            self.stats['underruns'] += 1
        else:
            self.cursor = min(desired, frontier)
        limit = int(np.floor(self.cursor / self.config.tick_seconds + 1e-5))
        while self.heap and self.heap[0][0] <= limit:
            _, _, ids, ticks = heapq.heappop(self.heap)
            due = ticks <= limit
            self._apply(ids[due], ticks[due])
            self.pending_hits -= int(np.count_nonzero(due))
            if not np.all(due):
                self._push(ids[~due], ticks[~due])

    def summary(self):
        return dict(cursor_s=-1.0 if self.cursor is None else self.cursor,
                    frontier_s=-1.0 if self.frontier is None else self.frontier,
                    running=int(self.running), synchronized=int(self.unroller.initial_tick is not None),
                    pending_hits=self.pending_hits, **self.stats, **self.unroller.stats)


TIMING_FIELDS = tuple(DetectorPlayback(np.empty(0)).summary())
TIMING_COL = {key: i for i, key in enumerate(TIMING_FIELDS)}
