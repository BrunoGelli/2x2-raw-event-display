import struct
import numpy as np
import pytest
from raw_display.drift_state import PairedDueLayer, encode_drift_frame, RECORD
from raw_display.trigger_windows import ObserverConfig
from raw_display.timing import TimingConfig
from test_detector_triggers import Driver, UNIX, R, message, charge, aux


@pytest.mark.parametrize('view3d', [False, True])
@pytest.mark.parametrize('source,name', [(5, 'beam'), (6, 'light')])
def test_default_190_boundaries_on_all_iogs(view3d, source, name):
    d = Driver(view3d=view3d).ready()
    for iog in d.iogs:
        d.feed(iog, message(UNIX+1, *(charge(t, channel=c) for c, t in enumerate([99,100,1999,2000]))))
    d.feed(source, message(UNIX+1, aux('T', 2, 100)))
    d.paint()
    for iog in d.iogs:
        start = iog * 64
        assert (d.router.states[name][start:start+4] >= 0).tolist() == [False, True, True, False]
        if view3d:
            assert d.router.drift_ticks[name][start:start+4].tolist() == [-1, 0, 1899, -1]


@pytest.mark.parametrize('late', [False, True])
def test_overlap_reassociates_same_hit_to_latest_preceding_trigger(late):
    d = Driver(view3d=True).ready()
    d.feed(5, message(UNIX+1, aux('T', 2, 100)))
    if not late:
        d.feed(5, message(UNIX+1, aux('T', 2, 150), aux('T', 2, 250)))
    d.feed(1, message(UNIX+1, charge(200)))
    d.paint()
    if late:
        assert d.router.drift_ticks['beam'][64] == 100
        d.feed(5, message(UNIX+1, aux('T', 2, 250), aux('T', 2, 150), aux('T', 2, 120)))
    assert d.router.drift_ticks['beam'][64] == 50
    assert d.router.matchers[1].source_stats['beam']['matched_hits'] == 1
    # An older trigger arriving later never replaces a more recent t0.
    d.feed(5, message(UNIX+1, aux('T', 2, 110)))
    assert d.router.drift_ticks['beam'][64] == 50


def test_pending_reassociation_and_repeated_pixels_keep_atomic_latest_pairs():
    d = Driver(view3d=True).ready()
    d.feed(1, message(UNIX+1, charge(120), charge(200), charge(150)))
    d.feed(5, message(UNIX+1, aux('T', 2, 100), aux('T', 2, 180)))
    d.feed(6, message(UNIX+1, aux('T', 2, 110)))
    d.router.step(1, d.tick(1, 160)/R, d.now)
    assert d.router.drift_ticks['beam'][64] == 50
    d.paint()
    assert d.router.states['beam'][64] == pytest.approx(d.tick(1,200)/R)
    assert d.router.drift_ticks['beam'][64] == 20
    assert d.router.drift_ticks['light'][64] == 90
    d.feed(1, message(UNIX+1, charge(140), charge(5000)))
    d.paint()
    assert d.router.drift_ticks['beam'][64] == 20
    assert d.router.drift_ticks['light'][64] == 90


def test_pps_rollover_and_unequal_local_epochs_preserve_exact_delta():
    d = Driver(view3d=True).ready()
    d.feed(6, message(UNIX+1, aux('T', 2, R-20)))
    for iog in d.iogs:
        d.feed(iog, message(UNIX+2, aux(), charge(15)))
    d.paint()
    for iog in d.iogs:
        assert d.router.drift_ticks['light'][iog*64] == 35


def test_stale_or_suspect_alignment_clears_pairs_and_pending_matches():
    d = Driver(view3d=True).ready()
    d.feed(1, message(UNIX+1, charge(120)))
    d.feed(5, message(UNIX+1, aux('T', 2, 100)))
    d.paint()
    d.router.expire(d.now+3)
    assert all(np.all(a == -1) for a in d.router.drift_ticks.values())
    assert all(np.all(a == -1) for a in d.router.states.values())
    assert all(not m.history and all(not layer.heap for layer in m.layers.values()) for m in d.router.matchers.values())


def test_pre_window_does_not_create_negative_drift_and_optional_state_is_absent():
    d = Driver(view3d=True, pre_us=10).ready()
    d.feed(1, message(UNIX+1, charge(90)))
    d.feed(5, message(UNIX+1, aux('T', 2, 100)))
    d.paint()
    assert d.router.states['beam'][64] >= 0  # preserve configured 2D pre-window
    assert d.router.drift_ticks['beam'][64] == -1
    off = Driver()
    assert not off.router.drift_ticks
    assert all(not m.paired and not m.trigger_ticks for m in off.router.matchers.values())


def test_overlap_history_and_pending_capacity_remain_bounded():
    d = Driver(view3d=True,max_windows=2,max_chunks=2,max_history_hits=3).ready()
    d.feed(1,message(UNIX+1,charge(500),charge(550),charge(600)))
    d.feed(5,message(UNIX+1,aux('T',2,100),aux('T',2,110)))
    d.feed(5,message(UNIX+1,aux('T',2,120),aux('T',2,130)))
    m=d.router.matchers[1]
    assert len(m.trigger_ticks['beam'])==2
    assert m.source_stats['beam']['association_trigger_limit_drops']==2
    assert m.layers['beam'].pending<=3 and len(m.layers['beam'].heap)<=2
    assert m.layers['beam'].dropped_hits>0


def test_binary_delta_update_without_new_hit_and_clear():
    old={name:(np.array([1234567.000012]),np.array([120],dtype=np.int64)) for name in ('beam','light')}
    new={name:(pair[0].copy(),pair[1].copy()) for name,pair in old.items()}
    new['beam'][1][0]=20
    frame=encode_drift_frame(old,new,12)
    assert struct.unpack_from('<4sII',frame)==(b'R3D1',12,1)
    rec=np.frombuffer(frame,dtype=RECORD,offset=12)[0]
    assert (rec['id'],rec['source'],rec['hit_s'],rec['dt_ticks'])==(0,0,1234567.000012,20)
    cleared={name:(np.array([-1.]),np.array([-1],dtype=np.int64)) for name in old}
    assert struct.unpack_from('<4sII',encode_drift_frame(new,cleared,13))==(b'R3D1',13,2)


def test_feature_gate_and_window_override(monkeypatch):
    monkeypatch.setenv('RAW_DISPLAY_3D','1')
    monkeypatch.delenv('RAW_DISPLAY_TRIGGER_POST_US',raising=False)
    cfg=ObserverConfig.from_env()
    assert cfg.view3d and cfg.trigger_view and cfg.post_us==190
    monkeypatch.setenv('RAW_DISPLAY_TRIGGER_POST_US','230')
    assert ObserverConfig.from_env().post_us==230
