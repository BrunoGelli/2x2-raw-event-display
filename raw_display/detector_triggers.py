"""Detector-wide Beam/Light candidates over ONE bounded hit history per IOG.

Separate source-specific last-hit layers allow browser-local Beam/Light/Both
selection without losing an older beam hit when a newer light hit uses its pixel.
This is a temporal-candidate view, not causal event reconstruction.
"""
from collections import deque
import numpy as np
from .common_timing import SOURCE_NAMES, TRIGGER_SOURCES, CommonPpsAligner
from .trigger_windows import DueLayer, project_triggers
from .drift_state import PairedDueLayer

SOURCE_BITS = {'beam': np.uint8(1), 'light': np.uint8(2)}


class DetectorWindows:
    def __init__(self, states, timing, config, drift_ticks=None):
        self.config, self.timing = config, timing
        self.paired = drift_ticks is not None
        self.layers = {name: (PairedDueLayer(states[name], drift_ticks[name], timing, config)
                             if self.paired else DueLayer(states[name], timing, config)) for name in SOURCE_NAMES}
        self.trigger_ticks = {name: np.empty(0, dtype=np.int64) for name in SOURCE_NAMES} if self.paired else {}
        self.pre = int(round(config.pre_us * 1e-6 / timing.tick_seconds))
        self.post = max(1, int(round(config.post_us * 1e-6 / timing.tick_seconds)))
        self.history_ticks = int(round(config.history_seconds / timing.tick_seconds))
        self.history, self.n_history = deque(), 0
        self.windows = {name: [] for name in SOURCE_NAMES}
        self.frontier, self.last_trim = -1, -1
        self.first_tick = None
        self.stats = dict(matched_hits=0, history_capacity_dropped_hits=0,
                          association_resets=0, reset_discarded_history_hits=0,
                          reset_discarded_pending_hits=0)
        self.source_stats = {name: dict(selected_triggers=0, matched_hits=0,
                             outside_history_triggers=0, window_limit_drops=0,
                             association_trigger_limit_drops=0)
                             for name in SOURCE_NAMES}

    def _mask(self, ticks, source):
        if not self.windows[source] or not len(ticks):
            return np.zeros(len(ticks), dtype=bool)
        windows = np.asarray(self.windows[source], dtype=np.int64)
        which = np.searchsorted(windows[:, 0], ticks, side='right') - 1
        return (which >= 0) & (ticks < windows[np.maximum(which, 0), 1])

    def _trim(self, frontier):
        self.frontier = max(self.frontier, int(frontier))
        cutoff = self.frontier - self.history_ticks
        stride = max(1, int(.1 / self.timing.tick_seconds))
        if self.last_trim >= 0 and self.frontier-self.last_trim < stride:
            return cutoff
        kept = deque(chunk for chunk in self.history if chunk[4] >= cutoff)
        self.history, self.n_history = kept, sum(len(c[0]) for c in kept)
        for source in SOURCE_NAMES:
            self.windows[source] = [(lo, hi) for lo, hi in self.windows[source] if hi > cutoff]
            if self.paired:
                values = self.trigger_ticks[source]
                self.trigger_ticks[source] = values[values + self.post > cutoff]
        self.last_trim = self.frontier
        return cutoff

    def _tag(self, ids, ticks, bits, source):
        bit = SOURCE_BITS[source]
        matched = self._mask(ticks, source)
        selected = matched & ((bits & bit) == 0)
        if self.paired:
            # Revisit already matched hits too: a late, more recent trigger can
            # improve t0 without changing the hit or double-counting matches.
            hits = ticks[matched]
            triggers = self.trigger_ticks[source]
            delta = np.full(len(hits), -1, dtype=np.int64)
            if len(triggers):
                which = np.searchsorted(triggers, hits, side='right') - 1
                candidate = hits - triggers[np.maximum(which, 0)]
                valid = (which >= 0) & (candidate >= 0) & (candidate < self.post)
                delta[valid] = candidate[valid]
            self.layers[source].add(ids[matched], hits, delta)
        else:
            self.layers[source].add(ids[selected], ticks[selected])
        self.source_stats[source]['matched_hits'] += int(np.count_nonzero(selected))
        self.stats['matched_hits'] += int(np.count_nonzero(selected & (bits == 0)))
        bits[selected] |= bit

    def consume_hits(self, ids, ticks, frontier):
        cutoff = self._trim(frontier)
        ids, ticks = np.asarray(ids, dtype=np.int32), np.asarray(ticks, dtype=np.int64)
        if not len(ids):
            return
        if self.first_tick is None:
            self.first_tick = int(ticks.min())
        bits = np.zeros(len(ids), dtype=np.uint8)
        for source in SOURCE_NAMES:
            self._tag(ids, ticks, bits, source)
        recent = ticks >= cutoff
        ids, ticks, bits = ids[recent], ticks[recent], bits[recent]
        if not len(ids):
            return
        while self.history and (self.n_history+len(ids) > self.config.max_history_hits or
                                len(self.history) >= self.config.max_chunks):
            removed = self.history.popleft()
            self.n_history -= len(removed[0])
            self.stats['history_capacity_dropped_hits'] += len(removed[0])
        if len(ids) > self.config.max_history_hits:
            self.stats['history_capacity_dropped_hits'] += len(ids)
        else:
            self.history.append((ids, ticks, bits, int(ticks.min()), int(ticks.max())))
            self.n_history += len(ids)

    def consume_triggers(self, source, ticks, frontier):
        ticks = np.asarray(ticks, dtype=np.int64)
        cutoff = self._trim(frontier)
        stats = self.source_stats[source]
        stats['selected_triggers'] += len(ticks)
        coverage = max(cutoff, self.first_tick if self.first_tick is not None else cutoff)
        stats['outside_history_triggers'] += int(np.count_nonzero(ticks-self.pre < coverage))
        additions = [(int(t)-self.pre, int(t)+self.post) for t in ticks if t+self.post > cutoff]
        if self.paired:
            values = np.unique(np.r_[self.trigger_ticks[source], ticks[ticks + self.post > cutoff]])
            stats['association_trigger_limit_drops'] += max(0, len(values) - self.config.max_windows)
            self.trigger_ticks[source] = values[-self.config.max_windows:]
        merged = []
        for lo, hi in sorted(self.windows[source]+additions):
            if merged and lo <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
            else:
                merged.append((lo, hi))
        if len(merged) > self.config.max_windows:
            stats['window_limit_drops'] += len(merged)-self.config.max_windows
            merged = merged[-self.config.max_windows:]
        self.windows[source] = merged
        # Prior windows were already applied on ingest/earlier trigger batches.
        # Only chunks intersecting a NEW window can acquire new associations.
        # Cached conservative bounds also handle out-of-order ticks within chunks.
        if additions:
            lower = min(lo for lo, _ in additions)
            upper = max(hi for _, hi in additions)
            for ids, old_ticks, bits, lo, hi in self.history:
                if hi >= lower and lo < upper:
                    self._tag(ids, old_ticks, bits, source)

    def clear_associations(self, sources, pixels):
        """Forget only the revoked routes. Local hit times remain reusable."""
        self.stats['association_resets'] += 1
        for name in sources:
            layer = self.layers[name]
            self.windows[name] = []
            layer.state[pixels] = -1.
            if self.paired:
                self.trigger_ticks[name] = np.empty(0, dtype=np.int64)
                layer.drift_ticks[pixels] = -1
            self.stats['reset_discarded_pending_hits'] += layer.pending
            layer.heap.clear()
            layer.pending = 0
            for _, _, bits, _, _ in self.history:
                bits &= ~SOURCE_BITS[name]

    def step(self, cursor):
        for layer in self.layers.values():
            layer.step(cursor)

    def summary(self):
        return dict(**self.stats, history_hits=self.n_history, history_chunks=len(self.history),
                    sources={name: dict(**self.source_stats[name],
                        active_windows=len(self.windows[name]),
                        pending_matched_hits=self.layers[name].pending,
                        matched_buffer_dropped_hits=self.layers[name].dropped_hits)
                        for name in SOURCE_NAMES})


