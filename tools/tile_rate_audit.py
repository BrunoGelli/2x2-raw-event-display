#!/usr/bin/env python3
"""Capture the existing display's tile-rate audit over loopback HTTP; plot offline.

No PACMAN/ZMQ connection, no listener, no hardware commands. Capture uses only
Python's standard library. Plot mode additionally needs NumPy and Matplotlib.
"""
import argparse
import csv
import json
import math
from pathlib import Path
import time
import urllib.parse
import urllib.request


def url_argument(value):
    u = urllib.parse.urlsplit(value)
    if (u.scheme != "http" or u.hostname != "127.0.0.1" or u.username or u.password
            or u.query or u.fragment or u.path not in ("", "/")):
        raise argparse.ArgumentTypeError("use http://127.0.0.1:PORT only (or an existing loopback tunnel)")
    try:
        port = u.port
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    if port is None:
        raise argparse.ArgumentTypeError("an explicit loopback HTTP port is required")
    return value.rstrip("/")


def capture(args):
    if not math.isfinite(args.seconds) or not 1 <= args.seconds <= 3600:
        raise ValueError("seconds must be in 1..3600")
    # Do not follow redirects out of loopback, even if server configuration changes.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise ValueError("HTTP redirects are disabled for local capture")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def get(after):
        url = args.url + f"/api/tile-rates?after={after}&limit=100"
        with opener.open(url, timeout=3) as response:
            data = json.load(response)
        if not data.get("enabled"):
            raise ValueError(data.get("hint", "Rate audit disabled"))
        return data

    first = get(0)
    if first.get("busy"):
        raise RuntimeError("Rate audit busy; retry the capture, not the collector")
    boot = first["boot_id"]
    after = first["latest"]  # Capture newly published intervals, not old history.
    meta = {k: v for k, v in first.items() if k != "samples"}
    start = time.monotonic()
    count = gaps = 0
    with Path(args.out).open("x") as f:
        f.write(json.dumps({"type": "metadata", "capture_utc_epoch": time.time(), **meta})+"\n")
        try:
            while time.monotonic()-start < args.seconds:
                data = get(after)
                if data["boot_id"] != boot:
                    raise RuntimeError("Collector restarted: do not join clocks/configurations across restarts")
                if data.get("busy"):
                    time.sleep(0.5)
                    continue
                for row in data["samples"]:
                    seq = int(row[0])
                    if seq <= after:
                        continue
                    gap = seq-after-1
                    if gap:
                        gaps += gap
                        f.write(json.dumps({"type": "gap", "after_seq": after, "before_seq": seq})+"\n")
                    f.write(json.dumps({"type": "sample", "values": row})+"\n")
                    after = seq
                    count += 1
                f.flush()
                time.sleep(0.5)
        finally:
            f.write(json.dumps({"type": "capture_end", "samples": count, "missing_intervals": gaps})+"\n")
    print(f"Saved {count} collector intervals to {args.out}; missing intervals: {gaps}.")
    print("These are host-consumption rates, not yet proof of detector-time bursts.")


def load(path):
    metadata, rows = None, []
    with Path(path).open() as f:
        for line in f:
            record = json.loads(line)
            if record.get("type") == "metadata":
                if metadata is not None:
                    raise ValueError("Multiple captures in one file are not supported")
                metadata = record
            elif record.get("type") == "sample":
                rows.append(record["values"])
    if metadata is None or not rows:
        raise ValueError("Need metadata and at least one completed collector interval")
    return metadata, rows


