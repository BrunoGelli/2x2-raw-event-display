"""Opt-in Chromium fixture through the real HTTP/WS app; never a DAQ connection."""
import json
import multiprocessing as mp
import os
from pathlib import Path
import shutil
import socket
import threading
import time
import numpy as np
import pytest
from raw_display.geometry import demo_geometry
from raw_display.runtime import make_shared, COL
from raw_display.timing import TimingConfig, TIMING_FIELDS, TIMING_COL
from raw_display.trigger_windows import ObserverConfig
from raw_display.observer_runtime import attach_observers
from raw_display.timed_server import create_timed_app


@pytest.mark.skipif(os.environ.get('RAW_DISPLAY_BROWSER_TEST') != '1',
                    reason='opt-in graphical test; no browser needed on DAQ')
def test_real_canvas_page_source_controls_and_protocol(tmp_path):
    sync_api = pytest.importorskip('playwright.sync_api')
    import uvicorn
    executable = os.environ.get('CHROMIUM_EXECUTABLE') or shutil.which('chromium')
    if not executable:
        pytest.skip('Chromium not installed')
    geo = demo_geometry(iogs=range(1, 9), chips_per_tile=1)
    ctx = mp.get_context('spawn')
    shared = make_shared(ctx, len(geo.pixels))
    attach_observers(ctx, shared, len(geo.pixels), range(1, 9), ObserverConfig(trigger_view=True))
    shared.timing = ctx.RawArray('d', 9*len(TIMING_FIELDS))
    shared.cpu_ratio = ctx.RawValue('d', 0.)
    shared.rate_ring = None
    clocks = np.frombuffer(shared.timing).reshape(9, len(TIMING_FIELDS))
    clocks[:, TIMING_COL['cursor_s']] = 2.1
    clocks[:, TIMING_COL['frontier_s']] = 3.1
    clocks[:, TIMING_COL['synchronized']] = 1
    np.frombuffer(shared.seen).fill(-1.)
    for iog in range(1, 9):
        beam, light = geo.lut[iog, 1, 11, 0], geo.lut[iog, 1, 11, 1]
        np.frombuffer(shared.seen)[[beam, light]] = [2.09, 2.095]
        np.frombuffer(shared.trigger_source_seen['beam'])[beam] = 2.09
        np.frombuffer(shared.trigger_source_seen['light'])[light] = 2.095
    stop = threading.Event()
    def update():
        while not stop.is_set():
            now = time.monotonic()
            shared.heartbeat.value = now
            info = dict(common_timing=dict(stale_after_s=2.5, hardware_phase_verified=False,
                sources=[dict(iog=i, status='aligned', last_valid_age_s=.1) for i in range(1, 9)]),
                captured_monotonic=now)
            raw = json.dumps(info).encode()
            with shared.observer_lock:
                np.frombuffer(shared.observer_json, dtype='u1')[:len(raw)] = np.frombuffer(raw, dtype='u1')
                shared.observer_length.value = len(raw)
            stop.wait(.1)
    class Alive:
        def is_alive(self):
            return True
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_timed_app(geo, shared, Alive()), log_level='error'))
    worker = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    updater = threading.Thread(target=update, daemon=True)
    worker.start(); updater.start()
    try:
        deadline = time.monotonic()+5
        while not server.started and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started
        with sync_api.sync_playwright() as p:
            browser = p.chromium.launch(executable_path=executable, headless=True,
                                       args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1500, 'height': 1000})
            errors, websockets, external = [], [], []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('websocket', lambda ws: websockets.append(ws.url))
            page.on('request', lambda req: external.append(req.url) if not req.url.startswith(f'http://127.0.0.1:{port}/') else None)
            page.goto(f'http://127.0.0.1:{port}/')
            page.wait_for_function('window.rawDisplayDiagnostics?.().frames >= 2')
            page.wait_for_function("document.getElementById('trigger-scope').textContent.includes('8/8 PPS')")
            assert page.locator('.plane').count() == 8
            page.select_option('#trigger-mode', 'only')
            for source, channel in [('beam', 0), ('light', 1), ('both', 0)]:
                page.select_option('#trigger-source', source)
                page.wait_for_timeout(100)
                pixels = page.evaluate('''channel => planes.map(p=>{
                    const t=p.tiles[0],at=t.offsets[channel];return Array.from(t.data.data.slice(at,at+3));
                })''', channel)
                assert all(pixel[0] > 200 and pixel[1] > 180 for pixel in pixels)
                if source != 'both':
                    dark = page.evaluate('''channel => planes.map(p=>{
                        const t=p.tiles[0],at=t.offsets[1-channel];return Array.from(t.data.data.slice(at,at+3));
                    })''', channel)
                    assert all(pixel == [4, 13, 20] for pixel in dark)
            page.click('#pause')
            assert page.evaluate('window.rawTriggerUI.state.paused')
            page.select_option('#trigger-source', 'beam')
            assert page.evaluate('window.rawTriggerUI.hitTime(0)') == 2090
            page.click('#pause')
            page.select_option('#trigger-mode', 'highlight')
            page.select_option('#trigger-source', 'both')
            page.wait_for_timeout(100)
            assert not errors and not external
            assert len(websockets) == 1  # source controls do not reconnect even HTTP/WS
            screenshot = os.environ.get('RAW_DISPLAY_BROWSER_SCREENSHOT')
            if screenshot:
                page.screenshot(path=screenshot, full_page=True)
            browser.close()
    finally:
        stop.set(); server.should_exit = True
        worker.join(5); updater.join(2); listener.close()
