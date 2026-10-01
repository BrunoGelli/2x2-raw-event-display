"""Offline regression and localhost integration only; never detector endpoints."""
import json
import multiprocessing as mp
from pathlib import Path
import struct
import subprocess
import time
import numpy as np
import pytest
import zmq
from fastapi.testclient import TestClient
from raw_display.codec import HEADER, WORD, decode_batch, make_message
from raw_display.timing import TimingConfig, DetectorPlayback
from raw_display.trigger_windows import ObserverConfig, project_triggers, TriggerWindows, DueLayer
from raw_display.late_audit import LateAudit
from raw_display.observer_runtime import (Observers, attach_observers, read_observers,
    snapshot_with_triggers, encode_trigger_frame, RECORD)
from raw_display.geometry import demo_geometry
from raw_display.runtime import make_shared, COL, FIELDS, stop_collector
from raw_display.timed_runtime import start_timed_collector
from raw_display.timed_server import create_timed_app
from raw_display.timing import TIMING_FIELDS
from raw_display.post_sync import sync83_message

R=10_000_000

def aux(kind, subtype, timestamp):
    return struct.pack('<BB2xI8x',ord(kind),subtype,timestamp)

def charge(t, receipt=None, io=1, chip=11, channel=0):
    b=bytearray(make_message(io,chip,channel,timestamp=t,downstream=True)[8:])
    struct.pack_into('<I',b,2,t if receipt is None else receipt)
    return bytes(b)

def message(*words):
    return HEADER.pack(b'D',123,len(words))+b''.join(words)

def ingest(clock, raw):
    hits=decode_batch([raw]);initial=(clock.unroller.offset,clock.unroller.initial_tick is not None)
    ids=np.arange(len(hits.timestamp),dtype=np.int32)
    return hits,ids,initial,clock.ingest(ids,hits)


@pytest.mark.parametrize('kind,subtype,expected',[('T',0,1),('T',2,1),('T',72,1),('T',83,1),('S',83,0),('S',72,0)])
def test_trigger_kind_not_sync_or_heartbeat(kind,subtype,expected):
    h=decode_batch([message(aux('S',83,R),aux(kind,subtype,100))])
    types,ticks,valid=project_triggers(h,(0,False),TimingConfig())
    assert len(types)==expected
    if expected: assert valid.tolist()==[True] and ticks.tolist()==[R+100]


def test_trigger_before_first_pps_is_not_assigned_an_epoch():
    h=decode_batch([message(aux('T',2,100),aux('S',83,R),aux('T',2,200))])
    types,ticks,valid=project_triggers(h,(0,False),TimingConfig())
    assert valid.tolist()==[False,True]
    assert ticks.tolist()==[100,R+200]


def test_trigger_word_timestamp_offset_and_multisync_batch():
    h=decode_batch([message(aux('S',83,R),aux('T',2,R-10),aux('S',83,R),aux('T',7,20))])
    types,ticks,valid=project_triggers(h,(0,False),TimingConfig())
    assert ticks.tolist()==[2*R-10,2*R+20]
    assert types.tolist()==[2,7] and valid.all()


def test_trigger_counter_spanning_reset_period_is_flagged_not_guessed():
    h=decode_batch([message(aux('T',2,R+10))])
    assert not project_triggers(h,(R,True),TimingConfig())[2].any()


def test_unroller_result_return_does_not_change_cut_frontier_or_raw_counts():
    c=DetectorPlayback(np.full(5,-1.),TimingConfig())
    h,ids,initial,(ticks,valid,selected)=ingest(c,message(aux('S',83,R),charge(0),charge(9),charge(10),charge(20)))
    assert selected.tolist()==[False,False,True,True]
    assert h.counters['data_hits']==4 and c.unroller.frontier_tick==R+20
    assert ticks.tolist()==[R,R+9,R+10,R+20]


def setup_matcher(**kwargs):
    tc=TimingConfig();s=np.full(64,-1.)
    return TriggerWindows(s,tc,ObserverConfig(trigger_view=True,**kwargs)),s


