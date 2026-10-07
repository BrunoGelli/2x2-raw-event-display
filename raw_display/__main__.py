"""CLI. ASIC-time live playback by default; explicit host-time diagnostic mode."""
import argparse
import json
import logging
import platform
import sys
import time
import numpy as np
from . import __version__
from .codec import make_message, decode_batch
from .geometry import demo_geometry, load_geometry, download_geometry
from .runtime import (DEFAULT_IO, DEFAULT_RUN_CONFIG, read_endpoints, read_asic_versions,
                      start_collector, stop_collector, snapshot, COL)


def positive(value):
    v = float(value)
    if not np.isfinite(v) or v <= 0:
        raise argparse.ArgumentTypeError('must be a positive finite number')
    return v


def positive_int(value):
    v = int(value)
    if v <= 0:
        raise argparse.ArgumentTypeError('must be a positive integer')
    return v


def raw_tick_cut(value):
    from .post_sync import validate_min_raw_timestamp
    try:
        return validate_min_raw_timestamp(int(value))
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError(str(exc))


def loopback_host(value):
    if value != '127.0.0.1':
        raise argparse.ArgumentTypeError('raw-display may bind only to 127.0.0.1')
    return value


def benchmark(geometry, words=1024, messages=2000, batch_messages=256):
    rng = np.random.default_rng(42)
    iog = geometry.metadata['iogs'][0]
    pool = np.flatnonzero(geometry.pixels['iog'] == iog)
    frames = []
    for _ in range(min(messages, 512)):
        px = geometry.pixels[rng.choice(pool, size=words)]
        io = (px['tile'].astype(int)-1)*4 + rng.integers(1, 5, size=words)
        frames.append(make_message(io, px['chip'], px['channel'], rng.integers(0, 256, words)))
    state = np.zeros(len(geometry.pixels))
    start = time.perf_counter()
    total = count = 0
    while count < messages:
        n = min(batch_messages, messages-count)
        batch = [frames[(count+j) % len(frames)] for j in range(n)]
        hits = decode_batch(batch, strict=True)
        ids = geometry.lookup(iog, hits)
        if np.any(ids < 0):
            raise RuntimeError('benchmark generated unmapped hits')
        state[ids] = count+1
        total += len(ids)
        count += n
    elapsed = time.perf_counter()-start
    return dict(kind='synthetic batched decoder + parity + LUT; NO ASIC playback or networking',
                python=platform.python_version(), numpy=np.__version__, platform=platform.platform(),
                words_per_message=words, batch_messages=batch_messages, messages=messages,
                hits=total, elapsed_s=elapsed, messages_per_s=messages/elapsed,
                hits_per_s=total/elapsed, relative_to_330khz=total/elapsed/330000)


def parser():
    p = argparse.ArgumentParser(description='2x2 read-only raw phosphor display')
    #p.add_argument('--version', action='version', version='raw-display 0.4.2')
    p.add_argument('--version', action='version', version=f'raw-display {__version__}')
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('serve', 'probe', 'check'):
        s = sub.add_parser(name)
        s.add_argument('--pacman-config', default=DEFAULT_IO)
        s.add_argument('--geometry-dir', default='layout')
        s.add_argument('--run-config', default=DEFAULT_RUN_CONFIG)
        s.add_argument('--iog', nargs='+', type=int)
        if name in ('serve', 'probe'):
            s.add_argument('--hwm', type=positive_int, default=4096)
            s.add_argument('--batch-messages', type=positive_int, default=256)
            s.add_argument('--time-basis', choices=('asic', 'host'), default='asic')
            s.add_argument('--min-raw-timestamp', type=raw_tick_cut, default=10,
                           help='display only: reject raw ASIC timestamps below this many ticks; 0 disables')
            s.add_argument('--playback-delay', type=float, default=1.25)
            s.add_argument('--tick-ns', type=float, default=100.)
            s.add_argument('--rollover-ticks', type=int, default=10_000_000)
            s.add_argument('--sync-type', type=int, default=83, help='83=ASCII S, PPS SYNC subtype')
            s.add_argument('--max-pending-hits', type=int, default=500000, help='per IOG')
        if name == 'probe':
            s.add_argument('--seconds', type=positive, default=30.)
    d = sub.add_parser('demo', help='synthetic host-time activity; no hardware connections')
    d.add_argument('--demo-rate', type=positive, default=330000.)
    for s in (sub.choices['serve'], d):
        s.add_argument('--host', type=loopback_host, default='127.0.0.1')
        s.add_argument('--port', type=int, default=8080)
        s.add_argument('--frame-hz', type=positive, default=10.)
        s.add_argument('--max-clients', type=int, default=4)
    sub.add_parser('fetch-geometry').add_argument('--geometry-dir', default='layout')
    b = sub.add_parser('benchmark', help='decoder-only benchmark; tools/benchmark_timing.py includes playback')
    b.add_argument('--geometry-dir')
    b.add_argument('--words', type=positive_int, default=1024)
    b.add_argument('--messages', type=positive_int, default=2000)
    b.add_argument('--batch-messages', type=positive_int, default=256)
    return p


