"""Synthetic wire-level regression; no real PACMAN addresses or data."""
import json
import struct
import numpy as np
import pytest
from raw_display.codec import HEADER, decode_batch, make_message
from raw_display.timing import TimingConfig, DetectorPlayback
from raw_display.trigger_windows import ObserverConfig
from raw_display.common_timing import CommonPpsAligner
from raw_display.detector_triggers import DetectorTriggerRouter, DetectorWindows

R = 10_000_000
UNIX = 1_790_000_000  # Unix*R exceeds 2**53; deliberately exercise integer precision.


def aux(kind='S', subtype=83, timestamp=R):
    return struct.pack('<BB2xI8x', ord(kind), subtype, timestamp)


def charge(t, receipt=None, channel=0):
    word = bytearray(make_message(1, 11, channel, timestamp=t, downstream=True)[8:])
    struct.pack_into('<I', word, 2, t if receipt is None else receipt)
    return bytes(word)


def message(second, *words, kind=b'D'):
    return HEADER.pack(kind, second, len(words))+b''.join(words)


class Driver:
    def __init__(self, iogs=range(1, 9), **kwargs):
        self.iogs = list(iogs)
        self.timing = TimingConfig()
        self.config = ObserverConfig(trigger_view=True, **kwargs)
        self.router = DetectorTriggerRouter(1024, self.iogs, self.timing, self.config)
        self.normal = np.full(1024, -1.)
        self.clocks = {i: DetectorPlayback(self.normal, self.timing) for i in self.iogs}
        self.now = 10.
        self.origins = {i: UNIX-(i % 3) for i in self.iogs}

    def feed(self, iog, *frames):
        clock = self.clocks[iog]
        h = decode_batch(frames)
        ids = iog*64+h.channel.astype(np.int32)
        initial = (clock.unroller.offset, clock.unroller.initial_tick is not None)
        ticks, valid, selected = clock.ingest(ids, h)
        self.router.record(iog, ids, h, ticks, selected, initial,
                           clock.unroller.frontier_tick, self.now)
        return h, ticks, valid, selected

    def ready(self):
        # All boards reach the same physical PPS (UNIX+1), from different starts.
        for i in self.iogs:
            for second in range(self.origins[i], UNIX+2):
                self.feed(i, message(second, aux()))
        assert all(s.status == 'aligned' for s in self.router.aligner.states.values())
        return self

    def tick(self, iog, phase=0):
        return self.clocks[iog].unroller.offset+phase

    def paint(self, phase=10000):
        for i in self.iogs:
            self.router.step(i, self.tick(i, phase)/R, self.now)


@pytest.mark.parametrize('source,kind', [(5, 'beam'), (6, 'light')])
def test_only_one_stream_has_trigger_but_all_eight_planes_match(source, kind):
    d = Driver().ready()
    for i in d.iogs:
        d.feed(i, message(UNIX+1, charge(101)))
    d.feed(source, message(UNIX+1, aux('T', 2, 100)))
    d.paint()
    state = d.router.states[kind]
    for i in d.iogs:
        assert state[i*64] == pytest.approx(d.tick(i, 101)/R)
        assert d.router.targets[i]['routed_'+kind] == 1
    other = 'light' if kind == 'beam' else 'beam'
    assert np.all(d.router.states[other] < 0)


@pytest.mark.parametrize('source', [5, 6])
@pytest.mark.parametrize('subtype', [0, 2, 7, 72, 83, 255])
def test_source_identity_depends_on_iog_not_subtype(source, subtype):
    d = Driver().ready()
    d.feed(1, message(UNIX+1, charge(120)))
    d.feed(source, message(UNIX+1, aux('T', subtype, 100)))
    d.paint()
    assert d.router.states['beam' if source == 5 else 'light'][64] >= 0


@pytest.mark.parametrize('iog', [1, 2, 3, 4, 7, 8])
def test_unexpected_trigger_source_does_not_create_event_windows(iog):
    d = Driver().ready()
    d.feed(1, message(UNIX+1, charge(120)))
    d.feed(iog, message(UNIX+1, aux('T', 2, 100)))
    d.paint()
    assert not any(np.any(s >= 0) for s in d.router.states.values())
    assert d.router.stats['unexpected_source_triggers'] == 1