def test_exact_window_boundaries_and_real_time_age():
    m,s=setup_matcher(post_us=300)
    ticks=np.array([R+99,R+100,R+3099,R+3100])
    m.consume([0,1,2,3],ticks,np.array([2]),np.array([R+100]),np.array([True]),R+4000)
    m.layer.step((R+5000)/R)
    assert np.flatnonzero(s>=0).tolist()==[1,2]
    np.testing.assert_allclose(s[[1,2]],ticks[[1,2]]/R)


def test_late_trigger_recovers_previously_received_hits_without_repainting_fresh():
    m,s=setup_matcher()
    m.consume([0],[R+120],np.array([],dtype='u1'),np.array([],dtype='i8'),np.array([],dtype=bool),R+5000)
    m.layer.step(1.1)
    m.consume([],[],np.array([2]),np.array([R+100]),np.array([True]),R+10000)
    assert s[0]==pytest.approx(1.000012)
    assert 1.1-s[0]>.09


def test_same_pixel_newer_nontrigger_hit_does_not_overwrite_tagged_layer():
    m,s=setup_matcher()
    m.consume([0,0],[R+100,R+5000],np.array([2]),np.array([R+100]),np.array([True]),R+10000)
    m.layer.step(1.01)
    assert s[0]==pytest.approx(1.00001)


def test_overlapping_windows_do_not_double_count_hits():
    m,s=setup_matcher()
    m.consume([0],[R+200],np.array([2,2]),np.array([R+100,R+150]),np.array([True,True]),R+4000)
    m.consume([],[],np.array([2]),np.array([R+100]),np.array([True]),R+5000)
    assert m.stats['matched_hits']==1


def test_cross_pps_window_uses_same_unrolled_epoch():
    c=DetectorPlayback(np.full(4,-1.),TimingConfig(min_raw_timestamp=0))
    raw=message(aux('S',83,R),aux('T',2,R-20),charge(R-10),aux('S',83,R),charge(15))
    h,ids,initial,(ticks,valid,selected)=ingest(c,raw)
    types,tt,tv=project_triggers(h,initial,c.config)
    m,s=setup_matcher()
    m.consume(ids[selected],ticks[selected],types,tt,tv,c.unroller.frontier_tick)
    m.layer.step(2.1)
    assert np.flatnonzero(s>=0).tolist()==[0,1]
    assert ticks.tolist()==[2*R-10,2*R+15]


def test_subtype_selection_does_not_select_other_triggers():
    m,s=setup_matcher(trigger_types=(2,))
    m.consume([0,1],[R+100,R+10000],np.array([7,2]),np.array([R+100,R+10000]),np.array([True,True]),R+20000)
    m.layer.step(1.1)
    assert np.flatnonzero(s>=0).tolist()==[1]


def test_history_capacity_bounded_and_expiration_visible():
    m,s=setup_matcher(max_history_hits=3,max_chunks=2)
    for k in range(10):
        m.consume([0,1],[R+k*10,R+k*10+1],np.array([],dtype='u1'),np.array([],dtype='i8'),np.array([],dtype=bool),R+100)
    assert m.n_history<=3 and len(m.history)<=2
    assert m.stats['history_capacity_dropped_hits']>0
    m.consume([],[],np.array([2]),np.array([R]),np.array([True]),4*R)
    assert m.stats['outside_history_triggers']==1


def test_matched_layer_future_and_late_maximum():
    s=np.full(4,-1.);tc=TimingConfig();d=DueLayer(s,tc,ObserverConfig(trigger_view=True))
    d.add([0,0,1],np.array([1.1,1.5,1.3])*R)
    d.step(1.2);assert s[0]==pytest.approx(1.1) and s[1]<0
    d.step(1.6);d.add([0],[R]);assert s[0]==pytest.approx(1.5)


def test_layer_limit_drops_not_blocks():
    d=DueLayer(np.full(5,-1.),TimingConfig(),ObserverConfig(max_history_hits=2))
    d.add([0,1,2],[R,R+1,R+2]);assert d.dropped_hits==3 and not d.heap


@pytest.mark.parametrize('kw',[{'pre_us':-1},{'post_us':0},{'post_us':float('nan')},
 {'history_seconds':20},{'max_history_hits':0},{'trigger_types':(256,)},{'audit_iogs':(9,)}])
