import multiprocessing as mp
import struct
import time
import numpy as np
import pytest
import zmq
from fastapi.testclient import TestClient
from raw_display.codec import HEADER, WORD, decode, decode_batch, make_message
from raw_display.geometry import demo_geometry
from raw_display.timing import TimingConfig, TimestampUnroller, DetectorPlayback, TIMING_FIELDS, TIMING_COL
from raw_display.timed_runtime import start_timed_collector, snapshot_timed
from raw_display.runtime import stop_collector, COL, make_shared
from raw_display.timed_server import create_timed_app, encode_timed_frame, FRAME_OFFSET, RECORD
from raw_display.__main__ import parser

R = 10_000_000


def sync(ts=R, subtype=83):
    return struct.pack('<2cxxI8x', b'S', bytes([subtype]), ts)


def hit(ts, receipt=None, chip=11, channel=7, io=1):
    # Independent envelope receipt field overwrite; downstream data is ordinary.
    word = bytearray(make_message(io, chip, channel, timestamp=ts, downstream=True)[8:])
    struct.pack_into('<I', word, 2, ts+10 if receipt is None else receipt)
    return bytes(word)


def message(*words):
    return HEADER.pack(b'D', 0, len(words)) + b''.join(words)


def flow_reference(words):
    # Independent direct translation of the array algorithm, before file timestamp filling.
    raw = np.frombuffer(b''.join(words), dtype=WORD)
    increments = np.zeros(len(raw), dtype=np.int64)
    for i, w in enumerate(words):
        if w[:2] == b'SS':
            increments[i] = round(struct.unpack_from('<I', w, 4)[0]/R)*R
    offsets = np.cumsum(increments)-increments
    last = R
    output = []
    ready = False
    for i, w in enumerate(words):
        if increments[i]:
            last = increments[i]
            ready = True
        if w[:1] == b'D':
            p = struct.unpack_from('<Q', w, 8)[0]
            if p & 3:
                continue
            ts = (p >> 16) & 0x7fffffff
            receipt = struct.unpack_from('<I', w, 2)[0]
            output.append((ts % R + offsets[i] - (last if receipt < ts else 0), ready))
    return output


def test_wait_for_correct_pps_no_arrival_fallback():
    u = TimestampUnroller()
    ticks, valid = u.consume(decode(message(hit(5), sync(R,72), hit(10))))
    assert not valid.any() and u.initial_tick is None
    assert u.stats['ignored_syncs'] == 1 and u.stats['warmup_hits'] == 2


def test_sync_only_batch_advances_state():
    u = TimestampUnroller()
    u.consume(decode(message(sync())))
    ticks, valid = u.consume(decode(message(hit(100))))
    assert ticks.tolist() == [R+100] and valid.all()
    assert u.stats['pps_syncs'] == 1


def test_boundary_packet_across_batch():
    u = TimestampUnroller()
    u.consume(decode(message(sync())))
    u.consume(decode(message(sync())))
    ts, ok = u.consume(decode(message(hit(R-50, 20), hit(30, 40))))
    assert ts.tolist() == [2*R-50,2*R+30] and ok.all()
    assert u.stats['boundary_corrected'] == 1


def test_missed_pps_and_rounding_follow_flow():
    u = TimestampUnroller()
    u.consume(decode(message(sync(R+11))))
    ts, valid = u.consume(decode(message(sync(2*R-4), hit(7))))
    assert ts.tolist() == [3*R+7] and valid.all()
    assert u.stats['missed_periods'] == 1


@pytest.mark.parametrize('batch_size', [1,2,3,7,31,1000])
def test_flow_equivalence_and_chunk_invariance(batch_size):
    words = [hit(20), sync(), hit(70), hit(R-90,R-50), sync(),
             hit(R-20,10), hit(25,50), sync(R,72), hit(100),
             sync(2*R+5), hit(90), hit(80,100), sync(), hit(1)]
    expected = flow_reference(words)
    u, actual = TimestampUnroller(), []
    for i in range(0,len(words),batch_size):
        t,v = u.consume(decode(message(*words[i:i+batch_size])))
        actual.extend(zip(t.tolist(),v.tolist()))
    assert actual == expected


