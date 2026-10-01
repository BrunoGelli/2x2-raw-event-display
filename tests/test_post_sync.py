"""No hardware required: raw-cut boundaries, PPS reception, and UI metadata."""
import json
import multiprocessing as mp
from pathlib import Path
import shutil
import struct
import subprocess
import time

import numpy as np
import pytest
import zmq
from fastapi.testclient import TestClient
from raw_display.codec import decode, decode_batch, make_message, HEADER, WORD
from raw_display.geometry import demo_geometry
from raw_display.post_sync import display_mask, sync83_message
from raw_display.runtime import FIELDS, COL, make_shared, snapshot, start_collector, stop_collector
from raw_display.server import create_app
from raw_display.timed_runtime import start_timed_collector, snapshot_timed
from raw_display.timed_server import create_timed_app
from raw_display.timing import TimingConfig, DetectorPlayback, TimestampUnroller, TIMING_FIELDS, TIMING_COL
from raw_display.__main__ import parser

R = 10_000_000


def syn(ts=R, subtype=83):
    return struct.pack('<2cxxI8x', b'S', bytes([subtype]), ts)


def data(ts, channel=7, receipt=None):
    w=bytearray(make_message(1,11,channel,timestamp=ts,downstream=True)[8:])
    struct.pack_into('<I',w,2,ts+10 if receipt is None else receipt)
    return bytes(w)


def msg(*words, unix=0):
    return HEADER.pack(b'D',unix,len(words))+b''.join(words)


@pytest.mark.parametrize('times,expected', [([0,1,9,10,11],[False,False,False,True,True]),
                                           ([R+5,R+9,R+10],[True,True,True]),
                                           ([],[])])
def test_exact_raw_less_than_ten(times,expected):
    assert display_mask(np.zeros(len(times)),np.asarray(times),10).tolist()==expected


def test_zero_disables_and_unknown_geometry_stays_unknown():
    assert display_mask([0,1,-1],[0,9,100],0).tolist()==[True,True,False]


@pytest.mark.parametrize('minimum',[0,1,10,100])
def test_cli_and_config_cut(minimum):
    for mode in ('asic','host'):
        a=parser().parse_args(['serve','--time-basis',mode,'--min-raw-timestamp',str(minimum)])
        assert a.min_raw_timestamp==minimum and a.host=='127.0.0.1'
    assert TimingConfig(min_raw_timestamp=minimum).min_raw_timestamp==minimum


@pytest.mark.parametrize('value',[-1,1.5,True,'10',2**31+1])
def test_invalid_config_cut_rejected(value):
    with pytest.raises(ValueError): TimingConfig(min_raw_timestamp=value)


@pytest.mark.parametrize('value',['-1','1.5','NaN','2147483649'])
def test_bad_cli_cut_rejected_before_hardware(value):
    with pytest.raises(SystemExit): parser().parse_args(['serve','--min-raw-timestamp',value])


def test_only_sync_word_subtype_83_counts():
    trigger=struct.pack('<2cxxI8x',b'T',b'S',R)
    h=decode_batch([msg(syn(),syn(subtype=72)),msg(syn(0),trigger,data(83))])
    assert h.counters['sync']==3
    assert h.counters['sync83_packets']==2  # Invalid timestamp still means received, not locked.
    assert h.counters['triggers']==1 and h.counters['data_hits']==1
    assert h.words['kind'].tolist()==[ord('S'),ord('S'),ord('S'),ord('T'),ord('D')]


def test_unroller_observes_all_hits_and_syncs_before_cut():
    h=decode(msg(syn(),data(0,0),data(9,1),data(10,2),syn(),data(5,3)))
    originals=h.words.tobytes(),h.timestamp.copy()
    states=[np.full(4,-1.),np.full(4,-1.)]
    cut=DetectorPlayback(states[0],TimingConfig(playback_delay=.1))
    raw=DetectorPlayback(states[1],TimingConfig(playback_delay=.1,min_raw_timestamp=0))
    ids=np.arange(4)
    cut.ingest(ids,h);raw.ingest(ids,h)
    assert cut.unroller.stats==raw.unroller.stats
    assert cut.unroller.offset==raw.unroller.offset==2*R
    assert cut.unroller.frontier_tick==raw.unroller.frontier_tick
    assert cut.stats['post_sync_filtered_hits']==3 and cut.stats['display_selected_hits']==1
    assert cut.stats['timing_eligible_hits']==4
    cut.step(100);cut.step(100.2)
    assert states[0][2]==pytest.approx(1.000001) and np.count_nonzero(states[0]>=0)==1
    assert h.words.tobytes()==originals[0]
    np.testing.assert_array_equal(h.timestamp,originals[1])


def test_filtered_only_batch_still_advances_clock():
    state=np.full(1,-1.)
    p=DetectorPlayback(state)
    p.ingest([0],decode(msg(syn(),data(2))))
    assert p.unroller.offset==R and p.unroller.frontier_tick==R+12
    assert p.pending_hits==0 and p.stats['post_sync_filtered_hits']==1
    p.ingest([],decode(msg(syn())))
    assert p.unroller.offset==2*R and p.unroller.stats['pps_syncs']==2


