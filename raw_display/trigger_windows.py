"""Bounded, same-IOG trigger-window tagging; never changes charge timestamps.

This is temporal coincidence, NOT a causal assignment or a complete event builder.
No cross-IOG epoch is guessed. All arrays are for one IO group.
"""
from collections import deque
from dataclasses import dataclass
import heapq
import math
import os
import numpy as np


@dataclass(frozen=True)
class ObserverConfig:
    trigger_view: bool = False
    pre_us: float = 0.0
    post_us: float = 300.0
    history_seconds: float = 2.0
    max_history_hits: int = 500_000
    max_chunks: int = 4096
    max_windows: int = 2048
    trigger_types: tuple = ()  # empty means any PACMAN T-word subtype
    audit_iogs: tuple = ()

    def __post_init__(self):
        if not (math.isfinite(self.pre_us) and 0 <= self.pre_us <= 100_000):
            raise ValueError('trigger pre-window must be 0..100000 microseconds')
        if not (math.isfinite(self.post_us) and 0 < self.post_us <= 100_000):
            raise ValueError('trigger post-window must be >0..100000 microseconds')
        if not math.isfinite(self.history_seconds) or not .1 <= self.history_seconds <= 10:
            raise ValueError('trigger history must be 0.1..10 seconds')
        if not 1 <= self.max_history_hits <= 2_000_000 or not 1 <= self.max_chunks <= 8192:
            raise ValueError('invalid trigger buffer limit')
        if not 1 <= self.max_windows <= 8192:
            raise ValueError('invalid trigger window limit')
        if any(type(t) is not int or not 0 <= t <= 255 for t in self.trigger_types):
            raise ValueError('trigger subtypes must be bytes')
        if any(type(i) is not int or i not in range(1, 9) for i in self.audit_iogs):
            raise ValueError('timing-audit IO groups must be 1..8')

    @classmethod
    def from_env(cls):
        enabled = os.environ.get('RAW_DISPLAY_TRIGGER_VIEW', '0')
        if enabled not in ('0', '1'):
            raise ValueError('RAW_DISPLAY_TRIGGER_VIEW must be 0 or 1')
        def integers(key):
            text = os.environ.get(key, '').strip()
            return tuple(sorted(set(int(v.strip(), 0) for v in text.split(',')))) if text else ()
        return cls(trigger_view=enabled == '1',
                   pre_us=float(os.environ.get('RAW_DISPLAY_TRIGGER_PRE_US', '0')),
                   post_us=float(os.environ.get('RAW_DISPLAY_TRIGGER_POST_US', '300')),
                   history_seconds=float(os.environ.get('RAW_DISPLAY_TRIGGER_HISTORY_S', '2')),
                   max_history_hits=int(os.environ.get('RAW_DISPLAY_TRIGGER_MAX_HITS', '500000')),
                   trigger_types=integers('RAW_DISPLAY_TRIGGER_TYPES'),
                   audit_iogs=integers('RAW_DISPLAY_LATE_AUDIT_IOGS'))