def test_multiple_wire_messages_keep_sync_order():
    u = TimestampUnroller()
    t,v = u.consume(decode_batch([message(sync(),hit(1)),message(sync(),hit(2))]))
    assert t.tolist() == [R+1,2*R+2] and v.all()


def test_non_pps_sync_invalid_sync_visible():
    u = TimestampUnroller()
    u.consume(decode(message(sync(0), sync(R//2))))
    assert u.initial_tick is None and u.stats['invalid_syncs'] == 2


def test_receipt_corruption_cannot_drive_clock():
    u = TimestampUnroller()
    t,v = u.consume(decode(message(sync(),hit(10,2**32-1))))
    assert not v.any() and u.frontier_tick == R
    assert u.stats['invalid_times'] == 1


def test_out_of_order_count_and_separate_iogs():
    a,b = TimestampUnroller(),TimestampUnroller()
    a.consume(decode(message(sync(),sync(),hit(99),hit(3))))
    t,v = b.consume(decode(message(sync(),hit(12))))
    assert t.tolist() == [R+12] and a.offset == 2*R
    assert a.stats['out_of_order_hits'] == 1


def playback(frontier=3.0, delay=.2):
    state = np.full(4,-1.)
    p = DetectorPlayback(state,TimingConfig(playback_delay=delay))
    p.unroller.initial_tick = R
    p.unroller.frontier_tick = int(frontier*R)
    return p,state


def test_bunched_arrival_spaced_by_asic_time():
    p,state=playback()
    p.enqueue([0,1,2],np.array([1.1,1.4,1.7])*R)
    p.step(10.)
    assert np.all(state == -1)
    p.step(10.11)
    assert state[0] == pytest.approx(1.1) and state[1] == -1
    p.step(10.41)
    assert state[1] == pytest.approx(1.4) and state[2] == -1
    p.step(10.71)
    assert state[2] == pytest.approx(1.7)


def test_genuine_simultaneous_hits_stay_simultaneous():
    p,state=playback()
    p.enqueue([0,1,2],np.array([1.2,1.2,1.2])*R)
    p.step(10.)
    p.step(10.21)
    np.testing.assert_allclose(state[:3],1.2)


def test_late_hit_keeps_age_and_cannot_overwrite_newer():
    p,state=playback()
    p.step(10.)
    p.step(10.4)
    p.enqueue([0,0,1],np.array([1.3,1.1,1.0])*R)
    assert state[0] == pytest.approx(1.3)
    assert p.cursor-state[1] == pytest.approx(.4)
    assert p.stats['late_hits']==3


def test_duplicate_future_pixels_use_latest_due_not_latest_received():
    p,state=playback()
    p.enqueue([0,0,0],np.array([1.1,1.7,1.3])*R)
    p.step(10.)
    p.step(10.2)
    assert state[0] == pytest.approx(1.1)
    p.step(10.4)
    assert state[0] == pytest.approx(1.3)
    p.step(10.8)
    assert state[0] == pytest.approx(1.7)


def test_underrun_freezes_and_rebuffers_without_jump():
    p,state=playback(frontier=1.3)
    p.step(10.)
    p.step(10.4)
    assert p.cursor == pytest.approx(1.3) and not p.running
    p.unroller.frontier_tick = int(1.45*R)
    p.step(10.5)
    assert not p.running and p.cursor == pytest.approx(1.3)
    p.unroller.frontier_tick = int(2.*R)
    p.step(12.)
    assert p.running and p.cursor == pytest.approx(1.3)


def test_clock_does_not_compress_host_stall():
    p,state=playback()
    p.step(10.)
    p.step(12.)
    assert p.cursor == 1.0 and p.stats['pacing_stalls']==1


def test_buffer_limit_drops_instead_of_blocking():
    state=np.full(4,-1.)
    p=DetectorPlayback(state,TimingConfig(max_pending_hits=2))
    p.enqueue([0,1,2],[R,R+1,R+2])
    assert not p.heap and p.stats['buffer_dropped_hits']==3


@pytest.mark.parametrize('kwargs', [{'tick_seconds':0},{'rollover_ticks':0},{'sync_type':256},
                                   {'playback_delay':float('nan')},{'max_pending_hits':0}])
def test_config_validation(kwargs):
    with pytest.raises(ValueError): TimingConfig(**kwargs)


def test_cli_default_is_asic_and_binding_rejected_early():
    a=parser().parse_args(['serve'])
    assert a.time_basis=='asic' and a.hwm==4096 and a.batch_messages==256 and a.host=='127.0.0.1'
    with pytest.raises(SystemExit): parser().parse_args(['serve','--host','0.0.0.0'])


def test_wire_protocol_carries_detector_times_not_host_age():
    previous=np.full(3,-1.)
    current=np.array([1.1,-1.,1.5])
    clocks=np.zeros((9,len(TIMING_FIELDS)))
    clocks[1,TIMING_COL['cursor_s']]=1.6
    clocks[1,TIMING_COL['frontier_s']]=2.9
    clocks[1,TIMING_COL['running']]=1
    raw=encode_timed_frame(previous,current,clocks,42)
    assert struct.unpack_from('<4sII',raw)==(b'RDP2',42,2)
    assert struct.unpack_from('<ddd',raw,12)==(1.6,2.9,1.)
    records=np.frombuffer(raw,dtype=RECORD,offset=FRAME_OFFSET)
    assert records['id'].tolist()==[0,2]
    np.testing.assert_allclose(records['time'],[1.1,1.5])


class Alive:
    def is_alive(self): return True


def test_http_ws_status_and_initial_sync_wait():
    geo=demo_geometry(iogs=[1],chips_per_tile=1)
    ctx=mp.get_context('spawn')
    shared=make_shared(ctx,len(geo.pixels))
    shared.timing=ctx.RawArray('d',9*len(TIMING_FIELDS))
    shared.cpu_ratio=ctx.RawValue('d',.2)
    shared.rate_ring=None
    np.frombuffer(shared.seen).fill(-1.)
    shared.heartbeat.value=time.monotonic()
    with TestClient(create_timed_app(geo,shared,Alive())) as c:
        assert c.get('/api/geometry').json()['time_basis']=='asic'
        assert c.get('/api/geometry').json()['protocol']==2
        with c.websocket_connect('/ws') as ws:
            raw=ws.receive_bytes()
            magic,seq,n=struct.unpack_from('<4sII',raw)
            assert magic==b'RDP2' and n==0
            ws.send_text(str(seq))
        assert c.get('/api/status').json()['sources'][0]['timing']['synchronized']==0


def test_local_zmq_timed_collector():
    geo=demo_geometry(iogs=[1],chips_per_tile=1)
    ctx=zmq.Context(); pub=ctx.socket(zmq.XPUB);pub.setsockopt(zmq.RCVTIMEO,6000)
    port=pub.bind_to_random_port('tcp://127.0.0.1')
    shared,proc=start_timed_collector(geo,{1:f'tcp://127.0.0.1:{port}'},TimingConfig(playback_delay=.1))
    try:
        assert pub.recv()==b'\x01' # subscription handshake: no production endpoints
        pub.send(message(sync(),hit(1_000_000,1_000_010),sync(),hit(1_000_000,1_000_010)))
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            state,stats,beat,timing,cpu=snapshot_timed(shared)
            if np.any(state>=0): break
            time.sleep(.05)
        assert proc.is_alive()
        assert stats[1,COL['mapped_hits']]==2
        assert timing[1,TIMING_COL['pps_syncs']]==2
        np.testing.assert_allclose(state[state>=0],[1.1])
    finally:
        stop_collector(shared,proc);pub.close(0);ctx.term()


def test_packet_file_adapter_matches_streaming():
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('validate_packet_timing',Path(__file__).parents[1]/'tools/validate_packet_timing.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    dtype=[('packet_type','u1'),('io_group','u1'),('timestamp','u8'),
           ('receipt_timestamp','u8'),('trigger_type','u1'),('valid_parity','u1')]
    p=np.zeros(8,dtype=dtype)
    p['io_group']=1;p['valid_parity']=1;p['packet_type']=[6,0,0,6,0,0,6,0]
    p['timestamp']=[R,10,R-5,R,R-2,30,2*R,100]
    p['receipt_timestamp']=[0,20,R-1,0,10,40,0,120]
    p['trigger_type'][[0,3,6]]=83
    for chunk in [1,3,8]:
        result=m.validate(p,TimingConfig(),chunk)
        assert result[1]['compared_hits']==5 and result[1]['mismatches']==0
