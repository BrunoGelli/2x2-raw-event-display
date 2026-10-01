import importlib.util
import multiprocessing as mp
from pathlib import Path
import time

import numpy as np
import pytest
import zmq
from fastapi.testclient import TestClient
from raw_display.geometry import demo_geometry
from raw_display.codec import decode_batch, make_message
from raw_display.rate_audit import RateAudit, make_rate_ring, read_rate_ring, SCALARS, SOURCE_FIELDS
from raw_display.runtime import FIELDS, COL, start_collector, stop_collector, make_shared
from raw_display.timed_server import create_timed_app as create_app
from raw_display.timed_runtime import start_timed_collector as start_collector
from raw_display.timing import TIMING_FIELDS


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv('RAW_DISPLAY_RATE_AUDIT', '1')
    geo = demo_geometry(iogs=[1, 2], chips_per_tile=1)
    ring = make_rate_ring(mp.get_context('spawn'))
    audit = RateAudit(geo, ring, COL)
    audit.begin = audit.loop_at = 0.0
    stats = np.zeros((9, len(FIELDS)))
    return geo, ring, audit, stats


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv('RAW_DISPLAY_RATE_AUDIT', raising=False)
    assert make_rate_ring(mp.get_context('spawn')) is None
    assert read_rate_ring(None)['enabled'] is False


def test_counts_unique_actual_interval_and_zero_bin(setup):
    geo, ring, a, stats = setup
    hits = decode_batch([make_message([1, 2, 3, 4], [11]*4, [7]*4, downstream=True)])
    ids = geo.lookup(1, hits)
    a.record(1, ids, 1, 256, .001, .002)
    for key, value in hits.counters.items():
        stats[1, COL[key]] += value
    stats[1, COL['mapped_hits']] = 4
    stats[1, COL['messages']] = 1
    a.publish(.11, stats)
    a.publish(.25, stats)
    d = read_rate_ring(ring)
    x, y = map(np.asarray, d['samples'])
    n = len(SCALARS)
    assert x[2]-x[1] == .11
    assert x[n] == 4  # repeated pixel hits count four times, not once
    assert x[n+64] == 1
    assert y[n:n+128].sum() == 0  # explicit zero interval, no stale counts
    assert y[2]-y[1] == pytest.approx(.14)
    s = x[n+128:].reshape(8, len(SOURCE_FIELDS))
    assert s[0, SOURCE_FIELDS.index('messages')] == 1
    assert s[0, SOURCE_FIELDS.index('batches')] == 1
    assert s[0, SOURCE_FIELDS.index('full_drains')] == 0


def test_missing_mapping_is_not_mapped_activity(setup):
    geo, ring, a, stats = setup
    a.record(1, np.array([-1, -1], dtype=int), 1, 1, 0, 0)
    a.publish(.1, stats)
    row = read_rate_ring(ring)['samples'][0]
    n = len(SCALARS)
    assert sum(row[n:n+128]) == 0


def test_reader_cannot_block_collector_and_gap_visible(setup):
    geo, ring, a, stats = setup
    ring.lock.acquire()
    t = time.monotonic()
    try:
        a.publish(.1, stats)
    finally:
        ring.lock.release()
    assert time.monotonic()-t < .1
    a.publish(.2, stats)
    row = read_rate_ring(ring)['samples'][0]
    assert row[0] == 2 and row[5] == 1


def test_ring_bounded_and_cursor(setup):
    _, ring, a, stats = setup
    for j in range(1, 706):
        a.publish(j*.1, stats)
    d = read_rate_ring(ring, after=702)
    assert d['oldest'] == 106
    assert [x[0] for x in d['samples']] == [703, 704, 705]
    with pytest.raises(ValueError):
        read_rate_ring(ring, -1)


def test_loop_gap_recorded(setup):
    _, ring, a, stats = setup
    a.loop(.01); a.loop(.91)
    a.publish(.92, stats)
    assert read_rate_ring(ring)['samples'][0][4] == pytest.approx(.90)


class Alive:
    def is_alive(self): return True


def test_http_uses_existing_ring_not_a_subscriber(setup):
    geo, ring, a, stats = setup
    a.publish(.1, stats)
    shared = make_shared(mp.get_context('spawn'), len(geo.pixels))
    shared.rate_ring = ring
    shared.timing = mp.get_context('spawn').RawArray('d', 9*len(TIMING_FIELDS))
    shared.cpu_ratio = mp.get_context('spawn').RawValue('d', 0.)
    with TestClient(create_app(geo, shared, Alive())) as c:
        r = c.get('/api/tile-rates')
        assert r.status_code == 200 and r.json()['enabled']
        assert r.json()['samples'][0][0] == 1
        assert c.get('/api/tile-rates?limit=101').status_code == 400


def test_local_zmq_collector_publishes_exact_rate_rows(setup):
    geo, _, _, _ = setup
    ctx = zmq.Context(); pub = ctx.socket(zmq.PUB)
    port = pub.bind_to_random_port('tcp://127.0.0.1')
    shared, proc = start_collector(geo, {1:f'tcp://127.0.0.1:{port}'}, batch_messages=32)
    try:
        deadline = time.monotonic()+6
        while time.monotonic() < deadline:
            pub.send(make_message([1, 2], [11]*2, [7]*2, downstream=True))
            time.sleep(.05)
            d = read_rate_ring(shared.rate_ring)
            if any(row[len(SCALARS)] > 0 for row in d['samples']):
                break
        assert proc.is_alive()
        hit_rows = [r for r in d['samples'] if r[len(SCALARS)] > 0]
        assert hit_rows
        for row in hit_rows:
            assert row[len(SCALARS)+64] == 1
            assert sum(row[len(SCALARS)+1:len(SCALARS)+64]) == 0
    finally:
        stop_collector(shared, proc); pub.close(0); ctx.term()


def test_capture_url_is_loopback_only():
    spec = importlib.util.spec_from_file_location('tile_audit_tool',
        Path(__file__).parents[1]/'tools/tile_rate_audit.py')
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    assert mod.url_argument('http://127.0.0.1:8765') == 'http://127.0.0.1:8765'
    for bad in ['http://example.com:8765', 'http://127.0.0.1.evil.test:8765',
                'http://user@127.0.0.1:8765', 'http://127.0.0.1:8765/other']:
        with pytest.raises(Exception):
            mod.url_argument(bad)