def batch_context(hits, initial, timing_config):
    """Recover the canonical unroller's within-batch epoch mapping, without mutation.

    initial=(offset, has_seen_pps), captured BEFORE the canonical ingest call.
    Uses exactly its valid-SYNC rule. No extra hit decoding or wall time.
    """
    words = hits.words
    if words is None or not len(words):
        return np.empty(0, dtype=np.int64), np.array([initial[0]], dtype=np.int64)
    sync_i = np.flatnonzero((words['kind'] == ord('S')) &
                           (words['io'] == timing_config.sync_type))
    aux = np.ndarray((len(words),), dtype='<u4', buffer=words, offset=4,
                     strides=(words.dtype.itemsize,))
    period = timing_config.rollover_ticks
    ts = aux[sync_i].astype(np.int64)
    steps = np.rint(ts / period).astype(np.int64) * period
    ok = (steps > 0) & (np.abs(ts - steps) <= max(1, period // 20))
    sync_i, steps = sync_i[ok], steps[ok]
    return sync_i, np.r_[initial[0], initial[0] + np.cumsum(steps, dtype=np.int64)]


def project_triggers(hits, initial, timing_config, context=None):
    """Return T-word subtypes, unrolled ticks, and validity in original order.

    T timestamp is the PACMAN counter at word bytes 4..7, NOT DATA receipt bytes
    2..5. Flow-style epoch projection uses preceding SYNCs and raw % period.
    Unknown pre-PPS epochs and trigger counters >= one reset period are rejected:
    lost-reset trigger epochs need explicit validation, not a guessed modulo.
    """
    words = hits.words
    if words is None or not hits.counters.get('triggers', 0):
        return np.empty(0, dtype=np.uint8), np.empty(0, dtype=np.int64), np.empty(0, dtype=bool)
    ti = np.flatnonzero(words['kind'] == ord('T'))
    aux = np.ndarray((len(words),), dtype='<u4', buffer=words, offset=4,
                     strides=(words.dtype.itemsize,))
    raw = aux[ti].astype(np.int64)
    si, prefix = context if context is not None else batch_context(hits, initial, timing_config)
    preceding = np.searchsorted(si, ti, side='left')
    ready = bool(initial[1]) | (preceding > 0)
    ticks = prefix[preceding] + raw
    valid = ready & (raw < timing_config.rollover_ticks) & (ticks >= 0)
    return words['io'][ti], ticks, valid


class DueLayer:
    """A second last-hit layer advanced ONLY by the canonical playhead."""
    def __init__(self, state, timing_config, config):
        self.state, self.timing, self.config = state, timing_config, config
        self.heap, self.serial, self.pending = [], 0, 0
        self.dropped_hits = 0
        self.cursor_tick = -1

    def apply(self, ids, ticks):
        np.maximum.at(self.state, ids, ticks * self.timing.tick_seconds)

    def add(self, ids, ticks):
        if not len(ids):
            return
        ids, ticks = np.asarray(ids, dtype=np.int32), np.asarray(ticks, dtype=np.int64)
        due = ticks <= self.cursor_tick
        self.apply(ids[due], ticks[due])
        ids, ticks = ids[~due], ticks[~due]
        if not len(ids):
            return
        if self.pending + len(ids) > self.config.max_history_hits or len(self.heap) >= self.config.max_chunks:
            self.dropped_hits += len(ids)
            return
        self.serial += 1
        heapq.heappush(self.heap, (int(ticks.min()), self.serial, ids, ticks))
        self.pending += len(ids)

    def step(self, cursor):
        if cursor is None:
            return
        self.cursor_tick = int(np.floor(cursor / self.timing.tick_seconds + 1e-5))
        while self.heap and self.heap[0][0] <= self.cursor_tick:
            _, _, ids, ticks = heapq.heappop(self.heap)
            due = ticks <= self.cursor_tick
            self.apply(ids[due], ticks[due])
            self.pending -= int(np.count_nonzero(due))
            if not np.all(due):
                self.serial += 1
                heapq.heappush(self.heap, (int(ticks[~due].min()), self.serial, ids[~due], ticks[~due]))


class TriggerWindows:
    def __init__(self, state, timing_config, config):
        self.config, self.timing = config, timing_config
        self.pre = int(round(config.pre_us * 1e-6 / timing_config.tick_seconds))
        self.post = max(1, int(round(config.post_us * 1e-6 / timing_config.tick_seconds)))
        self.history_ticks = int(round(config.history_seconds / timing_config.tick_seconds))
        self.history, self.n_history = deque(), 0
        self.windows = []
        self.layer = DueLayer(state, timing_config, config)
        self.frontier = -1
        self.first_tick = None
        self.last_trim = -1
        self.stats = dict(selected_triggers=0, invalid_trigger_times=0,
                          matched_hits=0, outside_history_triggers=0,
                          history_capacity_dropped_hits=0, window_limit_drops=0)

    def _mask(self, ticks):
        if not self.windows or not len(ticks):
            return np.zeros(len(ticks), dtype=bool)
        windows = np.asarray(self.windows, dtype=np.int64)
        which = np.searchsorted(windows[:, 0], ticks, side='right') - 1
        return (which >= 0) & (ticks < windows[np.maximum(which, 0), 1])

    def _merge_windows(self):
        merged = []
        for lo, hi in sorted(self.windows):
            if merged and lo <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(hi, merged[-1][1]))
            else:
                merged.append((lo, hi))
        if len(merged) > self.config.max_windows:
            self.stats['window_limit_drops'] += len(merged) - self.config.max_windows
            merged = merged[-self.config.max_windows:]
        self.windows = merged

    def _trim(self, frontier):
        self.frontier = max(self.frontier, int(frontier))
        cutoff = self.frontier - self.history_ticks
        # Expiration scans at most 10 times per detector second, not once per packet.
        stride = max(1, int(.1 / self.timing.tick_seconds))
        if self.last_trim >= 0 and self.frontier - self.last_trim < stride:
            return cutoff
        kept = deque()
        count = 0
        for ids, ticks, matched in self.history:
            if int(ticks.max()) >= cutoff:
                kept.append((ids, ticks, matched))
                count += len(ids)
        self.history, self.n_history = kept, count
        self.windows = [(lo, hi) for lo, hi in self.windows if hi > cutoff]
        self.last_trim = self.frontier
        return cutoff

    def consume(self, ids, ticks, trigger_types, trigger_ticks, trigger_valid, frontier):
        cutoff = self._trim(frontier)
        ids, ticks = np.asarray(ids, dtype=np.int32), np.asarray(ticks, dtype=np.int64)
        if len(ticks) and self.first_tick is None:
            self.first_tick = int(ticks.min())
        types = np.asarray(trigger_types)
        keep_type = np.isin(types, self.config.trigger_types) if self.config.trigger_types else np.ones(len(types), bool)
        self.stats['invalid_trigger_times'] += int(np.count_nonzero(keep_type & ~trigger_valid))
        new = trigger_ticks[keep_type & trigger_valid]
        if len(new):
            self.stats['selected_triggers'] += len(new)
            coverage = max(cutoff, self.first_tick if self.first_tick is not None else cutoff)
            self.stats['outside_history_triggers'] += int(np.count_nonzero(new-self.pre < coverage))
            for t in new:
                if t + self.post > cutoff:
                    self.windows.append((int(t)-self.pre, int(t)+self.post))
            self._merge_windows()
            # A late trigger may select hits received in PREVIOUS batches.
            for old_ids, old_ticks, matched in self.history:
                mask = self._mask(old_ticks) & ~matched
                self.layer.add(old_ids[mask], old_ticks[mask])
                self.stats['matched_hits'] += int(np.count_nonzero(mask))
                matched |= mask
        match = self._mask(ticks)
        self.layer.add(ids[match], ticks[match])
        self.stats['matched_hits'] += int(np.count_nonzero(match))
        recent = ticks >= cutoff
        ids, ticks, match = ids[recent], ticks[recent], match[recent]
        if len(ids):
            # Evict retained display-only history rather than ever stall collection.
            while self.history and (self.n_history + len(ids) > self.config.max_history_hits or
                                    len(self.history) >= self.config.max_chunks):
                removed = self.history.popleft()
                self.n_history -= len(removed[0])
                self.stats['history_capacity_dropped_hits'] += len(removed[0])
            if len(ids) > self.config.max_history_hits:
                self.stats['history_capacity_dropped_hits'] += len(ids)
            else:
                self.history.append((ids.copy(), ticks.copy(), match.copy()))
                self.n_history += len(ids)

    def summary(self):
        return dict(**self.stats, history_hits=self.n_history, history_chunks=len(self.history),
                    active_windows=len(self.windows), pending_matched_hits=self.layer.pending,
                    matched_buffer_dropped_hits=self.layer.dropped_hits)