class DetectorTriggerRouter:
    def __init__(self, n_pixels, iogs, timing, config):
        self.config, self.timing = config, timing
        self.beam_delay_ticks = int(round(config.beam_delay_us * 1e-6 / timing.tick_seconds))
        self.aligner = CommonPpsAligner(iogs, timing)
        self.states = {name: np.full(n_pixels, -1.) for name in SOURCE_NAMES} if config.trigger_view else {}
        self.drift_ticks = {name: np.full(n_pixels, -1, dtype=np.int64) for name in SOURCE_NAMES} if config.trigger_view and config.view3d else {}
        self.matchers = {i: DetectorWindows(self.states, timing, config, self.drift_ticks or None) for i in iogs} if config.trigger_view else {}
        self.frontiers = {i: None for i in iogs}
        self.pixel_iog = np.zeros(n_pixels, dtype=np.uint8) if config.trigger_view else None
        self.generations = {i: 0 for i in iogs}
        self.generation = 0
        self.stats = dict(unexpected_source_triggers=0, invalid_trigger_times=0,
                          filtered_subtype_triggers=0, source_unaligned_triggers=0,
                          trigger_batch_limit_drops=0, uncertain_batch_triggers=0)
        self.targets = {i: dict(routed_beam=0, routed_light=0,
                              unaligned_target_skips=0, invalid_projected_ticks=0)
                        for i in iogs}

    def _clear_revoked(self):
        if self.generation == self.aligner.generation:
            return
        changed = set()
        for iog, state in self.aligner.states.items():
            if self.generations[iog] != state.generation:
                changed.add(iog)
                self.generations[iog] = state.generation
        for target, matcher in self.matchers.items():
            affected = [name for source, name in TRIGGER_SOURCES.items()
                        if source != target and (source in changed or target in changed)]
            if affected:
                matcher.clear_associations(affected, self.pixel_iog == target)
        self.generation = self.aligner.generation

    def expire(self, now):
        self.aligner.expire(now)
        self._clear_revoked()

    def record(self, iog, ids, hits, ticks, selected, initial, frontier, now):
        self.expire(now)
        generation_before = self.aligner.generation
        offsets = self.aligner.observe_batch(iog, hits, initial, now)
        # Detector progress without PPS must not look fresh just because it was
        # delivered in a burst. This is an eligibility check, not a time correction.
        s = self.aligner.states[iog]
        if (s.status == 'aligned' and frontier is not None and s.last_sync_tick is not None
                and frontier-s.last_sync_tick > int(2.5*self.timing.rollover_ticks)):
            self.aligner.invalid(iog, now, 'detector frontier advanced without valid PPS')
        self._clear_revoked()
        self.frontiers[iog] = frontier
        if iog not in self.matchers:
            return
        if frontier is not None:
            pixel_ids = np.asarray(ids)[selected]
            self.pixel_iog[pixel_ids] = iog
            self.matchers[iog].consume_hits(pixel_ids, ticks[selected], frontier)
        if not hits.counters.get('triggers', 0):
            return
        types, trigger_ticks, valid = project_triggers(hits, initial, self.timing)
        source = TRIGGER_SOURCES.get(iog)
        if source is None:
            self.stats['unexpected_source_triggers'] += len(types)
            return
        keep_type = np.isin(types, self.config.trigger_types) if self.config.trigger_types else np.ones(len(types), bool)
        self.stats['filtered_subtype_triggers'] += int(np.count_nonzero(~keep_type))
        self.stats['invalid_trigger_times'] += int(np.count_nonzero(keep_type & ~valid))
        selected_triggers = keep_type & valid
        source_offset = self.aligner.offset(iog, now)
        ready = np.array([offset is not None and offset == source_offset for offset in offsets], dtype=bool)
        if self.aligner.generation != generation_before:
            self.stats['uncertain_batch_triggers'] += int(np.count_nonzero(selected_triggers))
            ready[:] = False
        self.stats['source_unaligned_triggers'] += int(np.count_nonzero(selected_triggers & ~ready))
        triggers = trigger_ticks[selected_triggers]
        ready = ready[selected_triggers]
        if len(triggers) > self.config.max_windows:
            self.stats['trigger_batch_limit_drops'] += len(triggers)-self.config.max_windows
            triggers, ready = triggers[-self.config.max_windows:], ready[-self.config.max_windows:]
        if not len(triggers):
            return
        remote = triggers[ready]
        for target, matcher in self.matchers.items():
            target_frontier = self.frontiers[target]
            if target_frontier is None:
                self.targets[target]['unaligned_target_skips'] += len(remote)
                continue
            if target == iog:
                # A hit and trigger on one PACMAN already share a hardware clock.
                local = triggers
            else:
                if not len(remote):
                    continue
                target_offset = self.aligner.offset(target, now)
                if target_offset is None:
                    self.targets[target]['unaligned_target_skips'] += len(remote)
                    continue
                local = remote + (source_offset - target_offset)
            if source == 'beam':
                local = local + self.beam_delay_ticks
            good = local >= 0
            self.targets[target]['invalid_projected_ticks'] += int(np.count_nonzero(~good))
            self.targets[target]['routed_'+source] += int(np.count_nonzero(good))
            matcher.consume_triggers(source, local[good], target_frontier)

    def step(self, iog, cursor, now):
        self.expire(now)
        if iog in self.matchers:
            self.matchers[iog].step(cursor)

    def summary(self, now):
        return dict(common_timing=self.aligner.summary(now), trigger_routing=dict(
                    source_iogs={name: i for i, name in TRIGGER_SOURCES.items()},
                    **self.stats, targets=[dict(iog=i, **v) for i, v in self.targets.items()]))