def test_exact_half_open_window_after_integer_epoch_conversion():
    d = Driver().ready()
    for channel, phase in enumerate([99, 100, 1999, 2000]):
        d.feed(1, message(UNIX+1, charge(phase, channel=channel)))
    d.feed(5, message(UNIX+1, aux('T', 2, 100)))
    d.paint()
    assert np.flatnonzero(d.router.states['beam'] >= 0).tolist() == [65, 66]


def test_adjacent_pps_epoch_windows_match_both_sides():
    d = Driver().ready()
    d.feed(5, message(UNIX+1, aux('T', 2, R-20)))
    d.feed(1, message(UNIX+1, charge(R-10)), message(UNIX+2, aux(), charge(15)))
    d.paint()
    matcher = d.router.matchers[1]
    assert matcher.source_stats['beam']['matched_hits'] == 2
    assert d.router.states['beam'][64] == pytest.approx(d.tick(1, 15)/R)


def test_future_candidates_wait_and_late_hits_keep_original_age():
    d = Driver().ready()
    d.feed(5, message(UNIX+1, aux('T', 2, 100)))
    d.feed(1, message(UNIX+1, charge(120)))
    d.router.step(1, d.tick(1, 99)/R, d.now)
    assert d.router.states['beam'][64] < 0
    d.router.step(1, d.tick(1, 5000)/R, d.now)
    assert d.router.states['beam'][64] == pytest.approx(d.tick(1, 120)/R)
    d.feed(1, message(UNIX+1, charge(110)))
    assert d.router.states['beam'][64] == pytest.approx(d.tick(1, 120)/R)


def test_repeated_pixel_keeps_beam_light_and_unrelated_hit_separate():
    d = Driver().ready()
    d.feed(1, message(UNIX+1, charge(120), charge(20_120), charge(40_120)))
    d.feed(5, message(UNIX+1, aux('T', 2, 100)))
    d.feed(6, message(UNIX+1, aux('T', 2, 20_100)))
    d.paint(50_000)
    assert d.router.states['beam'][64] == pytest.approx(d.tick(1, 120)/R)
    assert d.router.states['light'][64] == pytest.approx(d.tick(1, 20_120)/R)
    assert d.router.matchers[1].n_history == 3  # one history, not one per source


def test_overlap_counts_once_per_source_and_once_in_union():
    d = Driver().ready()
    d.feed(1, message(UNIX+1, charge(150)))
    for i in (5, 6, 5, 6):
        d.feed(i, message(UNIX+1, aux('T', 2, 100)))
    m = d.router.matchers[1]
    assert m.stats['matched_hits'] == 1
    assert all(s['matched_hits'] == 1 for s in m.source_stats.values())


def test_subtype_filter_does_not_reclassify_sources():
    d = Driver(trigger_types=(7,)).ready()
    d.feed(5, message(UNIX+1, aux('T', 2, 100), aux('T', 7, 5000)))
    d.feed(1, message(UNIX+1, charge(101), charge(5001, channel=1)))
    d.paint()
    assert np.flatnonzero(d.router.states['beam'] >= 0).tolist() == [65]
    assert d.router.stats['filtered_subtype_triggers'] == 1


def test_raw_veto_boundary_and_normal_clock_not_modified():
    d = Driver().ready()
    d.feed(5, message(UNIX+1, aux('T', 2, 0)))
    before_offset = d.clocks[1].unroller.offset
    h, ticks, valid, selected = d.feed(1, message(UNIX+1,
        charge(0), charge(9, channel=1), charge(10, channel=2)))
    assert selected.tolist() == [False, False, True]
    assert ticks.tolist() == [before_offset, before_offset+9, before_offset+10]
    d.paint()
    assert np.flatnonzero(d.router.states['beam'] >= 0).tolist() == [66]
    assert h.counters['data_hits'] == 3


def test_batch_boundaries_track_only_nonempty_valid_data_envelopes():
    h = decode_batch([message(11, charge(11)), b'bad', message(22, kind=b'?'),
                      message(33), message(44, aux(), aux('T', 2, 100))])
    assert h.message_ends.tolist() == [1, 3]
    assert h.message_seconds.tolist() == [11, 44]
    assert h.counters['malformed'] == 1
    assert h.word_indices.tolist() == [0]


def test_trigger_only_and_sync_only_batches_retain_header_metadata():
    for word in [aux(), aux('T', 2, 100)]:
        h = decode_batch([message(UNIX, word)])
        assert len(h.timestamp) == 0
        assert h.message_ends.tolist() == [1]
        assert h.message_seconds.tolist() == [UNIX]


