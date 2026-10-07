"""Offline evaluation of the read-only acceptance helper."""
import copy
from pathlib import Path
import importlib.util
import pytest

spec=importlib.util.spec_from_file_location('verify_detector_triggers',
    Path(__file__).resolve().parents[1]/'tools'/'verify_detector_triggers.py')
v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)


def fixture():
    s=dict(elapsed_s=0.,status=dict(collector_healthy=True,
        sources=[dict(iog=i,mapped_hits=100,display_selected_hits=100,triggers=1,
            timing=dict(timing_eligible_hits=100,pre_cut_late_hits=0,invalid_times=0)) for i in (5,6)]),
        observers=dict(session_id='same',enabled=True,feature_build=v.FEATURE,capture_age_s=.1,
            captured_monotonic=100.,common_timing=dict(supported=True,stale_after_s=2.5,
                sources=[dict(iog=i,status='aligned',last_valid_age_s=.1,offset_ticks=10000000000+i) for i in (5,6)])))
    t=copy.deepcopy(s);t['elapsed_s']=120.;t['observers']['captured_monotonic']=220.
    return [s,t]


def test_pass_never_approves_release_or_invents_trigger_coverage():
    r=v.evaluate(fixture(),[5,6]);assert r['software_checks_passed']
    assert not r['v1_release_approved'] and r['manual_checks_remaining']
    assert all(s['delta_triggers']==0 for s in r['iog_deltas'])


@pytest.mark.parametrize('change',['stale','session','offset','drop','missing','unhealthy','period'])
def test_rejects_incomplete_or_unhealthy_interval(change):
    samples=fixture();s=samples[-1];o=s['observers']
    if change=='stale':o['common_timing']['sources'][0]['last_valid_age_s']=3.
    elif change=='session':o['session_id']='new'
    elif change=='offset':o['common_timing']['sources'][0]['offset_ticks']+=10000000
    elif change=='drop':s['status']['sources'][0]['timing']['invalid_times']=1
    elif change=='missing':o['common_timing']['sources'].pop()
    elif change=='unhealthy':s['status']['collector_healthy']=False
    elif change=='period':o['common_timing']['supported']=False
    assert not v.evaluate(samples,[5,6])['software_checks_passed']


def test_reject_redirect():
    from urllib.error import HTTPError
    from urllib.request import Request
    with pytest.raises(HTTPError):
        v.NoRedirect().redirect_request(Request('http://127.0.0.1:8765/api/status'),None,
            302,'redirect',{},'http://other.example/api/status')
