"""Existing process/API/protocol integration using localhost synthetic publishers."""
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
from raw_display.common_timing import FEATURE_BUILD
from raw_display.geometry import demo_geometry
from raw_display.runtime import make_shared, FIELDS, COL, stop_collector
from raw_display.timing import TimingConfig, TIMING_FIELDS
from raw_display.trigger_windows import ObserverConfig
from raw_display.observer_runtime import (Observers, attach_observers, read_observers,
    snapshot_with_triggers, encode_trigger_frame)
from raw_display.timed_runtime import start_timed_collector
from raw_display.timed_server import create_timed_app
from test_detector_triggers import UNIX, R, message, aux, charge


class Alive:
    def is_alive(self):
        return True


def fixture():
    geo = demo_geometry(iogs=[1, 5, 6], chips_per_tile=1)
    ctx = mp.get_context('spawn')
    shared = make_shared(ctx, len(geo.pixels))
    cfg = ObserverConfig(trigger_view=True)
    attach_observers(ctx, shared, len(geo.pixels), [1, 5, 6], cfg)
    shared.timing = ctx.RawArray('d', 9*len(TIMING_FIELDS))
    shared.cpu_ratio = ctx.RawValue('d', 0.)
    shared.rate_ring = None
    shared.heartbeat.value = time.monotonic()
    np.frombuffer(shared.seen).fill(-1.)
    np.frombuffer(shared.trigger_seen)[0] = 2.00002
    np.frombuffer(shared.trigger_source_seen['beam'])[0] = 2.00001
    np.frombuffer(shared.trigger_source_seen['light'])[0] = 2.00002
    return geo, shared


def frame(ws):
    data = ws.receive_bytes()
    magic, seq, n = struct.unpack_from('<4sII', data)
    return magic, seq, n, data


def test_coherent_snapshot_preserves_legacy_tuple_and_adds_optional_source_arrays():
    geo, shared = fixture()
    assert len(snapshot_with_triggers(shared, FIELDS, TIMING_FIELDS)) == 6
    result = snapshot_with_triggers(shared, FIELDS, TIMING_FIELDS, True)
    assert len(result) == 7
    assert result[-1]['beam'][0] == 2.00001
    result[-1]['beam'][0] = 99
    assert np.frombuffer(shared.trigger_source_seen['beam'])[0] == 2.00001


def test_new_source_frames_opt_in_and_legacy_clients_still_work():
    geo, shared = fixture()
    with TestClient(create_timed_app(geo, shared, Alive())) as client:
        meta = client.get('/api/geometry').json()
        assert meta['feature_build'] == FEATURE_BUILD
        assert meta['trigger_sources'] == {'beam': 5, 'light': 6}
        assert meta['trigger_source_protocol'] == 1
        assert 'trigger_alignment' in client.get('/api/status').json()
        for query, expected in [('/ws', [b'RDP2']),
                ('/ws?trigger_windows=1', [b'RDT1', b'RDP2']),
                ('/ws?trigger_windows=1&trigger_sources=1', [b'RDB1', b'RDL1', b'RDP2'])]:
            with client.websocket_connect(query) as ws:
                # First pump publication can follow the first empty frame.
                saw_data = False
                for _ in range(10):
                    received = []
                    while True:
                        magic, seq, n, data = frame(ws)
                        received.append(magic)
                        if n and magic in (b'RDT1', b'RDB1', b'RDL1'):
                            saw_data = True
                        if magic == b'RDP2':
                            ws.send_text(str(seq))
                            break
                    if received == expected and (query == '/ws' or saw_data):
                        break
                assert received == expected
                if query != '/ws':
                    assert saw_data


def test_wrong_ack_closes_session():
    geo, shared = fixture()
    with TestClient(create_timed_app(geo, shared, Alive())) as client:
        with client.websocket_connect('/ws') as ws:
            frame(ws)
            ws.send_text('wrong')
            assert ws.receive()['code'] == 1008


def test_cross_origin_websocket_is_rejected():
    geo, shared = fixture()
    from starlette.websockets import WebSocketDisconnect
    with TestClient(create_timed_app(geo, shared, Alive())) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/ws', headers={'origin': 'https://untrusted.invalid'}):
                pass


def test_source_frames_encode_clear_to_unseen_without_changing_time_units():
    previous, current = np.array([1.]), np.array([-1.])
    for magic in (b'RDT1', b'RDB1', b'RDL1'):
        data = encode_trigger_frame(previous, current, 7, magic)
        assert struct.unpack_from('<4sII', data) == (magic, 7, 1)
        assert struct.unpack_from('<Id', data, 12) == (0, -1.)
    with pytest.raises(ValueError):
        encode_trigger_frame(previous, current, 7, b'FAIL')


