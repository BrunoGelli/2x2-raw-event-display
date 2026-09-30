"""CLI: safe demo/check first, then one-PACMAN probe, then live serving."""
import argparse
import json
import logging
import platform
import sys
import time
import numpy as np
from .codec import make_message, decode
from .geometry import demo_geometry, load_geometry, download_geometry
from .runtime import (DEFAULT_IO, DEFAULT_RUN_CONFIG, read_endpoints, read_asic_versions,
                      start_collector, stop_collector, snapshot, COL)


def positive(value):
    v = float(value)
    if not np.isfinite(v) or v <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return v

    

def loopback_host(value):
    """Fermilab deployment policy for this application: loopback only."""
    if value != "127.0.0.1":
        raise argparse.ArgumentTypeError("raw-display may bind only to 127.0.0.1")
    return value


def benchmark(geometry, words=1024, messages=2000):
    """Synthetic wire replay: decode + parity + geometry LUT + last-hit update.

    Does NOT measure network, actual PACMAN burst behavior, IPC, browser or VNC.
    Uses 64 different messages rather than repeatedly decoding one cache-hot word.
    """
    rng, frames = np.random.default_rng(42), []
    for k in range(64):
        iog = geometry.metadata["iogs"][k % len(geometry.metadata["iogs"])]
        pool = np.flatnonzero(geometry.pixels["iog"] == iog)
        p = geometry.pixels[rng.choice(pool, size=words)]
        io = (p["tile"].astype(int) - 1) * 4 + rng.integers(1, 5, size=words)
        frames.append((iog, make_message(io, p["chip"], p["channel"], rng.integers(0, 256, words))))
    state = np.zeros(len(geometry.pixels))
    start = time.perf_counter()
    for k in range(messages):
        iog, raw = frames[k % len(frames)]
        hits = decode(raw)
        ids = geometry.lookup(iog, hits)
        if np.any(ids < 0):
            raise RuntimeError("benchmark generated unmapped hits")
        state[ids] = k + 1
    elapsed = time.perf_counter() - start
    return dict(kind="synthetic decode + parity + LUT + state; no networking or browser",
                python=platform.python_version(), numpy=np.__version__,
                platform=platform.platform(), words_per_message=words, messages=messages,
                hits=words * messages, elapsed_s=elapsed,
                hits_per_s=words * messages / elapsed,
                relative_to_330khz=(words * messages / elapsed) / 330000)


def parser():
    p = argparse.ArgumentParser(description="2x2 read-only raw phosphor display")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("serve", "probe", "check"):
        s = sub.add_parser(name)
        s.add_argument("--pacman-config", default=DEFAULT_IO)
        s.add_argument("--geometry-dir", default="layout")
        s.add_argument("--run-config", default=DEFAULT_RUN_CONFIG,
                       help="authoritative CRS RUN_CONFIG.json for io_group_asic_version_")
        s.add_argument("--iog", nargs="+", type=int, help="subset from pacman.json; default all")
        if name in ("serve", "probe"):
            s.add_argument("--hwm", type=int, default=128, help="receive HWM in messages, not hits")
        if name == "probe":
            s.add_argument("--seconds", type=positive, default=30)
    d = sub.add_parser("demo", help="synthetic geometry/activity; no PACMAN connections")
    d.add_argument("--demo-rate", type=positive, default=330000)
    for s in (sub.choices["serve"], d):
        s.add_argument("--host", type=loopback_host, default="127.0.0.1",
                       help="web bind address; intentionally restricted to 127.0.0.1")
        s.add_argument("--port", type=int, default=8080)
        s.add_argument("--frame-hz", type=positive, default=10)
        s.add_argument("--max-clients", type=int, default=4)
    fetch = sub.add_parser("fetch-geometry", help="download pinned PacMon JSONs once")
    fetch.add_argument("--geometry-dir", default="layout")
    b = sub.add_parser("benchmark", help="offline synthetic packet replay, no sockets")
    b.add_argument("--geometry-dir", help="otherwise use explicitly synthetic geometry")
    b.add_argument("--words", type=int, default=1024)
    b.add_argument("--messages", type=int, default=2000)
    return p


