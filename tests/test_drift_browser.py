"""Real WebGL renderer + actual backend track fixture; synthetic transport only."""
import base64
import json
import os
from pathlib import Path
import shutil
import sys
import numpy as np
import pytest
from fastapi.testclient import TestClient
from raw_display.drift_state import encode_drift_frame
from raw_display.observer_runtime import snapshot_with_triggers, encode_trigger_frame
from raw_display.runtime import FIELDS
from raw_display.timing import TIMING_FIELDS
from raw_display.timed_server import create_timed_app, encode_timed_frame
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from synthetic_3d import build_fixture


@pytest.mark.skipif(os.environ.get('RAW_DISPLAY_3D_BROWSER_TEST')!='1',reason='optional WebGL synthetic-track fixture')
def test_actual_webgl_track_switch_pause_source_and_failure():
    api=pytest.importorskip('playwright.sync_api')
    executable=os.environ.get('CHROMIUM_EXECUTABLE') or shutil.which('chromium')
    if not executable:pytest.skip('Chromium not installed')
    root=Path(__file__).resolve().parents[1]
    if not (root/'layout/geometry_mod0_v4.json').exists():pytest.skip('fetch pinned geometry first')
    geo,g,shared,config,expected=build_fixture(root/'layout')
    class Alive:
        def is_alive(self):return True
    with TestClient(create_timed_app(geo,shared,Alive(),config,geometry3d=g,mode='SYNTHETIC')) as client:
        meta=client.get('/api/geometry').json();status=client.get('/api/status').json()
    normal,_,_,clocks,_,_,sources,delta=snapshot_with_triggers(shared,FIELDS,TIMING_FIELDS,True,True)
    old=np.full(len(normal),-1.)
    current={name:(sources[name],delta[name]) for name in sources}
    empty={name:(old,np.full(len(normal),-1,dtype=np.int64)) for name in sources}
    encode=lambda raw:base64.b64encode(raw).decode()
    frames=dict(normal=encode(encode_timed_frame(old,normal,clocks,1)),
        beam=encode(encode_trigger_frame(old,sources['beam'],1,b'RDB1')),
        light=encode(encode_trigger_frame(old,sources['light'],1,b'RDL1')),
        drift=encode(encode_drift_frame(empty,current,1)))
    selected=expected[0];pid=selected['id']
    revised={name:(a.copy(),b.copy()) for name,(a,b) in current.items()}
    revised['light'][1][pid]+=100
    frames['revised']=encode(encode_drift_frame(current,revised,1))
    harness='''const f=FIXTURE;const bytes=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0)).buffer;
      window.fixtureConnections=0;window.fixtureWant3D=false;window.fixtureRevised=false;
      window.WebSocket=class {
        constructor(){window.fixtureConnections++;window.fixtureSocket=this;setTimeout(()=>{this.onopen?.();this.emit();},10);}
        emit(){if(this.closed)return;for(const key of ['beam','light',...(window.fixtureWant3D?['drift',...(window.fixtureRevised?['revised']:[])]:[]),'normal'])
          this.onmessage?.({data:bytes(f[key])});}
        send(ack){window.fixtureWant3D=ack.startsWith('{')?JSON.parse(ack).view3d:false;setTimeout(()=>this.emit(),100);}
        close(){this.closed=true;}
      };'''.replace('FIXTURE',json.dumps(frames))
    with api.sync_playwright() as p:
        browser=p.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader'])
        page=browser.new_page(viewport={'width':1500,'height':1150},device_scale_factor=1)
        errors=[];requests=[];console_errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('console',lambda m:console_errors.append(m.text) if m.type=='error' else None)
        page.on('request',lambda r:requests.append(r.url))
        def route(r):
            name=r.request.url.split('http://raw-display.test',1)[-1].split('?',1)[0]
            if name=='/api/geometry':r.fulfill(json=meta)
            elif name=='/api/status':r.fulfill(json=status)
            elif name=='/api/geometry.bin':r.fulfill(body=geo.pixels.tobytes(),content_type='application/octet-stream')
            elif name=='/api/geometry3d.bin':r.fulfill(body=g.pixels.tobytes(),content_type='application/octet-stream')
            elif name=='/':r.fulfill(path=str(root/'raw_display/static/index.html'),content_type='text/html')
            elif name.startswith('/static/'):
                path=root/'raw_display'/name.lstrip('/')
                r.fulfill(path=str(path),content_type='text/javascript' if path.suffix=='.js' else 'text/css')
            else:r.abort()
        page.route('**/*',route);page.add_init_script(harness);page.goto('http://raw-display.test/')
        page.wait_for_function('window.rawDisplayDiagnostics?.().frames>=2')
        page.wait_for_timeout(1200)
        metrics={'2d':page.evaluate('({fps:document.getElementById("fps").textContent,...window.rawDisplayDiagnostics().view3d})')}
        assert not any('three.module' in r or 'geometry3d.bin' in r for r in requests)
        page.select_option('#module','3d');page.select_option('#trigger-mode','only');page.select_option('#trigger-source','light')
        page.wait_for_function('window.rawDisplay3D?.()?.diagnostics.draws>2',timeout=30000)
        page.wait_for_function('window.rawDriftData.state.frames>1')
        page.wait_for_timeout(1200)
        metrics['3d']=page.evaluate('({fps:document.getElementById("fps").textContent,...window.rawDisplayDiagnostics().view3d})')
        assert page.locator('#modules').is_hidden()
        assert page.locator('#detector3d').is_visible()
        ids=[e['id'] for e in expected]
        actual=np.array(page.evaluate("ids=>ids.map(id=>window.rawDisplay3D().position('light',id))",ids))
        target=np.array([[e[k] for k in ('x_mm','y_mm','z_mm')] for e in expected])
        assert np.max(np.abs(actual-target))<.081
        assert page.evaluate('window.rawDisplay3D().visibility()')==dict(normal=False,beam=False,light=True)
        page.select_option('#trigger-source','both')
        page.wait_for_function('window.rawDisplay3D().visibility().beam')
        assert page.evaluate('window.rawDisplay3D().visibility()')==dict(normal=False,beam=True,light=True)
        page.select_option('#trigger-mode','highlight')
        page.wait_for_function('window.rawDisplay3D().visibility().normal')
        assert page.evaluate('window.rawDisplay3D().visibility().normal')
        page.click('#pause')
        page.wait_for_function('document.getElementById("playback-clock-time").textContent.startsWith("PAUSED")')
        before=page.evaluate("id=>window.rawDisplay3D().position('light',id)",pid)
        label=page.locator('#playback-clock-time').text_content()
        page.evaluate('window.fixtureRevised=true')
        page.wait_for_timeout(300)
        assert page.evaluate("id=>window.rawDisplay3D().position('light',id)",pid)==before
        assert page.locator('#playback-clock-time').text_content()==label
        # Orbit while paused changes only the camera.
        box=page.locator('#detector3d-canvas').bounding_box()
        page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2)
        page.mouse.down();page.mouse.move(box['x']+box['width']/2+100,box['y']+box['height']/2+30,steps=6);page.mouse.up()
        page.click('#reset');page.click('#pause');page.wait_for_function('([id,before])=>JSON.stringify(window.rawDisplay3D().position("light",id))!==JSON.stringify(before)',arg=[pid,before])
        assert page.evaluate("id=>window.rawDisplay3D().position('light',id)",pid)!=before
        screenshot=os.environ.get('RAW_DISPLAY_3D_SCREENSHOT')
        if screenshot:page.screenshot(path=screenshot,full_page=True)
        page.select_option('#module','all');page.wait_for_timeout(250)
        count=page.evaluate('window.rawDriftData.state.frames');page.wait_for_timeout(250)
        assert page.evaluate('window.rawDriftData.state.frames')==count
        assert page.locator('#modules').is_visible()
        page.select_option('#module','3d');page.wait_for_timeout(250)
        assert requests.count('http://raw-display.test/api/geometry3d.bin')==1
        assert page.evaluate('window.fixtureConnections')==1
        assert not errors and not console_errors
        assert all(r.startswith('http://raw-display.test/') for r in requests)
        # Optional corrupt frames disable 3D without disconnecting normal 2D.
        page.evaluate("()=>{const b=new ArrayBuffer(12),d=new DataView(b);d.setUint32(0,0x52334431,false);d.setUint32(8,3,true);fixtureSocket.onmessage({data:b});}")
        page.wait_for_timeout(150)
        assert page.locator('#modules').is_visible()
        assert page.evaluate('window.fixtureConnections')==1
        metrics['fixture_connections']=page.evaluate('window.fixtureConnections')
        metrics['geometry_fetches']=requests.count('http://raw-display.test/api/geometry3d.bin')
        metrics['track_points']=len(expected)
        metrics['maximum_position_error_mm']=float(np.max(np.abs(actual-target)))
        if os.environ.get('RAW_DISPLAY_3D_METRICS'):
            Path(os.environ['RAW_DISPLAY_3D_METRICS']).write_text(json.dumps(metrics,indent=2)+'\n')
        browser.close()
