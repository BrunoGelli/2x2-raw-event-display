import json
import multiprocessing as mp
import numpy as np
from fastapi.testclient import TestClient
from raw_display.geometry3d import Geometry3D, RECORD3D
from raw_display.trigger_windows import ObserverConfig
from raw_display.observer_runtime import attach_observers, snapshot_with_triggers
from raw_display.runtime import FIELDS
from raw_display.timing import TIMING_FIELDS
from raw_display.timed_server import create_timed_app
from test_detector_protocol import fixture, Alive, frame


def setup():
    geo,shared=fixture()
    attach_observers(mp.get_context('spawn'),shared,len(geo.pixels),geo.metadata['iogs'],ObserverConfig(trigger_view=True,view3d=True))
    np.frombuffer(shared.trigger_source_seen['beam'])[0]=10.00002
    np.frombuffer(shared.drift_ticks['beam'],dtype=np.int64)[0]=200
    geometry3d=Geometry3D(np.zeros(len(geo.pixels),dtype=RECORD3D),dict(served_sha256='fixture'))
    return geo,shared,geometry3d


def batch(ws):
    result=[]
    while True:
        item=frame(ws); result.append(item)
        if item[0]==b'RDP2':return result


def test_snapshot_pairs_are_coherent_and_optional():
    geo,shared,_=setup()
    snapshot=snapshot_with_triggers(shared,FIELDS,TIMING_FIELDS,True,True)
    assert snapshot[6]['beam'][0]==10.00002 and snapshot[7]['beam'][0]==200
    snapshot[7]['beam'][0]=0
    assert np.frombuffer(shared.drift_ticks['beam'],dtype=np.int64)[0]==200


def test_opt_in_switching_and_t0_only_delta_on_same_websocket():
    geo,shared,g=setup()
    with TestClient(create_timed_app(geo,shared,Alive(),geometry3d=g,frame_hz=30)) as client:
        assert client.get('/api/geometry').json()['view3d_enabled']
        assert len(client.get('/api/geometry3d.bin').content)==len(geo.pixels)*16
        with client.websocket_connect('/ws?trigger_windows=1&trigger_sources=1') as ws:
            first=batch(ws)
            assert b'R3D1' not in [f[0] for f in first]
            ws.send_text(json.dumps(dict(ack=first[-1][1],view3d=True)))
            for _ in range(10):
                rows=batch(ws)
                r3d=[f for f in rows if f[0]==b'R3D1' and f[2]]
                if r3d:break
                ws.send_text(json.dumps(dict(ack=rows[-1][1],view3d=True)))
            assert r3d and rows[-1][0]==b'RDP2'
            with shared.lock:
                np.frombuffer(shared.drift_ticks['beam'],dtype=np.int64)[0]=100
            ws.send_text(json.dumps(dict(ack=rows[-1][1],view3d=True)))
            for _ in range(10):
                rows=batch(ws)
                updates=[f for f in rows if f[0]==b'R3D1' and f[2]]
                if updates:break
                ws.send_text(json.dumps(dict(ack=rows[-1][1],view3d=True)))
            assert updates
            assert not any(f[2] for f in rows if f[0] in (b'RDB1',b'RDL1'))
            ws.send_text(json.dumps(dict(ack=rows[-1][1],view3d=False)))
            rows=batch(ws)
            assert b'R3D1' not in [f[0] for f in rows]
            ws.send_text(str(rows[-1][1]))
            assert client.get('/api/status').json()['clients']==1


def test_disabled_extension_and_failure_preserve_rdp2(monkeypatch):
    geo,shared=fixture()
    with TestClient(create_timed_app(geo,shared,Alive(),geometry3d_error='wrong layout')) as client:
        assert client.get('/api/geometry3d.bin').status_code==404
        with client.websocket_connect('/ws?view3d=1') as ws:
            rows=batch(ws);assert [f[0] for f in rows]==[b'RDP2'];ws.send_text(str(rows[-1][1]))
    geo,shared,g=setup()
    def broken(*args):raise ValueError('synthetic optional transport failure')
    monkeypatch.setattr('raw_display.timed_server.encode_drift_frame',broken)
    with TestClient(create_timed_app(geo,shared,Alive(),geometry3d=g)) as client:
        with client.websocket_connect('/ws?view3d=1') as ws:
            for _ in range(3):
                rows=batch(ws);assert [f[0] for f in rows]==[b'RDP2'];ws.send_text(str(rows[-1][1]))
            assert client.get('/api/status').json()['view3d']['transport_errors']>0