def main():
    args = parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.command == "fetch-geometry":
            download_geometry(args.geometry_dir)
            return
        if args.command == "benchmark":
            if not 1 <= args.words <= 65535 or not 1 <= args.messages <= 1000000:
                raise ValueError("words must be 1..65535; messages 1..1000000")
            geo = load_geometry(args.geometry_dir, range(1, 9)) if args.geometry_dir else demo_geometry()
            print(json.dumps(benchmark(geo, args.words, args.messages), indent=2))
            return
        demo = args.command == "demo"
        endpoints = {} if demo else read_endpoints(args.pacman_config, args.iog)
        versions = ({iog: 2 for iog in range(1, 9)} if demo
                    else read_asic_versions(args.run_config, endpoints))
        geo = demo_geometry() if demo else load_geometry(args.geometry_dir, endpoints)
        geo.metadata["asic_versions"] = versions
        print(f"{'DEMO (no hardware)' if demo else 'PACMAN AUTO-FRAME'}: "
              f"{len(geo.pixels):,} geometry pixels, IO groups {geo.metadata['iogs']}, "
              f"ASIC packet families {versions}", flush=True)
        if args.command == "check":
            for t in geo.metadata["tiles"]:
                print(f"IOG {t['iog']} tile {t['tile']} -> geometry tile {t['geometry_tile']}: "
                      f"{t['count']} pixels, {t['width']}x{t['height']}")
            print(f"ASIC packet families from {args.run_config}: {versions}")
            print("No sockets were opened. Geometry SHA256:")
            print(json.dumps(geo.metadata["provenance"], indent=2))
            return
        if not demo and not 1 <= args.hwm <= 10000:
            raise ValueError("HWM must be in 1..10000 messages")
        if args.command in ("demo", "serve"):
            if args.host != "127.0.0.1":
                raise ValueError("raw-display may bind only to 127.0.0.1")
            if not 1 <= args.port <= 65535 or not 0.5 <= args.frame_hz <= 30 or not 1 <= args.max_clients <= 16:
                raise ValueError("port 1..65535, frame-hz 0.5..30, max-clients 1..16 required")
        shared, proc = start_collector(geo, endpoints, demo=demo,
            demo_rate=getattr(args, "demo_rate", 330000), hwm=getattr(args, "hwm", 128),
            asic_versions=versions)
        try:
            if args.command == "probe":
                start, last = time.monotonic(), time.monotonic()
                previous = None
                while time.monotonic() - start < args.seconds:
                    time.sleep(1)
                    _, stats, beat = snapshot(shared)
                    now = time.monotonic()
                    if not proc.is_alive():
                        raise RuntimeError("collector process failed")
                    if previous is not None:
                        dt = now - last
                        for iog in endpoints:
                            delta = stats[iog] - previous[iog]
                            r = lambda key: delta[COL[key]] / dt
                            frames = f"legacy/new={r('legacy_messages'):,.0f}/{r('new24_messages'):,.0f}"
                            ptypes = "/".join(f"{r('packet_type_'+str(k)):,.0f}" for k in range(4))
                            print(
                                f"IOG {iog} [ASIC v{versions[iog]}]: "
                                f"rx={r('messages'):,.0f} msg/s {r('bytes')/1e6:.2f} MB/s; "
                                f"words={r('words'):,.0f}/s D={r('data_words'):,.0f}/s; "
                                f"ptype0/1/2/3={ptypes}/s; "
                                f"data={r('data_hits'):,.0f}/s valid={r('valid_data_hits'):,.0f}/s "
                                f"U/D={r('upstream'):,.0f}/{r('downstream'):,.0f}/s; "
                                f"mapped={r('mapped_hits'):,.0f}/s unmapped={r('unmapped_hits'):,.0f}/s; "
                                f"{frames}; nonDmsg={r('nondata_messages'):,.0f}/s "
                                f"malformed+={int(delta[COL['malformed']])}", flush=True)
                    previous, last = stats, now
                print("Probe ended. Transport drops are not measurable from received message counts.")
            else:
                import uvicorn
                from .server import create_app
                app = create_app(geo, shared, proc, demo, args.frame_hz, args.max_clients)
                uvicorn.run(app, host=args.host, port=args.port, workers=1, access_log=False,
                            ws_max_size=1024, ws_max_queue=1, ws_per_message_deflate=False)
        finally:
            stop_collector(shared, proc)
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        logging.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
