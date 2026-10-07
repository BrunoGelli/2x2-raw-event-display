"""ASIC-time snapshot protocol; browser clocks never assign hit timestamps."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
import struct
import time
import numpy as np
from .post_sync import sync83_message
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from .runtime import FIELDS, COL
from .timed_runtime import snapshot_timed
from .timing import TIMING_FIELDS, TIMING_COL, TimingConfig
from .rate_audit import read_rate_ring
from .common_timing import SOURCE_NAMES, TRIGGER_SOURCES, FEATURE_BUILD
from .observer_runtime import snapshot_with_triggers, encode_trigger_frame, read_observers

HEADER = struct.Struct('<4sII')
CLOCK = struct.Struct('<ddd')       # cursor seconds, available frontier, running
RECORD = np.dtype([('id', '<u4'), ('time', '<f8')])
FRAME_OFFSET = HEADER.size + 8 * CLOCK.size


def encode_timed_frame(previous, current, timing, sequence):
    ids = np.flatnonzero(current != previous)
    records = np.empty(len(ids), dtype=RECORD)
    records['id'], records['time'] = ids, current[ids]
    parts = [HEADER.pack(b'RDP2', sequence, len(ids))]
    for iog in range(1, 9):
        row = timing[iog]
        parts.append(CLOCK.pack(row[TIMING_COL['cursor_s']],
                                row[TIMING_COL['frontier_s']], row[TIMING_COL['running']]))
    return b''.join(parts) + records.tobytes()


def create_timed_app(geometry, shared, proc, config=None, frame_hz=10., max_clients=4):
    config = config or TimingConfig()
    static = Path(__file__).with_name('static')
    times = np.zeros((9, len(TIMING_FIELDS)))
    times[:, :2] = -1
    hub = dict(seen=np.full(len(geometry.pixels), -1.), stats=np.zeros((9, len(FIELDS))),
               heartbeat=0., timing=times, cpu=0., sequence=0, clients=0, tagged=None, source_layers={})

    async def pump():
        while True:
            seen, stats, heartbeat, timing, cpu, tagged, source_layers = await asyncio.to_thread(
                snapshot_with_triggers, shared, FIELDS, TIMING_FIELDS, True)
            hub.update(seen=seen, stats=stats, heartbeat=heartbeat, timing=timing,
                       cpu=cpu, tagged=tagged, source_layers=source_layers, sequence=(hub['sequence']+1) & 0xffffffff)
            await asyncio.sleep(1/frame_hz)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(pump())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title='2x2 ASIC-time raw display', lifespan=lifespan)
    app.mount('/static', StaticFiles(directory=static), name='static')

    @app.get('/')
    def index():
        return FileResponse(static/'index.html', headers={'Cache-Control': 'no-cache'})

    @app.get('/api/geometry')
    def metadata():
        return {**geometry.metadata, 'mode': 'LIVE', 'protocol': 2,
                'time_basis': 'asic', 'timing_config': asdict(config),
                'feature_build': FEATURE_BUILD, 'playback_clock': 'pps-playhead-1',
                'trigger_source_protocol': 1,
                'trigger_sources': {name: i for i, name in TRIGGER_SOURCES.items()},
                'trigger_view_enabled': bool(getattr(shared, 'trigger_seen', None) is not None),
                'trigger_scope': 'detector-wide candidates on qualified PPS epochs',
                'trigger_pre_us': getattr(getattr(shared, 'observer_config', None), 'pre_us', 0.),
                'trigger_post_us': getattr(getattr(shared, 'observer_config', None), 'post_us', 300.),
                'timing_alignment': 'header-labelled PPS epochs; hardware phase and epoch labelling require live verification',
                'wire_format': 'legacy16 / Packet_v2 / batched', 'frame_hz': frame_hz}

    @app.get('/api/geometry.bin')
    def geometry_bin():
        return Response(geometry.pixels.tobytes(), media_type='application/octet-stream')

    @app.get('/api/status')
    def status():
        now = time.monotonic()
        sources = []
        for iog in geometry.metadata['iogs']:
            row = hub['stats'][iog]
            values = {key: int(row[COL[key]]) for key in FIELDS if not key.startswith('last_')}
            values['iog'] = iog
            for key in ('rx', 'trigger', 'sync83'):
                stamp = row[COL['last_'+key]]
                values[key+'_age_s'] = max(0., now-stamp) if stamp else None
            ts = {key: float(hub['timing'][iog, j]) for j, key in enumerate(TIMING_FIELDS)}
            ts['buffer_seconds'] = max(0., ts['frontier_s']-ts['cursor_s']) if ts['cursor_s'] >= 0 else None
            values['timing'] = ts
            sources.append(values)
        age = max(0., now-hub['heartbeat']) if hub['heartbeat'] else None
        observers = read_observers(shared)
        captured = observers.get('captured_monotonic')
        alignment = dict(session_id=observers.get('session_id'),
                         common_timing=observers.get('common_timing'),
                         capture_age_s=None if captured is None else max(0., now-captured))
        return dict(mode='LIVE', time_basis='unrolled ASIC time (IOG-relative)',
                    collector_alive=proc.is_alive(), trigger_alignment=alignment,
                    server_unix_s=time.time(),  # approximate wall-clock comparison only; never a hit time
                    collector_healthy=bool(proc.is_alive() and age is not None and age < 3),
                    collector_age_s=age, collector_cpu_fraction=float(hub['cpu']), sources=sources,
                    clients=hub['clients'], transport_loss='not measurable from this stream',
                    min_raw_timestamp=config.min_raw_timestamp)

    @app.get('/api/observers')
    def observer_status():
        data = read_observers(shared)
        stamp = data.get('captured_monotonic')
        data['capture_age_s'] = max(0., time.monotonic()-stamp) if stamp is not None else None
        return JSONResponse(data, headers={'Cache-Control': 'no-store'})

    @app.get('/api/timing-audit')
    def timing_audit_status():
        data = read_observers(shared)
        stamp = data.get('captured_monotonic')
        return JSONResponse(dict(version=1, session_id=data.get('session_id'), captured_monotonic=stamp, enabled_iogs=data.get('audit_iogs', []),
            capture_age_s=max(0., time.monotonic()-stamp) if stamp is not None else None,
            audits=data.get('audits', [])), headers={'Cache-Control': 'no-store'})

    @app.get('/api/tile-rates')
    def tile_rates(after: int = 0, limit: int = 100):
        try:
            data = read_rate_ring(shared.rate_ring, after, limit)
        except ValueError as exc:
            return JSONResponse({'error': str(exc)}, status_code=400)
        data.update(mode='LIVE', geometry_id=geometry.metadata['geometry_id'],
                    tiles=geometry.metadata['tiles'], iogs=geometry.metadata['iogs'],
                    time_basis='host consumption, deliberately retained as an independent control')
        return JSONResponse(data, headers={'Cache-Control': 'no-store'})

    @app.get('/healthz')
    def health():
        data = status()
        return JSONResponse(data, status_code=200 if data['collector_healthy'] else 503)

    @app.websocket('/ws')
    async def ws_stream(ws: WebSocket):
        origin, host = ws.headers.get('origin'), ws.headers.get('host', '')
        if (origin and origin not in {'http://'+host, 'https://'+host}) or hub['clients'] >= max_clients:
            await ws.close(code=1008)
            return
        await ws.accept()
        if ws.query_params.get('geometry', geometry.metadata['geometry_id']) != geometry.metadata['geometry_id']:
            await ws.close(code=4009)
            return
        hub['clients'] += 1
        previous, last_sequence = np.full(len(geometry.pixels), -1.), -1
        wants_tagged = ws.query_params.get('trigger_windows') == '1'
        wants_sources = wants_tagged and ws.query_params.get('trigger_sources') == '1'
        previous_sources = {name: np.full(len(geometry.pixels), -1.) for name in SOURCE_NAMES} if wants_sources else {}
        previous_tagged = np.full(len(geometry.pixels), -1.) if wants_tagged else None
        try:
            while True:
                sequence = hub['sequence']
                if sequence == last_sequence:
                    await asyncio.sleep(1/frame_hz)
                    continue
                current, tagged = hub['seen'], hub['tagged']
                source_layers = hub['source_layers']
                frame = encode_timed_frame(previous, current, hub['timing'], sequence)
                if ws.query_params.get('sync83') == '1':
                    await asyncio.wait_for(ws.send_json(sync83_message(
                        hub['stats'], geometry.metadata['iogs'], time.monotonic(), COL,
                        include_triggers=wants_tagged)), timeout=3)
                if wants_sources and source_layers:
                    for name, magic in [('beam', b'RDB1'), ('light', b'RDL1')]:
                        layer_frame = encode_trigger_frame(previous_sources[name], source_layers[name], sequence, magic)
                        await asyncio.wait_for(ws.send_bytes(layer_frame), timeout=3)
                elif wants_tagged and tagged is not None:
                    tagged_frame = encode_trigger_frame(previous_tagged, tagged, sequence)
                    await asyncio.wait_for(ws.send_bytes(tagged_frame), timeout=3)
                await asyncio.wait_for(ws.send_bytes(frame), timeout=3)
                ack = await asyncio.wait_for(ws.receive_text(), timeout=3)
                if ack != str(sequence):
                    await ws.close(code=1008)
                    return
                previous, last_sequence = current, sequence
                if wants_sources and source_layers:
                    previous_sources = source_layers
                if wants_tagged and tagged is not None:
                    previous_tagged = tagged
        except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError):
            pass
        finally:
            hub['clients'] -= 1
            try:
                await ws.close()
            except RuntimeError:
                pass
    return app