def test_two_distinct_observations_required_and_integer_precision():
    a = CommonPpsAligner([1], TimingConfig())
    a.observe(1, R, UNIX, 1.)
    assert a.offset(1, 1.) is None
    a.observe(1, 2*R, UNIX+1, 2.)
    assert a.offset(1, 2.) == UNIX*R-R
    assert isinstance(a.offset(1, 2.), int)
    assert a.offset(1, 2.) > 2**53


def test_no_future_sync_qualifies_earlier_trigger_in_same_batch():
    d = Driver(iogs=[1, 5])
    for i in d.iogs:
        d.feed(i, message(UNIX, aux()))
    d.feed(1, message(UNIX+1, aux(), charge(200)))
    d.feed(5, message(UNIX, aux('T', 2, R-10)),
             message(UNIX+1, aux(), aux('T', 2, 100)))
    assert d.router.stats['source_unaligned_triggers'] == 1
    assert d.router.targets[1]['routed_beam'] == 1


def test_multiple_syncs_in_one_envelope_are_ambiguous_not_backfilled():
    d = Driver(iogs=[1])
    d.feed(1, message(UNIX+1, aux(), aux()))
    state = d.router.aligner.states[1]
    assert state.status == 'suspect'
    assert state.offset_ticks is None
    assert state.invalid_observations == 2


@pytest.mark.parametrize('kind,subtype', [('S', 72), ('T', 83)])
def test_heartbeat_and_trigger_do_not_supply_pps_votes(kind, subtype):
    a = CommonPpsAligner([1], TimingConfig())
    h = decode_batch([message(UNIX, aux(kind, subtype, R))])
    a.observe_batch(1, h, (0, False), 1.)
    assert a.states[1].consistent_observations == 0


