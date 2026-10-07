"""Opt-in offline Canvas fixture. Real JS, mocked transport; NOT a live browser test."""
import base64
import json
import os
from pathlib import Path
import re
import shutil
import numpy as np
import pytest
from raw_display.geometry import demo_geometry
from raw_display.timing import TIMING_FIELDS, TIMING_COL
from raw_display.timed_server import encode_timed_frame
from raw_display.observer_runtime import encode_trigger_frame


@pytest.mark.skipif(os.environ.get('RAW_DISPLAY_OFFLINE_BROWSER_TEST') != '1',
                    reason='optional offline Chromium renderer fixture')
def test_canvas_source_controls_with_in_memory_transport():
    api = pytest.importorskip('playwright.sync_api')
    executable = os.environ.get('CHROMIUM_EXECUTABLE') or shutil.which('chromium')
    if not executable:
        pytest.skip('Chromium not installed')
    geo = demo_geometry(iogs=range(1, 9), chips_per_tile=1)
    n = len(geo.pixels)
    normal, beam, light = (np.full(n, -1.) for _ in range(3))
    for iog in range(1, 9):
        a, b = geo.lut[iog, 1, 11, 0], geo.lut[iog, 1, 11, 1]
        normal[[a, b]] = [2.09, 2.095]
        beam[a], light[b] = 2.09, 2.095
    clocks = np.zeros((9, len(TIMING_FIELDS)))
    clocks[:, TIMING_COL['cursor_s']] = 2.1
    clocks[:, TIMING_COL['frontier_s']] = 3.1
    clocks[:, TIMING_COL['synchronized']] = 1
    old = np.full(n, -1.)
    encoded = lambda b: base64.b64encode(b).decode('ascii')
    fixture = dict(meta=dict(**geo.metadata, time_basis='asic', mode='FIXTURE',
        trigger_view_enabled=True, trigger_source_protocol=1,
        trigger_sources={'beam': 5, 'light': 6}, trigger_pre_us=0, trigger_post_us=190),
        geometry=encoded(geo.pixels.tobytes()), frames=[
            encoded(encode_trigger_frame(old, beam, 1, magic=b'RDB1')),
            encoded(encode_trigger_frame(old, light, 1, magic=b'RDL1')),
            encoded(encode_timed_frame(old, normal, clocks, 1))],
        status=dict(collector_healthy=True, trigger_alignment=dict(capture_age_s=0.,
            common_timing=dict(stale_after_s=2.5, hardware_phase_verified=False,
                sources=[dict(iog=i, status='aligned', last_valid_age_s=.1) for i in range(1,9)])),
            sources=[dict(iog=i, mapped_hits=2, bad_parity=0, unmapped_hits=0,
                malformed=0, rx_age_s=.1, timing=dict(synchronized=1, running=1)) for i in range(1,9)]))
    static = Path(__file__).resolve().parents[1]/'raw_display'/'static'
    html = re.sub(r'<script\b[^>]*>.*?</script>|<link\b[^>]*>', '',
                  (static/'index.html').read_text(), flags=re.S)
    harness = '''const f=FIXTURE;
    const bytes=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0)).buffer;
    window.fixtureConnections=0;
    window.fetch=async path=>({ok:true,
      json:async()=>path==='/api/geometry'?f.meta:f.status,
      arrayBuffer:async()=>bytes(f.geometry)});
    window.WebSocket=class {
      constructor(url){window.fixtureConnections++;this.closed=false;
        setTimeout(()=>{this.onopen?.();this.emit();},10);}
      emit(){if(!this.closed) for(const frame of f.frames)this.onmessage?.({data:bytes(frame)});}
      send(ack){if(ack!=='1')throw Error('wrong ACK');setTimeout(()=>this.emit(),100);}
      close(){this.closed=true;}
    };'''.replace('FIXTURE', json.dumps(fixture))
    with api.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=executable, headless=True,
                                   args=['--no-sandbox'])
        page = browser.new_page(viewport={'width':1500,'height':1000})
        errors, requests = [], []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('request', lambda r: requests.append(r.url))
        page.set_content(html)
        for css in ('style.css','trigger_view.css'):
            page.add_style_tag(content=(static/css).read_text())
        page.add_script_tag(content=harness)
        for js in ('trigger_view.js','display.js'):
            page.add_script_tag(content=(static/js).read_text())
        page.wait_for_function('window.rawDisplayDiagnostics?.().frames >= 2')
        page.wait_for_function("document.getElementById('trigger-scope').textContent.includes('8/8 PPS')")
        assert page.locator('.plane').count()==8
        page.select_option('#trigger-mode','only')
        for source, channel in [('beam',0),('light',1),('both',0)]:
            page.select_option('#trigger-source',source)
            page.wait_for_timeout(100)
            data = page.evaluate('''ch=>planes.map(p=>{
                const t=p.tiles[0],at=t.offsets[ch];return Array.from(t.data.data.slice(at,at+3));
            })''', channel)
            assert all(pixel[0]>200 and pixel[1]>180 for pixel in data)
            if source!='both':
                data=page.evaluate('''ch=>planes.map(p=>{
                    const t=p.tiles[0],at=t.offsets[1-ch];return Array.from(t.data.data.slice(at,at+3));
                })''',channel)
                assert all(pixel==[4,13,20] for pixel in data)
        page.click('#pause')
        page.select_option('#trigger-source','beam')
        assert page.evaluate('window.rawTriggerUI.state.paused')
        assert page.evaluate('window.rawTriggerUI.hitTime(0)')==2090
        page.click('#pause')
        page.select_option('#trigger-mode','highlight')
        page.select_option('#trigger-source','both')
        page.wait_for_timeout(100)
        assert not errors and not requests
        assert page.evaluate('window.fixtureConnections')==1
        screenshot=os.environ.get('RAW_DISPLAY_BROWSER_SCREENSHOT')
        if screenshot:
            page.screenshot(path=screenshot, full_page=True)
        browser.close()