def test_observer_diagnostic_writer_remains_nonblocking():
    geo, shared = fixture()
    obs = Observers(len(geo.pixels), [1, 5, 6], TimingConfig(), shared.observer_config, shared)
    with shared.observer_lock:
        obs.publish_diagnostics(100.)
    assert obs.skipped_diagnostics == 1
    obs.publish_diagnostics(101.1)
    data = read_observers(shared)
    assert data['feature_build'] == FEATURE_BUILD
    assert data['skipped_diagnostic_publications'] == 1
    assert data['common_timing']['hardware_phase_verified'] is False


def test_eight_real_local_subscriptions_and_both_source_layers(monkeypatch):
    monkeypatch.setenv('RAW_DISPLAY_TRIGGER_VIEW', '1')
    monkeypatch.delenv('RAW_DISPLAY_LATE_AUDIT_IOGS', raising=False)
    monkeypatch.delenv('RAW_DISPLAY_RATE_AUDIT', raising=False)
    geo = demo_geometry(iogs=range(1, 9), chips_per_tile=1)
    ctx = zmq.Context()
    pubs, endpoints = {}, {}
    for i in range(1, 9):
        pub = ctx.socket(zmq.XPUB)
        pub.setsockopt(zmq.RCVTIMEO, 8000)
        port = pub.bind_to_random_port('tcp://127.0.0.1')
        pubs[i], endpoints[i] = pub, f'tcp://127.0.0.1:{port}'
    shared, proc = start_timed_collector(geo, endpoints, TimingConfig(playback_delay=.05))
    try:
        for i, pub in pubs.items():
            assert pub.recv() == b'\x01'  # one subscriber, no extra probe
            for second in range(UNIX-i % 3, UNIX+2):
                pub.send(message(second, aux()))
        deadline = time.monotonic()+3
        while time.monotonic() < deadline:
            info = read_observers(shared)
            states = info.get('common_timing', {}).get('sources', [])
            if len(states) == 8 and all(s['status'] == 'aligned' for s in states):
                break
            time.sleep(.02)
        assert len(states) == 8 and all(s['status'] == 'aligned' for s in states)
        for pub in pubs.values():
            pub.send(message(UNIX+1, charge(1_000_100), charge(1_100_100, channel=1)))
        pubs[5].send(message(UNIX+1, aux('T', 2, 1_000_000)))
        pubs[6].send(message(UNIX+1, aux('T', 2, 1_100_000)))
        current_second, keepalive = UNIX+2, time.monotonic()+.25
        deadline = time.monotonic()+7
        while time.monotonic() < deadline:
            data = snapshot_with_triggers(shared, FIELDS, TIMING_FIELDS, True)
            sources = data[-1]
            if all(np.count_nonzero(sources[name] >= 0) == 8 for name in ('beam', 'light')):
                break
            if time.monotonic() >= keepalive:
                for pub in pubs.values():
                    pub.send(message(current_second, aux()))
                current_second += 1
                keepalive = time.monotonic()+.5
            time.sleep(.02)
        assert proc.is_alive()
        for i in range(1, 9):
            for name, channel, phase in [('beam', 0, 1_000_100), ('light', 1, 1_100_100)]:
                pixel = geo.lut[i, 1, 11, channel]
                expected = (i % 3+2)+phase/R
                assert sources[name][pixel] == pytest.approx(expected)
            assert data[1][i, COL['mapped_hits']] == 2
            assert pubs[i].poll(0) == 0  # no additional subscription from readers
        with TestClient(create_timed_app(geo, shared, proc)) as client:
            assert client.get('/healthz').status_code == 200
            with client.websocket_connect('/ws?sync83=1&trigger_windows=1&trigger_sources=1') as ws:
                for _ in range(10):
                    meta = ws.receive_json()
                    beam, light, normal = frame(ws), frame(ws), frame(ws)
                    ws.send_text(str(normal[1]))
                    if beam[2] == light[2] == 8:
                        break
                assert [beam[0], light[0], normal[0]] == [b'RDB1', b'RDL1', b'RDP2']
                assert beam[1] == light[1] == normal[1]
                assert beam[2] == light[2] == 8
    finally:
        stop_collector(shared, proc)
        for pub in pubs.values():
            pub.close(0)
        ctx.term()


def test_new_and_legacy_javascript_regressions():
    import shutil
    if not shutil.which('node'):
        pytest.skip('Node is a development test dependency, not a DAQ runtime dependency')
    for name in ['test_trigger_view.js', 'test_detector_sources.js']:
        subprocess.run(['node', str(Path(__file__).with_name(name))], check=True, capture_output=True, text=True)
