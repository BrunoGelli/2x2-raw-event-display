import json
import multiprocessing as mp
import struct
import time
import numpy as np
import pytest
import zmq
from fastapi.testclient import TestClient
from raw_display.codec import HEADER, WORD, DecodeError, decode, decode_batch, make_message
from raw_display.geometry import demo_geometry
from raw_display.runtime import (read_endpoints, read_asic_versions, make_shared, snapshot,
                                 start_collector, stop_collector, COL)
from raw_display.server import create_app, encode_frame, FRAME_HEADER, UPDATE
from raw_display.__main__ import loopback_host


@pytest.fixture(scope="module")
def geo():
    return demo_geometry(chips_per_tile=1)


def test_independent_wire_example():
    chip, channel, timestamp, adc = 131, 37, 0x1234567, 0xA4
    p = bytearray([(chip << 2) & 255, (chip >> 6) | (channel << 2),
                   timestamp & 255, (timestamp >> 8) & 255, (timestamp >> 16) & 255,
                   ((timestamp >> 24) & 127) | 128, adc, 0])
    p[7] |= ((1 - sum(bin(b).count("1") for b in p) % 2) << 7)
    raw = struct.pack("<cIxH", b"D", 0x76543210, 1) + struct.pack("<cBI2x8s", b"D", 18, 87654321, p)
    hits = decode(raw)
    assert hits.io_channel.tolist() == [18]
    assert hits.chip.tolist() == [131]
    assert hits.channel.tolist() == [37]
    assert hits.timestamp.tolist() == [timestamp]
    assert hits.adc.tolist() == [adc]
    assert hits.receipt_timestamp.tolist() == [87654321]


@pytest.mark.parametrize("size", [0, 1, 2, 17, 1024, 65535])
def test_vectorized_fields(size):
    rng = np.random.default_rng(4)
    io, chip = rng.integers(1, 33, size), rng.integers(0, 256, size)
    ch, adc = rng.integers(0, 64, size), rng.integers(0, 256, size)
    timestamp = rng.integers(0, 2**31, size)
    hits = decode(make_message(io, chip, ch, adc, timestamp))
    for actual, expected in [(hits.io_channel, io), (hits.chip, chip), (hits.channel, ch),
                             (hits.adc, adc), (hits.timestamp, timestamp)]:
        np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize("raw", [b"", b"D123", HEADER.pack(b"D", 0, 1),
    HEADER.pack(b"D", 0, 0)+b"0"*16, HEADER.pack(b"D", 0, 1)+b"0"*24])
def test_reject_malformed_strict(raw):
    with pytest.raises(DecodeError):
        decode(raw)


def test_batch_skips_malformed_but_keeps_good_data():
    good1 = make_message([1,2], [11,12], [3,4])
    good2 = make_message([3], [13], [5])
    hit = decode_batch([good1, b"bad frame", good2])
    assert hit.channel.tolist() == [3,4,5]
    assert hit.counters["malformed"] == 1
    assert hit.counters["legacy_messages"] == 2
    assert hit.diagnostic is not None


def test_batch_matches_individual_decoding():
    rng = np.random.default_rng(7)
    frames = []
    expected_channels = []
    for _ in range(128):
        ch = rng.integers(0, 64, 8)
        frames.append(make_message(rng.integers(1, 33, 8), rng.integers(11, 21, 8), ch))
        expected_channels.extend(ch.tolist())
    hit = decode_batch(frames, strict=True)
    assert hit.channel.tolist() == expected_channels
    assert hit.counters["legacy_messages"] == 128
    assert hit.counters["data_hits"] == 1024


def test_parity_downstream_and_nondata():
    raw = bytearray(make_message([1,1,1,1], [11]*4, [0,1,2,3]))
    raw[8+15] ^= 128
    raw[8+16+15] ^= 64 | 128
    raw[8+32+8] ^= 2
    hit = decode(raw)
    assert hit.channel.tolist() == [1, 3]
    assert hit.counters["bad_parity"] == 1
    assert hit.counters["downstream"] == 1
    assert hit.counters["upstream"] == 2
    assert hit.counters["other_packets"] == 1


def test_nondata_message_is_not_malformed():
    hit = decode(HEADER.pack(b"!", 0, 0))
    assert len(hit.channel) == 0
    assert hit.counters["nondata_messages"] == 1
    assert hit.counters["legacy_messages"] == 1


def test_special_words():
    words = np.zeros(3, dtype=WORD)
    words["kind"] = [ord("T"), ord("S"), ord("?")]
    hits = decode(HEADER.pack(b"D", 0, 3)+words.tobytes())
    assert len(hits.chip) == 0
    assert hits.counters["triggers"] == hits.counters["sync"] == hits.counters["unknown_words"] == 1


def test_hydra_route_alias_and_even_iog(geo):
    hits = decode(make_message([17,18,19,20], [11]*4, [12]*4))
    ids = geo.lookup(6, hits)
    assert len(set(ids)) == 1 and ids[0] >= 0
    tile = next(t for t in geo.metadata["tiles"] if t["iog"] == 6 and t["tile"] == 5)
    assert tile["geometry_tile"] == 13
    assert tile["start"] <= ids[0] < tile["start"]+tile["count"]
    assert geo.lookup(5, hits)[0] != ids[0]