def test_unfiltered_late_diagnostic_is_preserved():
    p=DetectorPlayback(np.full(3,-1.))
    p.ingest([],decode(msg(syn())))
    p.cursor=1.2
    p.ingest([0,1,2],decode(msg(data(5,0),data(10,1),data(100,2))))
    assert p.stats['pre_cut_late_hits']==3
    assert p.stats['late_hits']==2
    assert p.stats['post_sync_filtered_hits']==1
    assert p.state[0]==-1 and p.state[1]==pytest.approx(1.000001)


def test_ntp_header_change_cannot_change_unrolling():
    words=[syn(),data(11),syn(),data(R-5,receipt=10)]
    a,b=TimestampUnroller(),TimestampUnroller()
    ta,va=a.consume(decode(msg(*words,unix=0)))
    tb,vb=b.consume(decode(msg(*words,unix=1799999999)))
    np.testing.assert_array_equal(ta,tb);np.testing.assert_array_equal(va,vb)
    assert a.stats==b.stats


def test_metadata_unknown_is_null_and_age_advances():
    stats=np.zeros((9,len(FIELDS)))
    m=sync83_message(stats,[1,6],100,COL)
    assert m['sources'][1]=={'iog':6,'count':0,'age_s':None}
    stats[6,COL['sync83_packets']]=5;stats[6,COL['last_sync83']]=98
    assert sync83_message(stats,[6],100,COL)['sources'][0]['age_s']==2
    assert sync83_message(stats,[6],101,COL)['sources'][0]['age_s']==3
    assert len(json.dumps(sync83_message(stats,range(1,9),100,COL)))<1024


class Alive:
    def is_alive(self):return True


@pytest.mark.parametrize('timed',[False,True])
def test_optional_ws_metadata_and_legacy_client(timed):
    geo=demo_geometry(iogs=[1],chips_per_tile=1)
    ctx=mp.get_context('spawn');shared=make_shared(ctx,len(geo.pixels))
    shared.heartbeat.value=time.monotonic()
    shared.timing=ctx.RawArray('d',9*len(TIMING_FIELDS));shared.cpu_ratio=ctx.RawValue('d',0.)
    shared.rate_ring=None
    if timed: np.frombuffer(shared.seen).fill(-1)
    app=create_timed_app(geo,shared,Alive()) if timed else create_app(geo,shared,Alive())
    with TestClient(app) as c:
        with c.websocket_connect('/ws') as ws:
            raw=ws.receive_bytes();ws.send_text(str(struct.unpack_from('<I',raw,4)[0]))
            assert raw[:4]==(b'RDP2' if timed else b'RDP1')
        with c.websocket_connect('/ws?sync83=1') as ws:
            meta=ws.receive_json()
            assert meta['type']=='sync83' and meta['sources'][0]['age_s'] is None
            raw=ws.receive_bytes();ws.send_text(str(struct.unpack_from('<I',raw,4)[0]))
        s=c.get('/api/status').json()
        assert s['min_raw_timestamp']==10
        assert s['sources'][0]['sync83_packets']==0 and s['sources'][0]['sync83_age_s'] is None


@pytest.mark.parametrize('timed',[False,True])
def test_local_collector_cut_and_heartbeat_isolation(timed):
    geo=demo_geometry(iogs=[1],chips_per_tile=1)
    ctx=zmq.Context();pub=ctx.socket(zmq.XPUB)
    pub.setsockopt(zmq.RCVTIMEO,6000);pub.setsockopt(zmq.XPUB_VERBOSE,1)
    port=pub.bind_to_random_port('tcp://127.0.0.1')
    endpoint={1:f'tcp://127.0.0.1:{port}'}
    shared,proc=(start_timed_collector(geo,endpoint,TimingConfig(playback_delay=.1)) if timed
                 else start_collector(geo,endpoint))
    try:
        assert pub.recv()==b'\x01'
        pub.send(msg(syn(),data(0,0),data(9,1),data(10,2),data(11,3),syn(),syn(subtype=72)))
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            snap=snapshot_timed(shared) if timed else snapshot(shared)
            state,stats=snap[:2]
            if stats[1,COL['mapped_hits']]==4 and np.count_nonzero(state>=0 if timed else state>0)==2:break
            time.sleep(.05)
        assert proc.is_alive() and stats[1,COL['mapped_hits']]==4
        assert stats[1,COL['post_sync_filtered_hits']]==2
        assert stats[1,COL['sync83_packets']]==2
        assert np.count_nonzero(state>=0 if timed else state>0)==2
        stamp=stats[1,COL['last_sync83']]
        if timed:
            assert snap[3][1,TIMING_COL['pps_syncs']]==2
        pub.send(msg(syn(subtype=72)))
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            stats=(snapshot_timed(shared) if timed else snapshot(shared))[1]
            if stats[1,COL['sync']]==4:break
            time.sleep(.05)
        assert stats[1,COL['sync83_packets']]==2 and stats[1,COL['last_sync83']]==stamp
        assert not pub.poll(100)  # only the original subscriber; no control/extra subscribers
    finally:
        stop_collector(shared,proc);pub.close(0);ctx.term()


def test_node_indicator_and_clock_regression():
    node=shutil.which('node')
    if node is None:pytest.skip('optional Node frontend test')
    result=subprocess.run([node,str(Path(__file__).with_name('test_sync_indicator.js'))],
                          capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr
