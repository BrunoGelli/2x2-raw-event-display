"""Optional trigger layer + late-hit audit attached to the EXISTING collector.

There are no sockets, detector commands, disk writes, or independent playheads.
Diagnostics have a separate non-blocking writer lock. Hit-state publication shares
only the existing short snapshot lock. No per-browser state enters the collector.
"""
import json
import struct
import uuid
import numpy as np
from .trigger_windows import ObserverConfig, TriggerWindows, project_triggers
from .late_audit import LateAudit

JSON_CAPACITY = 262144
HEADER = struct.Struct('<4sII')
RECORD = np.dtype([('id', '<u4'), ('time', '<f8')])


def attach_observers(ctx, shared, n_pixels, iogs, config):
    missing = set(config.audit_iogs) - set(iogs)
    if missing:
        raise ValueError('Timing audit requested IO groups not selected: ' + str(sorted(missing)))
    shared.observer_config = config
    shared.observer_json = ctx.RawArray('B', JSON_CAPACITY)
    shared.observer_length = ctx.RawValue('I', 0)
    shared.observer_lock = ctx.Lock()
    shared.trigger_seen = ctx.RawArray('d', n_pixels) if config.trigger_view else None
    if shared.trigger_seen is not None:
        np.frombuffer(shared.trigger_seen).fill(-1.)


def read_observers(shared):
    if not hasattr(shared, 'observer_json'):
        return dict(version=1, enabled=False, audit_iogs=[], sources=[], audits=[])
    with shared.observer_lock:
        n = shared.observer_length.value
        raw = bytes(shared.observer_json[:n])
    if not raw:
        return dict(version=1, enabled=shared.observer_config.trigger_view,
                    audit_iogs=list(shared.observer_config.audit_iogs), sources=[], audits=[])
    return json.loads(raw)


def snapshot_with_triggers(shared, fields, timing_fields):
    """Coherent normal/matched state and clocks. Legacy callers retain their API."""
    with shared.lock:
        state = np.frombuffer(shared.seen).copy()
        stats = np.frombuffer(shared.metrics).reshape(9, len(fields)).copy()
        heartbeat = shared.heartbeat.value
        timing = np.frombuffer(shared.timing).reshape(9, len(timing_fields)).copy()
        cpu = shared.cpu_ratio.value
        extra = getattr(shared, 'trigger_seen', None)
        matched = np.frombuffer(extra).copy() if extra is not None else None
    return state, stats, heartbeat, timing, cpu, matched


def encode_trigger_frame(previous, current, sequence):
    """Supplementary RDT1 records: same detector seconds as canonical RDP2.

    One normal RDP2 acknowledgement covers preceding optional RDT1 data; older
    clients never request/receive RDT1. State is diffed per client on success.
    """
    ids = np.flatnonzero(previous != current)
    records = np.empty(len(ids), dtype=RECORD)
    records['id'], records['time'] = ids, current[ids]
    return HEADER.pack(b'RDT1', sequence, len(ids)) + records.tobytes()


class Observers:
    def __init__(self, n_pixels, iogs, timing_config, config, shared):
        self.config, self.shared, self.iogs = config, shared, list(iogs)
        self.session_id = uuid.uuid4().hex
        self.tagged = np.full(n_pixels, -1.) if config.trigger_view else None
        self.matchers = {i: TriggerWindows(self.tagged, timing_config, config) for i in iogs} if config.trigger_view else {}
        self.audits = {i: LateAudit(i, timing_config) for i in config.audit_iogs}
        self.type_counts = {i: np.zeros(256, dtype=np.int64) for i in iogs}
        self.bad_times = {i: 0 for i in iogs}
        self.published = None
        self.skipped_diagnostics = 0

    def record(self, iog, ids, hits, result, clock, initial):
        ticks, valid, selected = result
        if iog in self.audits:
            self.audits[iog].record(hits, ticks, selected, clock.cursor, initial)
        if not hits.counters.get('triggers', 0) and iog not in self.matchers:
            return
        # Counts are visible even if optional time-window matching is disabled.
        types, trigger_ticks, trigger_valid = project_triggers(hits, initial, clock.config)
        if len(types):
            self.type_counts[iog] += np.bincount(types.astype(np.int64), minlength=256)
            self.bad_times[iog] += int(np.count_nonzero(~trigger_valid))
        if iog in self.matchers:
            frontier = clock.unroller.frontier_tick
            if frontier is not None:
                self.matchers[iog].consume(np.asarray(ids)[selected], ticks[selected],
                    types, trigger_ticks, trigger_valid, frontier)

    def step(self, iog, cursor):
        if iog in self.matchers:
            self.matchers[iog].layer.step(cursor)

    def publish_state(self):
        """Call under the existing shared snapshot lock; bounded array copy only."""
        if self.tagged is not None:
            np.copyto(np.frombuffer(self.shared.trigger_seen), self.tagged)

    def publish_diagnostics(self, now):
        if self.published is not None and now-self.published < 1.:
            return
        self.published = now
        sources = []
        for i in self.iogs:
            counts = self.type_counts[i]
            summary = self.matchers[i].summary() if i in self.matchers else {}
            sources.append(dict(iog=i, trigger_subtypes={str(k): int(counts[k]) for k in np.flatnonzero(counts)},
                                invalid_trigger_times=self.bad_times[i], matching=summary))
        payload = dict(version=1, session_id=self.session_id, captured_monotonic=now, enabled=self.config.trigger_view,
                       scope='same-IOG only; cross-IOG epochs uncalibrated',
                       pre_us=self.config.pre_us, post_us=self.config.post_us,
                       history_seconds=self.config.history_seconds,
                       trigger_types=list(self.config.trigger_types), audit_iogs=list(self.config.audit_iogs),
                       sources=sources, audits=[self.audits[i].summary() for i in self.config.audit_iogs],
                       skipped_diagnostic_publications=self.skipped_diagnostics)
        raw = json.dumps(payload, separators=(',', ':'), allow_nan=False).encode()
        if len(raw) > JSON_CAPACITY or not self.shared.observer_lock.acquire(False):
            self.skipped_diagnostics += 1
            return
        try:
            np.frombuffer(self.shared.observer_json, dtype=np.uint8)[:len(raw)] = np.frombuffer(raw, dtype=np.uint8)
            self.shared.observer_length.value = len(raw)
        finally:
            self.shared.observer_lock.release()