def plot(args):
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    m, values = load(args.file)
    data = np.asarray(values, dtype=float)
    ns, nf = len(m["scalars"]), len(m["source_fields"])
    if data.ndim != 2 or data.shape[1] != ns+128+8*nf or not np.all(np.isfinite(data)):
        raise ValueError("Invalid rate audit array/schema")
    dt = data[:, 2]-data[:, 1]
    if np.any(dt <= 0) or np.any(np.diff(data[:, 0]) <= 0):
        raise ValueError("Intervals/sequence must advance strictly")
    origin = data[0, 1]
    left, right = data[:, 1]-origin, data[:, 2]-origin
    mid = (left+right)/2
    counts = data[:, ns:ns+64]
    unique = data[:, ns+64:ns+128]
    sources = data[:, ns+128:].reshape(-1, 8, nf)
    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)
    groups = sorted(set(args.iog or m["iogs"]))
    if not set(groups) <= set(m["iogs"]):
        raise ValueError("Requested IOG absent from capture")

    def interval_line(ax, y, **kwargs):
        # Explicit breaks prevent lost audit rows being drawn as measured zero rates.
        x, v = [], []
        for k in range(len(y)):
            if k and (data[k, 0] != data[k-1, 0]+1 or abs(left[k]-right[k-1]) > 1e-5):
                x.append(float("nan")); v.append(float("nan"))
            x.extend((left[k], right[k]))
            v.extend((float(y[k]), float(y[k])))
        ax.plot(x, v, **kwargs)

    def finish(fig, ax, name, title, ylabel):
        ax.set(title=title, xlabel="Seconds since first captured interval (host clock)", ylabel=ylabel)
        ax.grid(True, alpha=0.25)
        ax.set_ylim(bottom=0)
        fig.tight_layout()
        fig.savefig(out/name, dpi=140)
        plt.close(fig)

    with (out/"tile_rates.csv").open("x", newline="") as f:
        w = csv.writer(f)
        w.writerow(["seq", "begin_monotonic", "end_monotonic", "duration_s", "io_group", "tile",
                    "mapped_packets", "mapped_packets_per_s", "unique_pixels", "active_fraction"])
        for tile in m["tiles"]:
            iog, t = tile["iog"], tile["tile"]
            if iog not in groups:
                continue
            code = (iog-1)*8+t-1
            rate = counts[:, code]/dt
            fraction = unique[:, code]/tile["count"]
            for k in range(len(data)):
                w.writerow([int(data[k, 0]), data[k, 1], data[k, 2], dt[k], iog, t,
                            int(counts[k, code]), rate[k], int(unique[k, code]), fraction[k]])
            fig, ax = plt.subplots(figsize=(10, 3.6))
            interval_line(ax, rate)
            finish(fig, ax, f"iog{iog}_tile{t}_rate.png",
                   f"IOG {iog} / tile {t}: valid mapped packets, actual publication intervals", "Packets / s")
    for iog in groups:
        fig, ax = plt.subplots(figsize=(10, 4.5))
        for t in range(1, 9):
            interval_line(ax, counts[:, (iog-1)*8+t-1]/dt, label=f"Tile {t}")
        ax.legend(ncol=4, fontsize=8)
        finish(fig, ax, f"iog{iog}_overlay.png", f"IOG {iog}: all tile arrival-rate traces", "Packets / s")
        fig, ax = plt.subplots(figsize=(10, 4.5))
        for t in m["tiles"]:
            if t["iog"] == iog:
                interval_line(ax, unique[:, (iog-1)*8+t["tile"]-1]/t["count"], label=f"Tile {t['tile']}")
        ax.set_ylim(0, 1)
        ax.legend(ncol=4, fontsize=8)
        finish(fig, ax, f"iog{iog}_active_fraction.png", f"IOG {iog}: fraction of pixels hit in each interval", "Active fraction")
        fig, ax = plt.subplots(figsize=(10, 3.6))
        jmsg, jbatch = m["source_fields"].index("messages"), m["source_fields"].index("batches")
        nb = sources[:, iog-1, jbatch]
        avg = np.divide(sources[:, iog-1, jmsg], nb, out=np.full_like(nb, np.nan), where=nb>0)
        interval_line(ax, avg)
        finish(fig, ax, f"iog{iog}_batch_size.png", f"IOG {iog}: actual mean messages per decoded batch", "Messages / batch")
    fig, ax = plt.subplots(figsize=(10, 3.6))
    ax.plot(mid, dt*1000, label="Publication interval", marker=".")
    ax.plot(mid, data[:, m["scalars"].index("max_loop_gap_s")]*1000, label="Largest collector-loop gap")
    ax.legend()
    finish(fig, ax, "collector_timing.png", "Collector publication timing: ~100 ms target, not guaranteed", "Milliseconds")
    fig, ax = plt.subplots(figsize=(10, 3.6))
    ax.plot(mid, data[:, m["scalars"].index("cpu_seconds")]/dt)
    finish(fig, ax, "collector_cpu.png", "Collector process CPU time / wall interval", "CPU core equivalents")
    print(f"Wrote tile_rates.csv and plots to {out}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("capture")
    c.add_argument("--url", type=url_argument, default="http://127.0.0.1:8765")
    c.add_argument("--seconds", type=float, default=60)
    c.add_argument("--out", required=True)
    q = sub.add_parser("plot")
    q.add_argument("file")
    q.add_argument("--outdir", required=True)
    q.add_argument("--iog", nargs="+", type=int)
    a = p.parse_args()
    try:
        capture(a) if a.command == "capture" else plot(a)
    except (OSError, ValueError, RuntimeError) as exc:
        p.exit(1, f"ERROR: {exc}\n")


if __name__ == "__main__":
    main()
