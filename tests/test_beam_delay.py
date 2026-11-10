"""Beam t0 offset remains detector-wide, source-specific and drift-safe."""
import pytest

from raw_display.trigger_windows import ObserverConfig
from test_detector_triggers import Driver, UNIX, R, aux, charge, message


@pytest.mark.parametrize('view3d', [False, True])
def test_delayed_beam_has_exact_half_open_190us_window_on_all_iogs(view3d):
    d = Driver(view3d=view3d, beam_delay_us=220).ready()
    # Trigger T at tick 100 -> corrected Beam t0 at 2300; 190 us = 1900 ticks.
    phases = [2299, 2300, 4199, 4200]
    for iog in d.iogs:
        d.feed(iog, message(UNIX+1, *(charge(t, channel=c) for c, t in enumerate(phases))))
    d.feed(5, message(UNIX+1, aux('T', 2, 100)))
    d.paint()
    assert d.router.beam_delay_ticks == 2200
    for iog in d.iogs:
        start = 64*iog
        assert (d.router.states['beam'][start:start+4] >= 0).tolist() == [False, True, True, False]
        if view3d:
            assert d.router.drift_ticks['beam'][start:start+4].tolist() == [-1, 0, 1899, -1]


@pytest.mark.parametrize('view3d', [False, True])
def test_light_stays_unshifted_when_beam_delay_is_configured(view3d):
    d = Driver(view3d=view3d, beam_delay_us=220).ready()
    for iog in d.iogs:
        d.feed(iog, message(UNIX+1, charge(101), charge(2301, channel=1)))
    d.feed(6, message(UNIX+1, aux('T', 2, 100)))
    d.feed(5, message(UNIX+1, aux('T', 2, 100)))
    d.paint()
    for iog in d.iogs:
        start = 64*iog
        assert (d.router.states['beam'][start:start+2] >= 0).tolist() == [False, True]
        assert (d.router.states['light'][start:start+2] >= 0).tolist() == [True, False]
        if view3d:
            assert d.router.drift_ticks['beam'][start:start+2].tolist() == [-1, 1]
            assert d.router.drift_ticks['light'][start:start+2].tolist() == [1, -1]


def test_delayed_beam_crosses_pps_boundary_and_keeps_integer_drift_ticks():
    d = Driver(view3d=True, beam_delay_us=220).ready()
    # The raw trigger belongs to the previous second, but corrected t0 is
    # exactly five ticks before the following PPS epoch.
    d.feed(5, message(UNIX+1, aux('T', 2, R-2205)))
    for iog in d.iogs:
        d.feed(iog, message(UNIX+2, aux(), charge(4),
                            charge(1894, channel=1), charge(1895, channel=2)))
    d.paint(12000)
    for iog in d.iogs:
        start = 64*iog
        assert (d.router.states['beam'][start:start+3] >= 0).tolist() == [True, True, False]
        assert d.router.drift_ticks['beam'][start:start+3].tolist() == [9, 1899, -1]


def test_beam_delay_defaults_to_zero_and_env_override_is_read(monkeypatch):
    monkeypatch.delenv('RAW_DISPLAY_BEAM_DELAY_US', raising=False)
    assert ObserverConfig.from_env().beam_delay_us == 0.
    monkeypatch.setenv('RAW_DISPLAY_BEAM_DELAY_US', '220')
    assert ObserverConfig.from_env().beam_delay_us == 220.
    assert Driver().router.beam_delay_ticks == 0


@pytest.mark.parametrize('invalid', ['nan', 'inf', '-inf', '100001', '-100001'])
def test_bad_beam_delay_is_rejected(monkeypatch, invalid):
    monkeypatch.setenv('RAW_DISPLAY_BEAM_DELAY_US', invalid)
    with pytest.raises(ValueError, match='beam trigger delay'):
        ObserverConfig.from_env()
