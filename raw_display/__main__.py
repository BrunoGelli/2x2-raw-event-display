"""CLI: safe demo/check first, then PACMAN probe, then live serving."""
import argparse
import json
import logging
import platform
import sys
import time
import numpy as np
from .codec import make_message, decode_batch
from .geometry import demo_geometry, load_geometry, download_geometry
from .runtime import (DEFAULT_IO, DEFAULT_RUN_CONFIG, read_endpoints, read_asic_versions,
                      start_collector, stop_collector, snapshot, COL)


def positive(value):
    v = float(value)
    if not np.isfinite(v) or v <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return v


def positive_int(value):
    v = int(value)
    if v <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return v


def loopback_host(value):
    """Fermilab deployment policy for this application: loopback only."""
    if value != "127.0.0.1":
        raise argparse.ArgumentTypeError("raw-display may bind only to 127.0.0.1")
    return value


def benchmark(geometry, words=1024, messages=2000, batch_messages=256):
    """Synthetic fixed-v2 legacy replay through batched decode + LUT + state.

    Does NOT measure ZMQ reception, shared-memory publication, browser or VNC.
    """
    rng = np.random.default_rng(42)
    iog = geometry.metadata["iogs"][0]
    pool = np.flatnonzero(geometry.pixels["iog"] == iog)
    frames = []
    for _ in range(min(messages, 512)):
        p = geometry.pixels[rng.choice(pool, size=words)]
        io = (p["tile"].astype(int) - 1) * 4 + rng.integers(1, 5, size=words)
        frames.append(make_message(io, p["chip"], p["channel"],
                                   rng.integers(0, 256, words)))

    state = np.zeros(len(geometry.pixels))
    start = time.perf_counter()
    total_hits = 0
    total_messages = 0
    while total_messages < messages:
        n = min(batch_messages, messages - total_messages)
        batch = [frames[(total_messages + j) % len(frames)] for j in range(n)]
        hits = decode_batch(batch, strict=True)
        ids = geometry.lookup(iog, hits)
        if np.any(ids < 0):
            raise RuntimeError("benchmark generated unmapped hits")
        state[ids] = total_messages + 1
        total_hits += len(ids)
        total_messages += n
    elapsed = time.perf_counter() - start
    return dict(
        kind="synthetic batched legacy16/Packet_v2 decode + parity + LUT + state",
        python=platform.python_version(),
        numpy=np.__version__,
        platform=platform.platform(),
        words_per_message=words,
        batch_messages=batch_messages,
        messages=messages,
        hits=total_hits,
        elapsed_s=elapsed,
        messages_per_s=messages / elapsed,
        hits_per_s=total_hits / elapsed,
        relative_to_330khz=(total_hits / elapsed) / 330000,
    )


def parser():
    p = argparse.ArgumentParser(description="2x2 read-only raw phosphor display")
    sub = p.add_subparsers(dest="command", required=True)

    for name in ("serve", "probe", "check"):
        s = sub.add_parser(name)
        s.add_argument("--pacman-config", default=DEFAULT_IO)
        s.add_argument("--geometry-dir", default="layout")
        s.add_argument("--run-config", default=DEFAULT_RUN_CONFIG,
                       help="authoritative CRS RUN_CONFIG.json; selected IOGs must be Packet_v2-compatible")
        s.add_argument("--iog", nargs="+", type=int, help="subset from pacman.json; default all")
        if name in ("serve", "probe"):
            s.add_argument("--hwm", type=positive_int, default=4096,
                           help="receive HWM in ZMQ messages per PACMAN")
            s.add_argument("--batch-messages", type=positive_int, default=256,
                           help="maximum messages drained from one PACMAN before one NumPy decode")
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

    b = sub.add_parser("benchmark", help="offline batched synthetic replay, no sockets")
    b.add_argument("--geometry-dir", help="otherwise use explicitly synthetic geometry")
    b.add_argument("--words", type=positive_int, default=1024)
    b.add_argument("--messages", type=positive_int, default=2000)
    b.add_argument("--batch-messages", type=positive_int, default=256)
    return p