def test_invalid_and_unmapped_addresses(geo):
    hits = decode(make_message([1,1,1], [11,255,11], [0,0,1]))
    hits.io_channel[0] = 0
    hits.io_channel[2] = 33
    assert geo.lookup(1, hits).tolist() == [-1,-1,-1]


def test_pixel_binary_and_orientation(geo):
    assert geo.pixels.dtype.itemsize == 8
    assert len(geo.pixels.tobytes()) == geo.metadata["n_pixels"]*8
    for t in geo.metadata["tiles"]:
        rec = geo.pixels[t["start"]:t["start"]+t["count"]]
        assert np.all(rec["iog"] == t["iog"])
        assert np.all(rec["tile"] == t["tile"])
        assert rec["col"].max() < t["width"]
        assert rec["row"].max() < t["height"]


def test_config_is_authoritative(tmp_path):
    p = tmp_path/"pacman.json"
    p.write_text(json.dumps({"io_class":"PACMAN_IO", "io_group":[[1,"127.0.0.2"],[6,"pacman-test"]]}))
    assert read_endpoints(p, [6]) == {6:"tcp://pacman-test:5556"}
    p.write_text(json.dumps({"io_class":"PACMAN_IO", "io_group":[[6,"new-host"]]}))
    assert read_endpoints(p) == {6:"tcp://new-host:5556"}
    with pytest.raises(ValueError):
        read_endpoints(p, [1])
    p.write_text(json.dumps({"io_class":"PACMAN_IO", "io_group":[[6,"host:5555"]]}))
    with pytest.raises(ValueError):
        read_endpoints(p)


@pytest.mark.parametrize("label", [2, "2", "2a", "2b", "2d", "v2d"])
def test_run_config_accepts_packet_v2_families(tmp_path, label):
    p = tmp_path/"RUN_CONFIG.json"
    p.write_text(json.dumps({"io_group_asic_version_":{"1":label}}))
    assert read_asic_versions(p, [1]) == {1:2}


@pytest.mark.parametrize("label", [3, "3a", "v3a", "unknown"])
def test_run_config_rejects_non_v2_family(tmp_path, label):
    p = tmp_path/"RUN_CONFIG.json"
    p.write_text(json.dumps({"io_group_asic_version_":{"1":label}}))
    with pytest.raises(ValueError):
        read_asic_versions(p, [1])


def test_web_bind_is_loopback_only():
    assert loopback_host("127.0.0.1") == "127.0.0.1"
    for host in ("0.0.0.0", "localhost", "192.168.2.10", "::1"):
        with pytest.raises(Exception):
            loopback_host(host)


def test_slow_client_diff_catches_skipped_snapshots():
    a = np.array([0.,0.,0.]); b = np.array([10.,0.,0.]); c=np.array([10.,20.,0.])
    first = encode_frame(a,b,20.,1)
    latest = encode_frame(a,c,30.,3)
    assert FRAME_HEADER.unpack_from(first)[:3] == (b"RDP1",1,1)
    records=np.frombuffer(latest,dtype=UPDATE,offset=FRAME_HEADER.size)
    assert records["id"].tolist() == [0,1]
    assert records["age"].tolist() == [20.,10.]


class Running:
    def is_alive(self): return True


def test_http_and_websocket(geo):
    shared=make_shared(mp.get_context("spawn"),len(geo.pixels))
    with shared.lock:
        np.frombuffer(shared.seen)[5]=time.monotonic()
        shared.heartbeat.value=time.monotonic()
    app=create_app(geo,shared,Running(),demo=True)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/static/display.js").status_code == 200
        assert client.get("/api/geometry").json()["mode"] == "DEMO"
        assert client.get("/api/geometry.bin").content == geo.pixels.tobytes()
        with client.websocket_connect("/ws") as ws:
            for _ in range(10):
                frame=ws.receive_bytes()
                magic,seq,n,now=FRAME_HEADER.unpack_from(frame)
                ws.send_text(str(seq))
                if n: break
            records=np.frombuffer(frame,dtype=UPDATE,offset=20)
            assert records["id"].tolist() == [5]
        assert client.get("/healthz").status_code == 200


def test_real_local_zmq_ingest_and_malformed(geo):
    ctx=zmq.Context();pub=ctx.socket(zmq.PUB)
    port=pub.bind_to_random_port("tcp://127.0.0.1")
    shared,proc=start_collector(geo,{1:f"tcp://127.0.0.1:{port}"}, batch_messages=32)
    try:
        raw=make_message([1,2,3,4],[11]*4,[7]*4)
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            pub.send(raw);pub.send(b"bad frame");time.sleep(.05)
            seen,stats,beat=snapshot(shared)
            if stats[1,COL["mapped_hits"]]>=4 and stats[1,COL["malformed"]]>0: break
        assert proc.is_alive()
        assert stats[1,COL["mapped_hits"]]>=4
        assert stats[1,COL["malformed"]]>0
        assert np.count_nonzero(seen)==1
    finally:
        stop_collector(shared,proc);pub.close(0);ctx.term()
    assert not proc.is_alive()
