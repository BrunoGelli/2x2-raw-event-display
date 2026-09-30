"""Bounded, read-only collector process. No sockets to the command server."""
from dataclasses import dataclass
import json
import logging
import multiprocessing as mp
import os
from pathlib import Path
import re
import time
import numpy as np
import zmq
from .codec import decode, DecodeError, MAX_MESSAGE

DEFAULT_IO = "/home/acd/acdaq/CRS_DAQ/daq0/crs_daq/io/pacman.json"
FIELDS = ("messages", "bytes", "words", "data_hits", "mapped_hits", "unmapped_hits",
          "bad_parity", "downstream", "other_packets", "triggers", "sync",
          "unknown_words", "malformed", "last_rx", "last_trigger")
COL = {key: n for n, key in enumerate(FIELDS)}


def read_endpoints(path=DEFAULT_IO, selected=None):
    """Read the authoritative IO config at startup, never a duplicated address list."""
    config = json.loads(Path(path).expanduser().read_text())
    if config.get("io_class") != "PACMAN_IO":
        raise ValueError("expected PACMAN_IO configuration")
    result = {}
    for entry in config["io_group"]:
        if not isinstance(entry, list) or len(entry) != 2:
            raise ValueError("io_group entries must be [integer, host]")
        iog, host = entry
        if type(iog) is not int or iog not in range(1, 9) or iog in result:
            raise ValueError("IO groups must be unique integers in 1..8")
        if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", host):
            raise ValueError("expected a hostname or IPv4 address without a port")
        result[iog] = f"tcp://{host}:5556"
    if not result or len(set(result.values())) != len(result):
        raise ValueError("empty IO config or repeated PACMAN address")
    if selected:
        missing = set(selected) - result.keys()
        if missing:
            raise ValueError(f"IO groups absent from config: {sorted(missing)}")
        result = {i: result[i] for i in sorted(set(selected))}
    return result


@dataclass
class Shared:
    seen: object
    metrics: object
    heartbeat: object
    lock: object
    stop: object


def make_shared(ctx, n_pixels):
    return Shared(ctx.RawArray("d", n_pixels), ctx.RawArray("d", 9 * len(FIELDS)),
                  ctx.RawValue("d", 0.0), ctx.Lock(), ctx.Event())


def snapshot(shared):
    with shared.lock:
        return (np.frombuffer(shared.seen).copy(),
                np.frombuffer(shared.metrics).reshape(9, len(FIELDS)).copy(),
                shared.heartbeat.value)


def collect(geometry, endpoints, shared, demo=False, demo_rate=330000.0, hwm=128):
    """One process owns all SUB sockets, the decoder, LUT and live state.

    Fixed arrays + bounded ZMQ queues. No per-browser or unbounded hit queues.
    An additional SUB still costs PACMAN CPU/network bandwidth: commission it.
    """
    log = logging.getLogger("raw-display.collector")
    try:
        if hasattr(os, "nice"):
            os.nice(5)
    except OSError:
        pass
    state = np.zeros(len(geometry.pixels), dtype=np.float64)
    stats = np.zeros((9, len(FIELDS)), dtype=np.float64)
    ctx, sockets = None, {}
    rng = np.random.default_rng(730)
    iogs = geometry.metadata["iogs"]
    previous = time.monotonic()
    published, track_at, warned = 0.0, previous, {}
    try:
        if not demo:
            ctx, poller = zmq.Context(), zmq.Poller()
            for iog, endpoint in endpoints.items():
                sock = ctx.socket(zmq.SUB)
                sock.setsockopt(zmq.RCVHWM, hwm)
                sock.setsockopt(zmq.LINGER, 0)
                sock.setsockopt(zmq.MAXMSGSIZE, MAX_MESSAGE)
                sock.setsockopt(zmq.SUBSCRIBE, b"")
                sock.connect(endpoint)
                sockets[sock] = iog
                poller.register(sock, zmq.POLLIN)
        while not shared.stop.is_set():
            now = time.monotonic()
            if demo:
                dt, previous = min(now - previous, 0.1), now
                n = int(demo_rate * dt)
                ids = rng.integers(0, len(state), n)
                state[ids] = now
                groups, counts = np.unique(geometry.pixels["iog"][ids], return_counts=True)
                for iog, count in zip(groups, counts):
                    stats[iog, COL["data_hits"]] += count
                    stats[iog, COL["mapped_hits"]] += count
                for iog in iogs:
                    stats[iog, COL["last_rx"]] = now
                if now - track_at > 1:
                    tile = geometry.metadata["tiles"][int(rng.integers(len(geometry.metadata["tiles"]))) ]
                    lo, hi = tile["start"], tile["start"] + tile["count"]
                    p = geometry.pixels[lo:hi]
                    line = np.abs(p["row"].astype(int) - p["col"].astype(int)) <= 1
                    state[np.flatnonzero(line) + lo] = now
                    track_at = now
                shared.stop.wait(0.02)
            else:
                for sock, event in poller.poll(20):
                    if not event & zmq.POLLIN:
                        continue
                    iog = sockets[sock]
                    # Fairness: a noisy source cannot monopolize the entire poller.
                    for _ in range(8):
                        try:
                            parts = sock.recv_multipart(flags=zmq.NOBLOCK)
                        except zmq.Again:
                            break
                        now = time.monotonic()
                        stats[iog, COL["messages"]] += 1
                        stats[iog, COL["bytes"]] += sum(map(len, parts))
                        stats[iog, COL["last_rx"]] = now
                        try:
                            if len(parts) != 1:
                                raise DecodeError("multipart envelope is not supported; no frame guessing")
                            hits = decode(parts[0])
                            for key, count in hits.counters.items():
                                stats[iog, COL[key]] += count
                            if hits.counters["triggers"]:
                                stats[iog, COL["last_trigger"]] = now
                            ids = geometry.lookup(iog, hits)
                            valid = ids >= 0
                            stats[iog, COL["mapped_hits"]] += np.count_nonzero(valid)
                            stats[iog, COL["unmapped_hits"]] += np.count_nonzero(~valid)
                            # Repeated IDs intentionally get one last-arrival timestamp.
                            state[ids[valid]] = now
                        except DecodeError as exc:
                            stats[iog, COL["malformed"]] += 1
                            if now - warned.get(iog, 0) > 5:
                                log.warning("IOG %d: %s", iog, exc)
                                warned[iog] = now
            now = time.monotonic()
            if now - published >= 0.1:
                with shared.lock:
                    np.copyto(np.frombuffer(shared.seen), state)
                    np.copyto(np.frombuffer(shared.metrics).reshape(stats.shape), stats)
                    shared.heartbeat.value = now
                published = now
    except Exception:
        log.exception("collector exited; the web status must show failure")
        raise
    finally:
        for sock in sockets:
            sock.close(linger=0)
        if ctx is not None:
            ctx.term()


def start_collector(geometry, endpoints, **kwargs):
    ctx = mp.get_context("spawn")
    shared = make_shared(ctx, len(geometry.pixels))
    proc = ctx.Process(target=collect, args=(geometry, endpoints, shared), kwargs=kwargs,
                       name="raw-display-collector", daemon=True)
    proc.start()
    return shared, proc


def stop_collector(shared, proc):
    shared.stop.set()
    proc.join(3)
    if proc.is_alive():
        # Only our child process; never a DAQ/PacMon process.
        proc.terminate()
        proc.join(2)