def main():
    args = parser().parse_args()
    if args.command in ('serve', 'probe') and args.time_basis == 'asic':
        from .timing_cli import run
        run(args)
        return
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        if args.command == 'fetch-geometry':
            download_geometry(args.geometry_dir)
            return
        if args.command == 'benchmark':
            if args.words > 65535 or args.messages > 1000000 or args.batch_messages > 8192:
                raise ValueError('benchmark limits exceeded')
            geo = load_geometry(args.geometry_dir, range(1,9)) if args.geometry_dir else demo_geometry()
            print(json.dumps(benchmark(geo, args.words, args.messages, args.batch_messages), indent=2))
            return
        demo = args.command == 'demo'
        endpoints = {} if demo else read_endpoints(args.pacman_config, args.iog)
        if not demo:
            read_asic_versions(args.run_config, endpoints)
        geo = demo_geometry() if demo else load_geometry(args.geometry_dir, endpoints)
        print(f'{"DEMO (no hardware)" if demo else "HOST-TIME / LEGACY16 / PACKET-V2"}: '
              f'{len(geo.pixels):,} pixels, IOGs {geo.metadata["iogs"]}', flush=True)
        if args.command == 'check':
            for t in geo.metadata['tiles']:
                print(f'IOG {t["iog"]} tile {t["tile"]} -> geometry tile {t["geometry_tile"]}: {t["count"]} pixels')
            print('No sockets opened. Geometry SHA256:')
            print(json.dumps(geo.metadata['provenance'], indent=2))
            return
        if not demo and (args.hwm > 100000 or args.batch_messages > 8192):
            raise ValueError('invalid collector settings')
        if args.command in ('serve', 'demo') and not (args.host == '127.0.0.1' and
                1 <= args.port <= 65535 and .5 <= args.frame_hz <= 30 and 1 <= args.max_clients <= 16):
            raise ValueError('invalid web settings; only 127.0.0.1 may be bound')
        shared, proc = start_collector(geo, endpoints, demo=demo,
            demo_rate=getattr(args, 'demo_rate', 330000), hwm=getattr(args, 'hwm', 4096),
            batch_messages=getattr(args, 'batch_messages', 256),
            min_raw_timestamp=getattr(args, 'min_raw_timestamp', 0))
        try:
            if args.command == 'probe':
                start = last = time.monotonic()
                before = None
                while time.monotonic()-start < args.seconds:
                    time.sleep(1)
                    _, stats, _ = snapshot(shared)
                    now = time.monotonic()
                    if not proc.is_alive():
                        raise RuntimeError('collector failed')
                    if before is not None:
                        for iog in endpoints:
                            d = (stats[iog]-before[iog])/(now-last)
                            print(f'IOG {iog}: rx={d[COL["messages"]]:,.0f} msg/s; '
                                  f'data={d[COL["data_hits"]]:,.0f}/s valid={d[COL["valid_data_hits"]]:,.0f}/s '
                                  f'U/D={d[COL["upstream"]]:,.0f}/{d[COL["downstream"]]:,.0f}/s; '
                                  f'mapped={d[COL["mapped_hits"]]:,.0f}/s unmapped={d[COL["unmapped_hits"]]:,.0f}/s; '
                                  f'malformed={d[COL["malformed"]]:.0f}/s', flush=True)
                    before, last = stats, now
            else:
                import uvicorn
                from .server import create_app
                uvicorn.run(create_app(geo, shared, proc, demo, args.frame_hz, args.max_clients,
                                       min_raw_timestamp=getattr(args, 'min_raw_timestamp', 0)),
                            host='127.0.0.1', port=args.port, workers=1, access_log=False,
                            ws_max_size=1024, ws_max_queue=1, ws_per_message_deflate=False)
        finally:
            stop_collector(shared, proc)
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        logging.error('%s', exc)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