def test_config_rejects_bad_values(kw):
    with pytest.raises(ValueError):ObserverConfig(**kw)


def test_environment_defaults_and_errors(monkeypatch):
    for key in list(__import__('os').environ):
        if key.startswith('RAW_DISPLAY_TRIGGER_') or key=='RAW_DISPLAY_LATE_AUDIT_IOGS':monkeypatch.delenv(key)
    assert not ObserverConfig.from_env().trigger_view
    monkeypatch.setenv('RAW_DISPLAY_TRIGGER_VIEW','1');monkeypatch.setenv('RAW_DISPLAY_LATE_AUDIT_IOGS','6')
    assert ObserverConfig.from_env().audit_iogs==(6,)
    monkeypatch.setenv('RAW_DISPLAY_TRIGGER_VIEW','oops')
    with pytest.raises(ValueError):ObserverConfig.from_env()


def test_audit_locates_raw_counter_exceeding_period_and_keeps_packet_data():
    tc=TimingConfig();clock=DetectorPlayback(np.full(8,-1.),tc)
    ingest(clock,message(aux('S',83,R),aux('S',83,R),aux('S',83,R)))
    clock.cursor=2.8
    raw=message(charge(3*R+100,300,io=17,chip=31,channel=5),charge(5_000_000,5_000_001,io=21,chip=11))
    h,ids,initial,result=ingest(clock,raw)
    old=h.words.tobytes();a=LateAudit(6,tc);a.record(h,result[0],result[2],clock.cursor,initial)
    summary=a.summary()
    assert summary['totals']['selected_hits']==2
    assert summary['totals']['late_raw_ge_period']==1
    assert summary['top_late_chips'][0]['tile'] in (5,6)
    assert any(x['tile']==5 and x['chip']==31 and x['raw_ge_period']==1 for x in summary['top_late_chips'])
    assert summary['samples'][0]['raw_cycles']==3
    assert h.words.tobytes()==old


def test_audit_histograms_sum_to_counts():
    tc=TimingConfig();c=DetectorPlayback(np.full(4,-1.),tc);c.cursor=1.5
    h,ids,initial,result=ingest(c,message(aux('S',83,R),charge(100,200),charge(6_000_000,6_100_000)))
    a=LateAudit(1,tc);a.record(h,result[0],result[2],c.cursor,initial);d=a.summary()
    assert sum(d['lateness_counts'])==d['totals']['late_hits']==1
    assert sum(d['inferred_asic_to_receipt_counts'])==2


def test_sync_metadata_trigger_marker_counts_T_words_not_S():
    stats=np.zeros((9,len(FIELDS)))
    stats[1,COL['sync83_packets']]=5;stats[1,COL['last_sync83']]=10.
    stats[1,COL['triggers']]=2;stats[1,COL['last_trigger']]=9.5
    p=sync83_message(stats,[1],10.1,COL,include_triggers=True)['sources'][0]
    assert p['trigger_count']==2 and p['count']==5 and p['trigger_age_s']==pytest.approx(.6)


def test_nonblocking_diagnostic_writer():
    ctx=mp.get_context('spawn');shared=make_shared(ctx,8);cfg=ObserverConfig(audit_iogs=(1,))
    attach_observers(ctx,shared,8,[1],cfg)
    o=Observers(8,[1],TimingConfig(),cfg,shared)
    with shared.observer_lock:o.publish_diagnostics(100.)
    assert o.skipped_diagnostics==1
    o.publish_diagnostics(101.1);assert read_observers(shared)['skipped_diagnostic_publications']==1


def test_iog_independence_and_no_extra_clocks():
    ctx=mp.get_context('spawn');shared=make_shared(ctx,8);cfg=ObserverConfig(trigger_view=True)
    attach_observers(ctx,shared,8,[1,2],cfg)
    o=Observers(8,[1,2],TimingConfig(),cfg,shared)
    for iog in (1,2):
        c=DetectorPlayback(np.full(8,-1.),TimingConfig())
        h,ids,initial,result=ingest(c,message(aux('S',83,R),aux('T',2,100),charge(200)) if iog==1 else message(aux('S',83,R),charge(200)))
        ids=ids+iog  # disjoint physical pixels
        o.record(iog,ids,h,result,c,initial);o.step(iog,1.01)
    assert o.tagged[1]>0 and o.tagged[2]<0


