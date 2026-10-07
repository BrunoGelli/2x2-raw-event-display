"""Guarded, integer-only PPS-epoch labels. This is NOT a phase calibration.

A PACMAN envelope labels message assembly, not the PPS edge itself. Consistent
header labels allow a *provisional* whole-second epoch association under the
operational assumption that S83 is packaged in its corresponding second. No
stream-only method here can detect a stable one-second packaging/NTP bias.
That assumption MUST be checked on detector before the v1 release.

No clock/hit is modified; host monotonic time is used only for freshness.
"""
from dataclasses import dataclass
import math
from typing import Optional
import numpy as np
from .trigger_windows import batch_context

TRIGGER_SOURCES = {5: 'beam', 6: 'light'}
SOURCE_NAMES = ('beam', 'light')
FEATURE_BUILD = 'detector-wide-2d-1'
ALIGNMENT_STALE_SECONDS = 2.5
ALIGNMENT_CONFIRMATIONS = 2


@dataclass
class PpsState:
    offset_ticks: Optional[int] = None
    candidate_ticks: Optional[int] = None
    confirmations: int = 0
    status: str = 'warming'
    last_sync_tick: Optional[int] = None
    last_header_second: Optional[int] = None
    last_observed: Optional[float] = None
    consistent_observations: int = 0
    inconsistent_observations: int = 0
    invalid_observations: int = 0
    stale_transitions: int = 0
    reason: str = 'waiting for consistent PPS labels'


class CommonPpsAligner:
    """Two ordered, consistent S83 observations qualify each independent epoch.

    Once qualified, the offset is pinned for the collector session. Conflicting
    labels revoke eligibility; two observations of the ORIGINAL offset restore
    it. A persistent new offset requires a collector restart, never an automatic
    retiming. Identical/reordered or multi-S83-in-one-envelope labels cannot vote.
    """
    def __init__(self, iogs, timing_config):
        self.timing = timing_config
        self.period = timing_config.rollover_ticks
        self.states = {i: PpsState() for i in iogs}
        self.generation = 0  # increments when a formerly usable mapping is revoked
        self.supported = math.isclose(self.period * timing_config.tick_seconds,
                                      1., rel_tol=0., abs_tol=1e-9)
        if not self.supported:
            for state in self.states.values():
                state.status = 'unsupported'
                state.reason = 'header-second alignment requires a one-second PPS period'

    def _revoke(self, state, status, reason):
        if state.status == 'aligned':
            self.generation += 1
        state.status, state.reason = status, reason
        state.confirmations = 0

    def invalid(self, iog, now, reason):
        if not self.supported:
            return
        state = self.states[iog]
        state.invalid_observations += 1
        self._revoke(state, 'suspect', reason)
        # Do not refresh last_observed: malformed words are not valid PPS progress.

    def observe(self, iog, sync_tick, header_second, now):
        if not self.supported:
            return
        state = self.states[iog]
        tick, second = int(sync_tick), int(header_second)
        if second <= 0 or tick < 0 or tick % self.period:
            self.invalid(iog, now, 'missing/invalid header second or nonintegral epoch')
            return
        candidate = second * self.period - tick  # Python ints; never float Unix ticks
        if state.last_sync_tick is not None and (
                tick <= state.last_sync_tick or second <= state.last_header_second):
            state.inconsistent_observations += 1
            self._revoke(state, 'suspect', 'non-increasing PPS/header label')
            # Save the last observation so a transient bad header can recover.
            state.last_sync_tick, state.last_header_second = tick, second
            return
        state.last_sync_tick, state.last_header_second = tick, second
        state.last_observed = float(now)
        expected = state.offset_ticks
        if expected is not None and candidate != expected:
            state.inconsistent_observations += 1
            self._revoke(state, 'suspect', 'header/PPS offset changed; pinned offset not replaced')
            state.candidate_ticks = candidate
            return
        if candidate != state.candidate_ticks:
            if state.candidate_ticks is not None:
                state.inconsistent_observations += 1
            state.candidate_ticks = candidate
            state.confirmations = 0
        state.confirmations += 1
        state.consistent_observations += 1
        if state.confirmations >= ALIGNMENT_CONFIRMATIONS:
            state.offset_ticks = candidate
            state.status = 'aligned'
            state.reason = 'consistent header-labelled PPS epoch; hardware phase unverified'
        else:
            state.status = 'warming'
            state.reason = 'waiting for another consistent PPS label'

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

    def observe_batch(self, iog, hits, initial, now):
        """Return one optional epoch offset per T word, respecting S/T wire order.

        DATA arrays remain untouched. Later SYNCs cannot qualify earlier T words.
        All selected S words (including invalid ones) are considered. Multiple
        selected S words sharing an envelope are ambiguous, not back-labelled.
        """
        words = hits.words
        if not hits.counters.get('sync', 0) and not hits.counters.get('triggers', 0):
            return []
        if words is None:
            return []
        si, prefix = batch_context(hits, initial, self.timing)
        valid_syncs = {int(index): int(tick) for index, tick in zip(si, prefix[1:])}
        selected = (words['kind'] == ord('S')) & (words['io'] == self.timing.sync_type)
        indices = np.flatnonzero(selected | (words['kind'] == ord('T')))
        if not len(indices):
            return []
        ends, seconds = hits.message_ends, hits.message_seconds
        have_headers = (ends is not None and seconds is not None and len(ends) > 0
                        and len(ends) == len(seconds) and int(ends[-1]) == len(words)
                        and np.all(np.diff(ends) > 0))
        if have_headers:
            sync_messages = np.searchsorted(ends, np.flatnonzero(selected), side='right')
            multiplicity = np.bincount(sync_messages, minlength=len(ends))
        offsets = []
        for index in indices:
            if words['kind'][index] == ord('T'):
                offsets.append(self.offset(iog, now))
                continue
            if index not in valid_syncs:
                self.invalid(iog, now, 'invalid selected SYNC timestamp')
            elif not have_headers:
                self.invalid(iog, now, 'missing envelope metadata')
            else:
                message = int(np.searchsorted(ends, index, side='right'))
                if multiplicity[message] != 1:
                    self.invalid(iog, now, 'multiple PPS words in one envelope')
                else:
                    self.observe(iog, valid_syncs[int(index)], seconds[message], now)
        return offsets

    def summary(self, now):
        return dict(supported=self.supported, generation=self.generation,
                    method='provisional header-labelled PPS; integer ticks',
                    hardware_phase_verified=False,
                    required_consistent_observations=ALIGNMENT_CONFIRMATIONS,
                    stale_after_s=ALIGNMENT_STALE_SECONDS,
                    sources=[dict(iog=i, status=s.status, reason=s.reason,
                        offset_ticks=s.offset_ticks, candidate_offset_ticks=s.candidate_ticks,
                        confirmations=s.confirmations, last_sync_tick=s.last_sync_tick,
                        last_header_second=s.last_header_second,
                        last_valid_age_s=None if s.last_observed is None else max(0., now-s.last_observed),
                        consistent_observations=s.consistent_observations,
                        inconsistent_observations=s.inconsistent_observations,
                        invalid_observations=s.invalid_observations,
                        stale_transitions=s.stale_transitions)
                        for i, s in self.states.items()])