def main():
    args = parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.command == "fetch-geometry":
            download_geometry(args.geometry_dir)
            return

        if args.command == "benchmark":
            if args.words > 65535 or args.messages > 1000000 or args.batch_messages > 8192:
                raise ValueError("words <=65535, messages <=1000000, batch-messages <=8192 required")
            geo = load_geometry(args.geometry_dir, range(1, 9)) if args.geometry_dir else demo_geometry()
            print(json.dumps(benchmark(geo, args.words, args.messages, args.batch_messages), indent=2))
            return

        demo = args.command == "demo"
        endpoints = {} if demo else read_endpoints(args.pacman_config, args.iog)
        versions = ({iog: 2 for iog in range(1, 9)} if demo
                    else read_asic_versions(args.run_config, endpoints))
        geo = demo_geometry() if demo else load_geometry(args.geometry_dir, endpoints)
        geo.metadata["asic_versions"] = versions

        print(
            f"{'DEMO (no hardware)' if demo else 'LEGACY16 / PACKET-V2 / BATCHED'}: "
            f"{len(geo.pixels):,} geometry pixels, IO groups {geo.metadata['iogs']}",
            flush=True,
        )

        if args.command == "check":
            for t in geo.metadata["tiles"]:
                print(f"IOG {t['iog']} tile {t['tile']} -> geometry tile {t['geometry_tile']}: "
                      f"{t['count']} pixels, {t['width']}x{t['height']}")
            print(f"Verified Packet_v2-compatible ASIC families from {args.run_config}: {versions}")
            print("No sockets were opened. Geometry SHA256:")
            print(json.dumps(geo.metadata["provenance"], indent=2))
            return

        if not demo:
            if args.hwm > 100000 or args.batch_messages > 8192:
                raise ValueError("hwm <=100000 and batch-messages <=8192 required")

        if args.command in ("demo", "serve"):
            if args.host != "127.0.0.1":
                raise ValueError("raw-display may bind only to 127.0.0.1")
            if not 1 <= args.port <= 65535 or not 0.5 <= args.frame_hz <= 30 or not 1 <= args.max_clients <= 16:
                raise ValueError("port 1..65535, frame-hz 0.5..30, max-clients 1..16 required")

        shared, proc = start_collector(
            geo, endpoints, demo=demo,
            demo_rate=getattr(args, "demo_rate", 330000),
            hwm=getattr(args, "hwm", 4096),
            batch_messages=getattr(args, "batch_messages", 256),
        )
        try:
            if args.command == "probe":
                start, last = time.monotonic(), time.monotonic()
                previous = None
                while time.monotonic() - start < args.seconds:
                    time.sleep(1)
                    _, stats, _beat = snapshot(shared)
                    now = time.monotonic()
                    if not proc.is_alive():
                        raise RuntimeError("collector process failed")
                    if previous is not None:
                        dt = now - last
                        for iog in endpoints:
                            delta = stats[iog] - previous[iog]
                            r = lambda key: delta[COL[key]] / dt
                            ptypes = "/".join(f"{r('packet_type_'+str(k)):,.0f}" for k in range(4))
                            print(
                                f"IOG {iog}: "
                                f"rx={r('messages'):,.0f} msg/s {r('bytes')/1e6:.2f} MB/s; "
                                f"words={r('words'):,.0f}/s D={r('data_words'):,.0f}/s; "
                                f"ptype0/1/2/3={ptypes}/s; "
                                f"data={r('data_hits'):,.0f}/s valid={r('valid_data_hits'):,.0f}/s "
                                f"U/D={r('upstream'):,.0f}/{r('downstream'):,.0f}/s; "
                                f"mapped={r('mapped_hits'):,.0f}/s unmapped={r('unmapped_hits'):,.0f}/s; "
                                f"legacy={r('legacy_messages'):,.0f}/s "
                                f"nonDmsg={r('nondata_messages'):,.0f}/s "
                                f"malformed+={int(delta[COL['malformed']])}",
                                flush=True,
                            )
                    previous, last = stats, now
                print("Probe ended. Transport drops are not measurable from received message counts.")
            else:
                import uvicorn
                from .server import create_app
                app = create_app(geo, shared, proc, demo, args.frame_hz, args.max_clients)
                uvicorn.run(
                    app, host=args.host, port=args.port, workers=1, access_log=False,
                    ws_max_size=1024, ws_max_queue=1, ws_per_message_deflate=False,
                )
        finally:
            stop_collector(shared, proc)

    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        logging.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
