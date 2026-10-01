"""ASIC-time collector. Production SUB topology/settings are unchanged.

Transport remains the v0.3.0 single-frame, bounded-drain loop. No new sockets,
commands, disk writes or waiting for downstream presentation are introduced.
"""
import logging
import multiprocessing as mp
import os
import signal
import time
import numpy as np
import zmq
from .codec import decode_batch, MAX_MESSAGE
from .runtime import make_shared, COL, FIELDS, stop_collector
from .timing import TimingConfig, DetectorPlayback, TIMING_FIELDS
from .rate_audit import make_rate_ring, RateAudit
from .post_sync import display_mask
from .trigger_windows import ObserverConfig
from .observer_runtime import attach_observers, Observers


def snapshot_timed(shared):
    with shared.lock:
        return (np.frombuffer(shared.seen).copy(),
                np.frombuffer(shared.metrics).reshape(9, len(FIELDS)).copy(),
                shared.heartbeat.value,
                np.frombuffer(shared.timing).reshape(9, len(TIMING_FIELDS)).copy(),
                shared.cpu_ratio.value)


def collect_timed(geometry, endpoints, shared, config, hwm=4096, batch_messages=256):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    log = logging.getLogger('raw-display.collector')
    try:
        os.nice(5)
    except (AttributeError, OSError):
        pass
    state = np.full(len(geometry.pixels), -1.0)
    stats = np.zeros((9, len(FIELDS)), dtype=np.float64)
    timing = np.zeros((9, len(TIMING_FIELDS)), dtype=np.float64)
    clocks = {i: DetectorPlayback(state, config) for i in endpoints}
    observers = Observers(len(state), endpoints, config, shared.observer_config, shared)
    audit = RateAudit(geometry, shared.rate_ring, COL) if shared.rate_ring is not None else None
    ctx, poller = zmq.Context(), zmq.Poller()
    sockets, warned = {}, {}
    published, cpu_at = time.monotonic(), time.process_time()
    try:
        for iog, endpoint in endpoints.items():
            sock = ctx.socket(zmq.SUB)
            sock.setsockopt(zmq.RCVHWM, hwm)
            sock.setsockopt(zmq.LINGER, 0)
            sock.setsockopt(zmq.MAXMSGSIZE, MAX_MESSAGE)
            sock.setsockopt(zmq.SUBSCRIBE, b'')
            sock.connect(endpoint)
            sockets[sock] = iog
            poller.register(sock, zmq.POLLIN)
        while not shared.stop.is_set():
            if audit is not None:
                audit.loop(time.monotonic())
            for sock, event in poller.poll(20):
                if not event & zmq.POLLIN:
                    continue
                iog = sockets[sock]
                frames, nbytes = [], 0
                drain_at = time.monotonic()
                for _ in range(batch_messages):
                    try:
                        frame = sock.recv(flags=zmq.NOBLOCK)
                    except zmq.Again:
                        break
                    frames.append(frame)
                    nbytes += len(frame)
                if not frames:
                    continue
                now = time.monotonic()
                stats[iog, COL['messages']] += len(frames)
                stats[iog, COL['bytes']] += nbytes
                stats[iog, COL['last_rx']] = now
                hits = decode_batch(frames)
                for key, value in hits.counters.items():
                    stats[iog, COL[key]] += value
                if hits.diagnostic and now - warned.get(iog, 0) > 5:
                    log.warning('IOG %d: %s', iog, hits.diagnostic)
                    warned[iog] = now
                if hits.counters['triggers']:
                    stats[iog, COL['last_trigger']] = now
                if hits.counters['sync83_packets']:
                    stats[iog, COL['last_sync83']] = now
                ids = geometry.lookup(iog, hits)
                mapped = int(np.count_nonzero(ids >= 0))
                stats[iog, COL['mapped_hits']] += mapped
                stats[iog, COL['unmapped_hits']] += len(ids) - mapped
                selected = display_mask(ids, hits.timestamp, config.min_raw_timestamp)
                selected_count = int(np.count_nonzero(selected))
                stats[iog, COL['post_sync_filtered_hits']] += mapped - selected_count
                stats[iog, COL['display_selected_hits']] += selected_count
                clock = clocks[iog]
                initial = (clock.unroller.offset, clock.unroller.initial_tick is not None)
                result = clock.ingest(ids, hits)
                observers.record(iog, ids, hits, result, clock, initial)
                if audit is not None:
                    audit.record(iog, ids, len(frames), batch_messages,
                                 now - drain_at, time.monotonic() - now)
            now = time.monotonic()
            if now - published >= 0.1:
                for iog, clock in clocks.items():
                    clock.step(now)
                    observers.step(iog, clock.cursor)
                    summary = clock.summary()
                    timing[iog] = [summary[key] for key in TIMING_FIELDS]
                cpu = time.process_time()
                with shared.lock:
                    np.copyto(np.frombuffer(shared.seen), state)
                    np.copyto(np.frombuffer(shared.metrics).reshape(stats.shape), stats)
                    np.copyto(np.frombuffer(shared.timing).reshape(timing.shape), timing)
                    observers.publish_state()
                    shared.heartbeat.value = now
                    shared.cpu_ratio.value = (cpu - cpu_at) / (now - published)
                if audit is not None:
                    audit.publish(now, stats)
                observers.publish_diagnostics(now)
                published, cpu_at = now, cpu
    except Exception:
        log.exception('ASIC-time collector failed')
        raise
    finally:
        for sock in sockets:
            sock.close(linger=0)
        ctx.term()


def start_timed_collector(geometry, endpoints, config=None, hwm=4096, batch_messages=256):
    config = config or TimingConfig()
    ctx = mp.get_context('spawn')
    shared = make_shared(ctx, len(geometry.pixels))
    attach_observers(ctx, shared, len(geometry.pixels), endpoints, ObserverConfig.from_env())
    # Compatible with a working copy that still has the optional rate-audit patch.
    if getattr(shared, 'rate_ring', None) is None:
        shared.rate_ring = make_rate_ring(ctx)
    shared.timing = ctx.RawArray('d', 9 * len(TIMING_FIELDS))
    shared.cpu_ratio = ctx.RawValue('d', 0.0)
    np.frombuffer(shared.seen).fill(-1.0)
    proc = ctx.Process(target=collect_timed, args=(geometry, endpoints, shared, config),
                       kwargs=dict(hwm=hwm, batch_messages=batch_messages),
                       name='raw-display-collector', daemon=True)
    proc.start()
    return shared, proc
