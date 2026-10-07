"""Optional actual Canvas/clock UI test with synthetic in-memory transport only."""
import base64
import json
import os
from pathlib import Path
import re
import shutil
import struct
import pytest


@pytest.mark.skipif(os.environ.get('RAW_DISPLAY_CLOCK_BROWSER_TEST') != '1',
                    reason='optional offline Chromium clock integration')
def test_clock_with_actual_canvas_pause_and_reconnect():
    api = pytest.importorskip('playwright.sync_api')
    executable = os.environ.get('CHROMIUM_EXECUTABLE') or shutil.which('chromium')
    if not executable:
        pytest.skip('Chromium not installed')
    static = Path(__file__).resolve().parents[1]/'raw_display'/'static'
    n, unix, period = 16, 1791338400, 10_000_000
    rows = [dict(iog=i, module=(i-1)//2, tile=1, geometry_tile=1, start=(i-1)*2,
                 count=2, width=2, height=1, x_min=0, y_min=0, pitch=3.8) for i in range(1,9)]
    metadata = dict(mode='FIXTURE', time_basis='asic', n_pixels=n, record_bytes=8,
                    geometry_id='clock-fixture', iogs=list(range(1,9)), tiles=rows,
                    timing_config=dict(rollover_ticks=period, tick_seconds=1e-7))
    geometry = b''.join(struct.pack('<HHBBBB',j,0,11,j,0,0) for i in range(8) for j in range(2))
    frame = struct.pack('<4sII',b'RDP2',1,n)
    for i in range(1,9):
        frame += struct.pack('<ddd',39-i,41-i,0)
    for i in range(1,9):
        for j in range(2):
            frame += struct.pack('<Id',(i-1)*2+j,39-i-.1)
    data = dict(collector_healthy=True, server_unix_s=unix+.3,
        trigger_alignment=dict(session_id='fixture-one', capture_age_s=0,
            common_timing=dict(supported=True, stale_after_s=2.5, sources=[
                dict(iog=i,status='aligned',last_valid_age_s=0,last_sync_tick=(40-i)*period,
                     last_header_second=unix) for i in range(1,9)])),
        sources=[dict(iog=i,mapped_hits=2,bad_parity=0,unmapped_hits=0,malformed=0,
                     rx_age_s=0,timing=dict(synchronized=1,running=0)) for i in range(1,9)])
    fixture=dict(meta=metadata,status=data,geometry=base64.b64encode(geometry).decode(),
                 frame=base64.b64encode(frame).decode())
    html=re.sub(r'<script\b[^>]*>.*?</script>|<link\b[^>]*>','',
                (static/'index.html').read_text(),flags=re.S)
    harness='''const fixture=FIXTURE;
    const bytes=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0)).buffer;
    window.fixtureConnections=0;window.fixtureStop=false;window.fixtureRequests=[];
    window.fetch=async path=>{window.fixtureRequests.push(path);return {ok:true,
      json:async()=>path==='/api/geometry'?fixture.meta:fixture.status,
      arrayBuffer:async()=>bytes(fixture.geometry)};};
    window.WebSocket=class {
      constructor(){window.fixtureConnections++;this.closed=false;
        setTimeout(()=>{this.onopen?.();this.emit();},10);}
      emit(){if(!this.closed&&!window.fixtureStop)this.onmessage?.({data:bytes(fixture.frame)});}
      send(ack){if(ack!=='1')throw Error('changed ACK');setTimeout(()=>this.emit(),100);}
      close(){this.closed=true;this.onclose?.({code:1000});}
    };
    '''.replace('FIXTURE',json.dumps(fixture))
    with api.sync_playwright() as p:
        browser=p.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport=dict(width=1500,height=1000))
        errors,requests=[],[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('request',lambda r:requests.append(r.url))
        page.set_content(html)
        # Minimal fixture layout; the production clock CSS and JS are unchanged.
        page.add_style_tag(content='body{background:#07121a;color:#ddd;font:14px sans-serif}.modules{display:grid;grid-template-columns:1fr 1fr}.planes{display:flex}.plane{width:46%}canvas{width:100%;height:90px}header,.metrics,nav{display:flex;gap:20px;margin:12px}h1{font-size:24px}.label{display:block}')
        page.add_style_tag(content=(static/'playback_clock.css').read_text())
        page.add_script_tag(content=harness)
        for name in ('playback_clock.js','display.js'):
            page.add_script_tag(content=(static/name).read_text())
        page.wait_for_function("window.rawPlaybackClock.lastModel?.rows.length===8")
        assert page.locator('.plane').count()==8
        assert 'Oldest of 8/8' in page.locator('#playback-clock-detail').inner_text()
        assert 'BUFFERING' in page.locator('#playback-clock-detail').inner_text()
        assert 'CDT' in page.locator('#playback-clock-time').inner_text()
        assert page.evaluate('window.rawPlaybackClock.lastModel.min')==(unix-1)*1000
        page.select_option('#playback-clock-zone','UTC')
        page.wait_for_function("document.getElementById('playback-clock-time').textContent.includes('UTC')")
        page.click('#pause')
        page.wait_for_function("document.getElementById('playback-clock-time').textContent.startsWith('PAUSED')")
        frozen=page.locator('#playback-clock-time').inner_text()
        page.evaluate('fixture.status.trigger_alignment.common_timing.sources.forEach(s=>s.last_header_second+=10)')
        page.wait_for_timeout(1200)
        assert page.locator('#playback-clock-time').inner_text()==frozen
        assert 'at pause' in page.locator('#playback-clock-detail').inner_text()
        page.click('#pause')
        page.wait_for_function('window.rawPlaybackClock.lastModel.min===EXPECTED'.replace('EXPECTED',str((unix+9)*1000)))
        assert page.evaluate('window.fixtureConnections')==1
        page.evaluate('window.fixtureStop=true')
        page.wait_for_function("document.getElementById('playback-clock').dataset.state==='stale'")
        assert 'unavailable' in page.locator('#playback-clock-time').inner_text()
        # A reconnect must requalify its display clock from a new status response.
        page.evaluate('window.fixtureStop=false; socket.close()')
        page.wait_for_function('window.fixtureConnections===2')
        page.wait_for_function('window.rawPlaybackClock.lastModel?.rows.length===8')
        assert set(page.evaluate('window.fixtureRequests'))=={'/api/geometry','/api/geometry.bin','/api/status'}
        assert not errors and not requests
        screenshot=os.environ.get('RAW_DISPLAY_CLOCK_SCREENSHOT')
        if screenshot:
            page.locator('#playback-clock').screenshot(path=screenshot)
        browser.close()
