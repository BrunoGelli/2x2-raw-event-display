"""Associate per-IOG PPS intervals using message headers sampled away from rollover."""
from dataclasses import dataclass, field
import math
from typing import Optional

import numpy as np
from .trigger_windows import batch_context

TRIGGER_SOURCES = {5: 'beam', 6: 'light'}
SOURCE_NAMES = ('beam', 'light')
FEATURE_BUILD = 'receipt-pps-1'
ALIGNMENT_STALE_SECONDS = 2.5
ALIGNMENT_CONFIRMATIONS = 2


@dataclass
class PpsState:
    offset_ticks: Optional[int] = None  # pinned on first qualification
    candidate_ticks: Optional[int] = None
    reset_min: Optional[int] = None
    reset_max: Optional[int] = None
    # At most two distinct interval numbers in each band are needed.
    intervals: tuple = field(default_factory=lambda: (set(), set()))
    status: str = 'warming'
    last_sync_tick: Optional[int] = None
    last_header_second: Optional[int] = None
    last_observed: Optional[float] = None
    consistent_observations: int = 0
    inconsistent_observations: int = 0
    invalid_observations: int = 0
    stale_transitions: int = 0
    generation: int = 0
    reason: str = 'waiting for interior receipt evidence'


class CommonPpsAligner:
    """Streaming, per-board qualification. Only complete envelopes supply receipts.

    A valid edge can be labelled in its nominal second or the preceding one.
    A contradiction or stale PPS starts a new evidence window. A previously
    qualified board must corroborate its original origin before rejoining.
    """
    def __init__(self, iogs, timing_config):
        self.timing = timing_config
        self.period = timing_config.rollover_ticks
        self.states = {i: PpsState() for i in iogs}
        self.generation = 0
        self.supported = math.isclose(self.period * timing_config.tick_seconds,
                                      1., rel_tol=0., abs_tol=1e-9)
        if not self.supported:
            for state in self.states.values():
                state.status = 'unsupported'
                state.reason = 'receipt alignment requires a one-second PPS period'

    def _revoke(self, state, status, reason):
        if state.status == 'aligned':
            state.generation += 1
            self.generation += 1
        state.status, state.reason = status, reason
        state.candidate_ticks = state.reset_min = state.reset_max = None
        for band in state.intervals:
            band.clear()

    def invalid(self, iog, now, reason):
        if self.supported:
            state = self.states[iog]
            state.invalid_observations += 1
            self._revoke(state, 'suspect', reason)

    def _conflict(self, state, reason):
        state.inconsistent_observations += 1
        self._revoke(state, 'suspect', reason)

    def observe(self, iog, sync_tick, header_second, now):
        if not self.supported:
            return
        state = self.states[iog]
        tick, second = int(sync_tick), int(header_second)
        if (second <= 0 or tick < 0 or tick % self.period or
                (state.last_sync_tick is not None and tick <= state.last_sync_tick)):
            self.invalid(iog, now, 'invalid PPS progression or missing header')
            return
        state.last_sync_tick, state.last_header_second = tick, second
        state.last_observed = float(now)
        origin = second * self.period - tick
        expected = state.offset_ticks if state.offset_ticks is not None else state.candidate_ticks
        if expected is not None and origin not in (expected, expected - self.period):
            self._conflict(state, 'reset header incompatible with receipt origin')
            return
        state.reset_min = origin if state.reset_min is None else min(state.reset_min, origin)
        state.reset_max = origin if state.reset_max is None else max(state.reset_max, origin)
        if state.reset_max - state.reset_min > self.period:
            self._conflict(state, 'reset headers span more than one second')

    def _receipt(self, state, band, interval, second):
        if state.reset_min is None:
            return
        origin = (second - interval) * self.period
        if ((state.offset_ticks is not None and origin != state.offset_ticks) or
                (state.candidate_ticks is not None and origin != state.candidate_ticks) or
                not origin - self.period <= state.reset_min <= state.reset_max <= origin):
            self._conflict(state, 'interior receipt conflicts with PPS epoch')
            return
        state.candidate_ticks = origin
        state.consistent_observations += 1
        if len(state.intervals[band]) < ALIGNMENT_CONFIRMATIONS:
            state.intervals[band].add(interval)
        if all(len(b) >= ALIGNMENT_CONFIRMATIONS for b in state.intervals):
            state.offset_ticks = origin
            state.status, state.reason = 'aligned', 'both receipt bands corroborate PPS origin'
        else:
            state.status, state.reason = 'warming', 'waiting for distinct intervals in both receipt bands'

    def expire(self, now):
        for state in self.states.values():
            if (state.last_observed is not None and
                    now - state.last_observed > ALIGNMENT_STALE_SECONDS and
                    state.status not in ('stale', 'unsupported')):
                state.stale_transitions += 1
                self._revoke(state, 'stale', 'no fresh valid PPS observation')

    def offset(self, iog, now):
        state = self.states[iog]
        if (state.status == 'aligned' and state.last_observed is not None and
                0 <= now - state.last_observed <= ALIGNMENT_STALE_SECONDS):
            return state.offset_ticks
        return None

    def _receipt_events(self, hits, ends, seconds, selected_sync, valid_sync, prefix, initial):
        # Accepted D words are parity-valid type 0. Sample their PACMAN receipt
        # phase irrespective of ASIC FIFO delay, geometry, or the display veto.
        indices = hits.word_indices
        if indices is None or not len(indices):
            return []
        groups = np.searchsorted(ends, indices, side='right')
        starts = np.r_[0, np.flatnonzero(np.diff(groups)) + 1]
        messages = groups[starts]
        lo = np.minimum.reduceat(hits.receipt_timestamp, starts)
        hi = np.maximum.reduceat(hits.receipt_timestamp, starts)
        preceding = np.searchsorted(valid_sync, indices[starts])
        eligible = (initial[1] | (preceding > 0)) & (seconds[messages] > 0)
        # An envelope containing an edge, even with only interior D words,
        # spans rollover and cannot establish an interior message time.
        if len(selected_sync):
            has_sync = np.zeros(len(ends), dtype=bool)
            has_sync[np.searchsorted(ends, selected_sync, side='right')] = True
            eligible &= ~has_sync[messages]
        events = []
        for band, start in enumerate((2, 7)):
            inside = eligible & (lo >= start*self.period//10) & (hi < (start+1)*self.period//10)
            for msg, epoch in zip(messages[inside], preceding[inside]):
                # Evidence becomes usable only AFTER the entire envelope.
                events.append((int(ends[msg]), 0, (band, int(prefix[epoch])//self.period, int(seconds[msg]))))
        return events

    def observe_batch(self, iog, hits, initial, now):
        words = hits.words
        if words is None:
            return []
        triggers = np.flatnonzero(words['kind'] == ord('T'))
        if not self.supported:
            return [None] * len(triggers)
        ends, seconds = hits.message_ends, hits.message_seconds
        if (ends is None or seconds is None or not len(ends) or len(ends) != len(seconds)
                or ends[0] <= 0 or ends[-1] != len(words) or np.any(np.diff(ends) <= 0)):
            self.invalid(iog, now, 'missing envelope metadata')
            return [None] * len(triggers)
        selected = np.flatnonzero((words['kind'] == ord('S')) & (words['io'] == self.timing.sync_type))
        si, prefix = batch_context(hits, initial, self.timing)
        ticks = dict(zip(si, prefix[1:]))
        sync_messages = np.searchsorted(ends, selected, side='right')
        unique, counts = np.unique(sync_messages, return_counts=True)
        ambiguous = set(unique[counts > 1])
        events = self._receipt_events(hits, ends, seconds, selected, si, prefix, initial)
        for index, msg in zip(selected, sync_messages):
            events.append((index, 1, (ticks.get(index), seconds[msg], msg in ambiguous)))
        events.extend((index, 2, None) for index in triggers)
        offsets = []
        for _, kind, value in sorted(events):
            if kind == 0:
                self._receipt(self.states[iog], *value)
            elif kind == 1:
                tick, second, ambiguous = value
                if tick is None or ambiguous:
                    self.invalid(iog, now, 'invalid SYNC or multiple resets in one envelope')
                else:
                    self.observe(iog, tick, second, now)
            else:
                offsets.append(self.offset(iog, now))
        return offsets

    def summary(self, now):
        return dict(supported=self.supported, generation=self.generation,
                    method='interior PACMAN receipt timestamps and PPS intervals',
                    hardware_phase_verified=False,
                    required_consistent_observations=ALIGNMENT_CONFIRMATIONS,
                    stale_after_s=ALIGNMENT_STALE_SECONDS,
                    sources=[dict(iog=i, status=s.status, reason=s.reason,
                        method='receipt' if s.status == 'aligned' else 'local',
                        epoch_second=None if self.offset(i, now) is None else s.offset_ticks//self.period,
                        offset_ticks=s.offset_ticks, candidate_offset_ticks=s.candidate_ticks,
                        receipt_intervals=[len(b) for b in s.intervals],
                        confirmations=min(map(len, s.intervals)), last_sync_tick=s.last_sync_tick,
                        last_header_second=s.last_header_second,
                        last_valid_age_s=None if s.last_observed is None else max(0., now-s.last_observed),
                        consistent_observations=s.consistent_observations,
                        inconsistent_observations=s.inconsistent_observations,
                        invalid_observations=s.invalid_observations,
                        stale_transitions=s.stale_transitions)
                        for i, s in self.states.items()])
