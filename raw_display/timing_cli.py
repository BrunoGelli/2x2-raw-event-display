"""ASIC-time default entry point; old demo/check/benchmark commands still work."""
import argparse
from dataclasses import replace
import json
import logging
import os
import sys
import time
import numpy as np
from .runtime import read_endpoints, read_asic_versions, DEFAULT_RUN_CONFIG, stop_collector, COL
from .geometry import load_geometry
from .timing import TimingConfig, TIMING_COL
from .timed_runtime import start_timed_collector, snapshot_timed
from .trigger_windows import ObserverConfig


def run(args):
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        if args.command == 'serve' and args.host != '127.0.0.1':
            raise ValueError('raw-display may bind only to 127.0.0.1')
        config = TimingConfig(tick_seconds=args.tick_ns*1e-9,
                              rollover_ticks=args.rollover_ticks, sync_type=args.sync_type,
                              playback_delay=args.playback_delay, max_pending_hits=args.max_pending_hits,
                              min_raw_timestamp=args.min_raw_timestamp)
        if not 1 <= args.hwm <= 100000 or not 1 <= args.batch_messages <= 8192:
            raise ValueError('invalid HWM or batch-message limit')
        if args.command == 'serve' and not (1 <= args.port <= 65535 and
                .5 <= args.frame_hz <= 30 and 1 <= args.max_clients <= 16):
            raise ValueError('invalid web server settings')
        endpoints = read_endpoints(args.pacman_config, args.iog)
        versions = read_asic_versions(args.run_config, endpoints)
        geo = load_geometry(args.geometry_dir, endpoints)
        geo.metadata['asic_versions'] = versions
        observers = ObserverConfig.from_env()
        geometry3d, geometry3d_error = None, None
        if observers.view3d:
            from .geometry3d import load_geometry3d, DEFAULT_DIRECTORY
            try:
                geometry3d = load_geometry3d(geo, os.environ.get('RAW_DISPLAY_3D_GEOMETRY', DEFAULT_DIRECTORY))
            except (ValueError, OSError, KeyError, TypeError) as exc:
                geometry3d_error = str(exc)
                logging.warning('3D disabled; continuing normal 2D display: %s', exc)
                observers = replace(observers, view3d=False)
        print(f'ASIC-TIME / LEGACY16 / PACKET-V2: {len(geo.pixels):,} pixels, IOGs {list(endpoints)}', flush=True)
        print(f'PPS subtype={config.sync_type}, rollover={config.rollover_ticks}, '
              f'tick={args.tick_ns:g} ns, playback reserve={config.playback_delay:g} s. '
              f'display cut: raw timestamp < {config.min_raw_timestamp} ticks. '
              'IOG-relative epochs; waiting for PPS, no arrival-time fallback.', flush=True)
        shared, proc = start_timed_collector(geo, endpoints, config, args.hwm, args.batch_messages, observers)
        try:
            if args.command == 'serve':
                import uvicorn
                from .timed_server import create_timed_app
                uvicorn.run(create_timed_app(geo, shared, proc, config, args.frame_hz, args.max_clients,
                                            geometry3d=geometry3d, geometry3d_error=geometry3d_error),
                            host='127.0.0.1', port=args.port, workers=1, access_log=False,
                            ws_max_size=1024, ws_max_queue=1, ws_per_message_deflate=False)
            else:
                start = last = time.monotonic()
                before = None
                while time.monotonic()-start < args.seconds:
                    time.sleep(1)
                    _, stats, _, timing, cpu = snapshot_timed(shared)
                    now = time.monotonic()
                    if not proc.is_alive():
                        raise RuntimeError('collector failed')
                    if before is not None:
                        for iog in endpoints:
                            row = timing[iog]
                            get = lambda k: row[TIMING_COL[k]]
                            rate = (stats[iog,COL['mapped_hits']]-before[iog,COL['mapped_hits']])/(now-last)
                            state = 'PLAYING' if get('running') else ('BUFFERING' if get('synchronized') else 'WAITING_PPS')
                            print(f'IOG {iog}: mapped={rate:,.0f}/s {state}; '
                                  f'PPS={get("pps_syncs"):.0f}, boundary={get("boundary_corrected"):.0f}, '
                                  f'late(pre/selected)={get("pre_cut_late_hits"):.0f}/{get("late_hits"):.0f}, '
                                  f'cut={get("post_sync_filtered_hits"):.0f}, pending={get("pending_hits"):.0f}, '
                                  f'buffer_drop={get("buffer_dropped_hits"):.0f}, '
                                  f'bad_sync={get("invalid_syncs"):.0f}, invalid_time={get("invalid_times"):.0f}; '
                                  f'collector CPU={cpu*100:.1f}%', flush=True)
                    before, last = stats, now
                print('Probe complete. Received counts do not certify lossless upstream transport.')
        finally:
            stop_collector(shared, proc)
    except KeyboardInterrupt:
        pass
    except (ValueError, OSError, KeyError, RuntimeError) as exc:
        logging.error('%s', exc)
        raise SystemExit(1)