class Alive:
    def is_alive(self):return True


def shared_fixture(monkeypatch):
    ctx=mp.get_context('spawn');geo=demo_geometry(iogs=[1],chips_per_tile=1)
    shared=make_shared(ctx,len(geo.pixels));cfg=ObserverConfig(trigger_view=True,audit_iogs=(1,))
    attach_observers(ctx,shared,len(geo.pixels),[1],cfg)
    shared.timing=ctx.RawArray('d',9*len(TIMING_FIELDS));shared.cpu_ratio=ctx.RawValue('d',0.)
    shared.rate_ring=None;shared.heartbeat.value=time.monotonic()
    np.frombuffer(shared.seen).fill(-1.);np.frombuffer(shared.trigger_seen)[0]=1.5
    return geo,shared


def test_http_ws_trigger_frame_opt_in_and_old_protocol_retained(monkeypatch):
    geo,shared=shared_fixture(monkeypatch)
    with TestClient(create_timed_app(geo,shared,Alive())) as client:
        assert client.get('/api/geometry').json()['trigger_view_enabled']
        assert client.get('/api/timing-audit').status_code==200
        with client.websocket_connect('/ws?sync83=1&trigger_windows=1') as ws:
            for _ in range(10):
                meta=ws.receive_json();assert meta['sources'][0]['trigger_count']==0
                tag=ws.receive_bytes();magic,seq,n=struct.unpack_from('<4sII',tag)
                assert magic==b'RDT1'
                raw=ws.receive_bytes();assert raw[:4]==b'RDP2'
                ws.send_text(str(seq))
                if n:break
            assert n==1
        with client.websocket_connect('/ws') as ws:
            raw=ws.receive_bytes();magic,seq,n=struct.unpack_from('<4sII',raw)
            assert magic==b'RDP2';ws.send_text(str(seq))


def test_real_local_collector_integration(monkeypatch):
    monkeypatch.setenv('RAW_DISPLAY_TRIGGER_VIEW','1');monkeypatch.setenv('RAW_DISPLAY_LATE_AUDIT_IOGS','1')
    geo=demo_geometry(iogs=[1],chips_per_tile=1)
    ctx=zmq.Context();pub=ctx.socket(zmq.XPUB);pub.setsockopt(zmq.RCVTIMEO,8000)
    port=pub.bind_to_random_port('tcp://127.0.0.1')
    shared,proc=start_timed_collector(geo,{1:f'tcp://127.0.0.1:{port}'},TimingConfig(playback_delay=.1))
    try:
        assert pub.recv()==b'\x01'
        pub.send(message(aux('S',83,R),aux('T',2,1_000_000),charge(1_001_000,1_001_010),charge(2_000_000,2_000_010),aux('S',83,R)))
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            state,stats,beat,timing,cpu,tag=snapshot_with_triggers(shared,FIELDS,TIMING_FIELDS)
            if np.any(tag>=0) and read_observers(shared).get('audits'):break
            time.sleep(.05)
        assert proc.is_alive() and stats[1,COL['mapped_hits']]==2
        assert np.max(tag)==pytest.approx(1.1001)
        assert read_observers(shared)['sources'][0]['trigger_subtypes']=={'2':1}
        assert read_observers(shared)['audits'][0]['totals']['selected_hits']==2
    finally:
        stop_collector(shared,proc);pub.close(0);ctx.term()


def test_frontend_node_regressions():
    import shutil
    if not shutil.which('node'):pytest.skip('Node is optional; run on development machine')
    script=Path(__file__).with_name('test_trigger_view.js')
    subprocess.run(['node',str(script)],check=True,capture_output=True,text=True)


def test_old_sync_message_shape_is_unchanged_for_legacy_clients():
    stats=np.zeros((9,len(FIELDS)))
    assert sync83_message(stats,[6],100.,COL)['sources'][0]=={'iog':6,'count':0,'age_s':None}