@pytest.mark.parametrize('stamp', [0, 1, R//2, R+R//10])
def test_invalid_sync_cannot_qualify(stamp):
    d = Driver(iogs=[1])
    d.feed(1, message(UNIX, aux(timestamp=stamp)))
    assert d.router.aligner.offset(1, d.now) is None


def test_inconsistent_header_revokes_mapping_and_never_silently_retimes():
    d = Driver().ready()
    d.feed(1, message(UNIX+1, charge(120)))
    d.feed(5, message(UNIX+1, aux('T', 2, 100)))
    d.paint()
    original = d.router.aligner.states[1].offset_ticks
    assert d.router.states['beam'][64] >= 0
    for second in [UNIX+3, UNIX+4, UNIX+5]:
        d.feed(1, message(second, aux()))  # persistent one-second changed label
    assert d.router.aligner.states[1].offset_ticks == original
    assert d.router.aligner.states[1].status == 'suspect'
    assert np.all(d.router.states['beam'] < 0)
    assert d.router.matchers[1].stats['association_resets'] == 1


def test_original_offset_recovers_after_two_consistent_pps_labels():
    a = CommonPpsAligner([1], TimingConfig())
    a.observe(1, R, UNIX, 1.)
    a.observe(1, 2*R, UNIX+1, 2.)
    original = a.offset(1, 2.)
    a.observe(1, 3*R, UNIX+3, 3.)
    assert a.offset(1, 3.) is None
    # Skip one physical second to avoid duplicated header; positive multi-period
    # increments are supported by the canonical unroller and by this label check.
    a.observe(1, 5*R, UNIX+4, 5.)
    assert a.offset(1, 5.) is None
    a.observe(1, 6*R, UNIX+5, 6.)
    assert a.offset(1, 6.) == original


def test_stale_source_does_not_block_other_healthy_sources():
    d = Driver().ready()
    d.now += 3.
    # IOG 6 is absent. Other streams requalify after freshness expires.
    for i in [1, 2, 3, 4, 5, 7, 8]:
        d.feed(i, message(UNIX+2, aux()), message(UNIX+3, aux()))
    d.feed(1, message(UNIX+3, charge(120)))
    d.feed(5, message(UNIX+3, aux('T', 2, 100)))
    d.paint()
    assert d.router.states['beam'][64] >= 0
    assert d.router.aligner.states[6].status == 'stale'
    assert d.router.targets[6]['unaligned_target_skips'] == 1


def test_missing_pps_detected_from_detector_frontier_even_in_fast_replay():
    d = Driver().ready()
    d.feed(1, message(UNIX+1, charge(100, receipt=3*R)))
    assert d.router.aligner.states[1].status == 'suspect'


def test_missing_message_headers_never_infer_epoch_from_host_arrival():
    a = CommonPpsAligner([1], TimingConfig())
    h = decode_batch([message(UNIX, aux())])
    h.message_ends = h.message_seconds = None
    a.observe_batch(1, h, (0, False), 1.)
    assert a.offset(1, 1.) is None
    assert a.states[1].invalid_observations == 1


def test_non_one_second_clock_configuration_disables_common_label_alignment():
    a = CommonPpsAligner([1], TimingConfig(tick_seconds=2e-7))
    a.observe(1, R, UNIX, 1.)
    a.observe(1, 2*R, UNIX+1, 2.)
    assert not a.supported and a.offset(1, 2.) is None


def test_pre_pps_and_out_of_period_trigger_are_rejected():
    d = Driver(iogs=[5])
    d.feed(5, message(UNIX, aux('T', 2, 100), aux()))
    d.feed(5, message(UNIX+1, aux(), aux('T', 2, R+1)))
    assert d.router.stats['invalid_trigger_times'] == 2


def test_disabled_view_has_no_hit_history_or_layer_arrays():
    r = DetectorTriggerRouter(10, [1, 5, 6], TimingConfig(), ObserverConfig())
    assert not r.matchers and not r.states


def test_history_and_per_source_queues_are_bounded():
    d = Driver(max_history_hits=3, max_chunks=2).ready()
    for n in range(10):
        d.feed(1, message(UNIX+1, charge(100+n*10), charge(101+n*10)))
    m = d.router.matchers[1]
    assert m.n_history <= 3 and len(m.history) <= 2
    assert m.stats['history_capacity_dropped_hits'] > 0
    for source in (5, 6):
        d.feed(source, message(UNIX+1, aux('T', 2, 100)))
    for layer in m.layers.values():
        assert layer.pending <= 3 and len(layer.heap) <= 2


def test_storm_work_is_capped_and_counters_expose_drops():
    d = Driver(max_windows=2).ready()
    d.feed(5, message(UNIX+1, *[aux('T', 2, n*10000) for n in range(10)]))
    assert d.router.stats['trigger_batch_limit_drops'] == 8
    assert d.router.targets[1]['routed_beam'] == 2


def test_outside_history_trigger_reports_incomplete_coverage():
    d = Driver(history_seconds=.1).ready()
    d.feed(1, message(UNIX+1, charge(5_000_000)))
    d.feed(5, message(UNIX+1, aux('T', 2, 100)))
    assert d.router.matchers[1].source_stats['beam']['outside_history_triggers'] == 1


def test_diagnostics_are_json_serializable_with_explicit_calibration_caveat():
    d = Driver().ready()
    payload = d.router.summary(d.now)
    json.dumps(payload, allow_nan=False)
    assert payload['common_timing']['hardware_phase_verified'] is False
    assert payload['trigger_routing']['source_iogs'] == {'beam': 5, 'light': 6}


@pytest.mark.parametrize('seed', range(10))
def test_randomized_multi_iog_repeated_pixels_match_independent_oracle(seed):
    rng = np.random.default_rng(seed)
    d = Driver().ready()
    beam = rng.integers(100, R-4000, size=15)
    light = rng.integers(100, R-4000, size=18)
    expected = {name: np.full(1024, -1.) for name in ['beam', 'light']}
    # The same channel fires many times; include guaranteed near-boundary cases.
    phases = np.r_[rng.integers(100, R-4000, size=80), beam, beam+1899, beam+1900,
                   light, light+1899, light+1900]
    channels = rng.integers(0, 8, size=len(phases))
    order = np.argsort(phases)
    phases, channels = phases[order], channels[order]
    for i in d.iogs:
        words = [charge(int(t), channel=int(c)) for t, c in zip(phases, channels)]
        step = int(rng.integers(1, 22))
        for start in range(0, len(words), step):
            d.feed(i, message(UNIX+1, *words[start:start+step]))
        for name, triggers in [('beam', beam), ('light', light)]:
            selected = np.any((phases[:, None] >= triggers) &
                              (phases[:, None] < triggers+1900), axis=1)
            np.maximum.at(expected[name], i*64+channels[selected], (d.tick(i)+phases[selected])/R)
    d.feed(5, message(UNIX+1, *[aux('T', 2, int(t)) for t in sorted(beam)]))
    d.feed(6, message(UNIX+1, *[aux('T', 2, int(t)) for t in sorted(light)]))
    d.paint(R)
    for name in expected:
        np.testing.assert_allclose(d.router.states[name], expected[name], rtol=0, atol=1e-14)
