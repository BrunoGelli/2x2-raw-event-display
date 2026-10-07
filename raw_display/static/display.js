/* No external assets or framework. Packet decoding stays on daq03. */
'use strict';
const $ = id => document.getElementById(id);
let meta, pixels, seen, pausedSeen, pauseTime=0, paused=false, socket;
let lastFrameAt=0, status=null, oldStatus=null, oldStatusAt=0, retry=null;
const planes=[], modules=new Map();
// This indicator reports collector arrival, NOT delayed detector playback.
// It is driven by observed subtype-83 counters, never by an artificial 1 Hz timer.
let syncLinkUp=false, syncMetaAt=-Infinity;
const sync83State=Array.from({length:9},()=>({count:null,age:null,at:0,pulseUntil:0}));
function receiveSync83(packet,now=performance.now()){
  if(packet.type!=='sync83'||packet.version!==1||!Array.isArray(packet.sources)||packet.sources.length>8)
    throw new Error('Bad SYNC-83 metadata');
  const ids=new Set();
  for(const src of packet.sources){
    if(!Number.isInteger(src.iog)||src.iog<1||src.iog>8||ids.has(src.iog)||
       !Number.isSafeInteger(src.count)||src.count<0||
       (src.age_s!==null&&(!Number.isFinite(src.age_s)||src.age_s<0)))
      throw new Error('Bad SYNC-83 source');
    ids.add(src.iog);
  }
  window.rawTriggerUI?.receiveMeta(packet,now);
  syncMetaAt=now;
  for(const src of packet.sources){
    const old=sync83State[src.iog];
    const increment=old.count!==null&&src.count>old.count;
    const fresh=src.age_s!==null&&src.age_s<0.6;
    sync83State[src.iog]={count:src.count,age:src.age_s,at:now,
      pulseUntil:increment&&fresh?now+250:(src.count===old.count?old.pulseUntil:0)};
  }
}
function sync83View(iog,now=performance.now()){
  const s=sync83State[iog];
  if(!syncLinkUp||now-syncMetaAt>3500)return {kind:'unknown',label:'SYNC 83 —',age:null};
  if(s.age===null||s.count===null||s.count===0)return {kind:'waiting',label:'SYNC 83 WAIT',age:null};
  const age=s.age+Math.max(0,now-s.at)/1000;
  if(age>2.5)return {kind:'stale',label:'SYNC 83 STALE',age};
  return {kind:now<s.pulseUntil?'pulse':'live',label:'SYNC 83 RX',age};
}
function renderSync83(now){
  for(const iog of meta.iogs){
    const el=$(`sync83-${iog}`);if(!el)continue;
    const v=sync83View(iog,now),name=`sync83-indicator ${v.kind}`;
    if(el.className!==name)el.className=name;
    const label=el.querySelector('.sync83-label');
    if(label&&label.textContent!==v.label)label.textContent=v.label;
    el.title=`IOG ${iog}: ${sync83State[iog].count??0} SYNC subtype-83 packets received. `+
      (v.age===null?'No current arrival information.':`Last collector arrival ${v.age.toFixed(1)} s ago.`)+
      ' Heartbeat 72 ignored. RX is not a hardware/NTP lock and precedes buffered charge playback.';
  }
}

let pauseClocks=[];
const clocks=Array.from({length:9},()=>({base:-1,frontier:-1,running:false,at:0}));
const viewAudit={connections:0,closes:0,statusErrors:0,frames:0,maxFrameGapMs:0};
const asicTime=()=>meta?.time_basis==='asic';
function displayTime(iog,now=performance.now()){
  if(!asicTime())return now;
  const c=clocks[iog];
  if(c.base<0)return -1;
  // Only pace the reported detector clock. Never run far ahead during a stall.
  const advance=c.running?Math.min(Math.max(0,now-c.at),250,Math.max(0,c.frontier-c.base)*1000):0;
  return c.base*1000+advance;
}
window.rawDisplayDiagnostics=()=>({...viewAudit,timeBasis:meta?.time_basis||'host',
  clocks:clocks.slice(1).map((c,i)=>({iog:i+1,...c})),
  sync83:sync83State.slice(1).map((s,i)=>({iog:i+1,...s,...sync83View(i+1)})),
  collectorHealthy:status?.collector_healthy,paused});
const fmt = n => n.toLocaleString(undefined,{maximumFractionDigits:1});
const rateText = n => n >= 1000 ? `${(n/1000).toFixed(1)}k` : n.toFixed(0);
const palette = new Uint8Array(256*3);
for(let i=0;i<256;i++){
  const t=i/255;
  palette[3*i]=Math.round(4+93*t*t);
  palette[3*i+1]=Math.round(13+232*t);
  palette[3*i+2]=Math.round(20+170*t);
}

class Plane {
  constructor(iog, holder) {
    this.iog=iog; this.zoom=1; this.panX=0; this.panY=0;
    const box=document.createElement('div');box.className='plane';
    box.innerHTML=`<h3>IOG ${iog}<span id="rate-${iog}">waiting</span></h3><div class="sync83-row"><span id="sync83-${iog}" class="sync83-indicator waiting"><i aria-hidden="true"></i><span class="sync83-label">SYNC 83 WAIT</span></span></div><canvas aria-label="IO group ${iog} raw activity"></canvas>`;
    holder.appendChild(box);this.box=box;this.canvas=box.querySelector('canvas');
    this.ctx=this.canvas.getContext('2d',{alpha:false});
    this.tiles=meta.tiles.filter(t=>t.iog===iog).map(t=>{
      const image=document.createElement('canvas');image.width=t.width;image.height=t.height;
      const ctx=image.getContext('2d',{alpha:false}),data=ctx.createImageData(t.width,t.height);
      for(let p=0;p<data.data.length;p+=4){data.data[p]=4;data.data[p+1]=13;data.data[p+2]=20;data.data[p+3]=255;}
      const lookup=new Int32Array(t.width*t.height);lookup.fill(-1);
      const offsets=new Uint32Array(t.count);
      for(let id=t.start;id<t.start+t.count;id++){
        const [col,row]=pixel(id);const at=(t.height-1-row)*t.width+col;lookup[at]=id;offsets[id-t.start]=at*4;
      }
      return {...t,image,ctx,data,lookup,offsets};
    });
    this.xMin=Math.min(...this.tiles.map(t=>t.x_min-t.pitch/2));
    this.xMax=Math.max(...this.tiles.map(t=>t.x_min+(t.width-.5)*t.pitch));
    this.yMin=Math.min(...this.tiles.map(t=>t.y_min-t.pitch/2));
    this.yMax=Math.max(...this.tiles.map(t=>t.y_min+(t.height-.5)*t.pitch));
    this.canvas.addEventListener('wheel',e=>{e.preventDefault();this.zoom=Math.max(1,Math.min(12,this.zoom*Math.exp(-e.deltaY*.001)));},{passive:false});
    let drag=null;
    this.canvas.addEventListener('pointerdown',e=>{drag=[e.clientX,e.clientY];this.canvas.setPointerCapture(e.pointerId);});
    this.canvas.addEventListener('pointerup',()=>{drag=null;});
    this.canvas.addEventListener('pointercancel',()=>{drag=null;});
    this.canvas.addEventListener('pointermove',e=>{
      if(drag){const r=this.canvas.width/this.canvas.getBoundingClientRect().width;this.panX+=(e.clientX-drag[0])*r;this.panY+=(e.clientY-drag[1])*r;drag=[e.clientX,e.clientY];}
      this.hover(e);
    });
    this.canvas.addEventListener('pointerleave',()=>{$('tooltip').hidden=true;});
    this.canvas.addEventListener('dblclick',()=>this.reset());
  }
  reset(){this.zoom=1;this.panX=this.panY=0;}
  transform(){
    const c=this.canvas,r=c.getBoundingClientRect(),dpr=Math.min(window.devicePixelRatio||1,1.5);
    const w=Math.max(1,Math.round(r.width*dpr)),h=Math.max(1,Math.round(r.height*dpr));
    if(c.width!==w||c.height!==h){c.width=w;c.height=h;}
    const s=Math.min((w-16)/(this.xMax-this.xMin),(h-18)/(this.yMax-this.yMin))*this.zoom;
    return {s,ox:w/2+this.panX-s*(this.xMin+this.xMax)/2,oy:h/2+this.panY+s*(this.yMin+this.yMax)/2};
  }
  draw(now, ages, brightness){
    if(this.canvas.getBoundingClientRect().width===0)return;
    const {s,ox,oy}=this.transform(),ctx=this.ctx;
    ctx.fillStyle='#07121a';ctx.fillRect(0,0,this.canvas.width,this.canvas.height);ctx.imageSmoothingEnabled=false;
    for(const t of this.tiles){
      const rgba=t.data.data;
      for(let id=t.start;id<t.start+t.count;id++){
        const last=ages[id];
        const ai=(asicTime()?last<0:last===0) ? brightness.length-1 : Math.max(0,Math.min(brightness.length-1,Math.floor((now-last)/16)));
        const color=brightness[ai]*3,at=t.offsets[id-t.start];
        rgba[at]=palette[color];rgba[at+1]=palette[color+1];rgba[at+2]=palette[color+2];
        if(window.rawTriggerUI?.state.mode!=='all')window.rawTriggerUI?.paint(rgba,at,id,now,brightness,brightness[ai]);
      }
      t.ctx.putImageData(t.data,0,0);
      const x=ox+s*(t.x_min-t.pitch/2),y=oy-s*(t.y_min+(t.height-.5)*t.pitch),w=t.width*t.pitch*s,h=t.height*t.pitch*s;
      ctx.drawImage(t.image,x,y,w,h);ctx.strokeStyle='#284352';ctx.lineWidth=.8;ctx.strokeRect(x,y,w,h);
      if(w>28){ctx.fillStyle='#a4b9c8';ctx.font='9px system-ui';ctx.fillText(`T${t.tile}`,x+3,y+11);}
    }
  }
  hover(e){
    const r=this.canvas.getBoundingClientRect(),{s,ox,oy}=this.transform();
    const x=((e.clientX-r.left)*this.canvas.width/r.width-ox)/s;
    const y=(oy-(e.clientY-r.top)*this.canvas.height/r.height)/s;
    for(const t of this.tiles){
      const col=Math.round((x-t.x_min)/t.pitch),row=Math.round((y-t.y_min)/t.pitch);
      if(col<0||row<0||col>=t.width||row>=t.height)continue;
      const id=t.lookup[(t.height-1-row)*t.width+col];if(id<0)continue;
      const p=pixel(id),ages=paused?pausedSeen:seen,now=paused?(asicTime()?pauseClocks[this.iog]:pauseTime):displayTime(this.iog);
      const age=(asicTime()?ages[id]>=0:Boolean(ages[id])) ? `${Math.max(0,(now-ages[id])/1000).toFixed(2)} s ago` : 'not observed';
      $('tooltip').textContent=`IOG ${t.iog}  ·  Tile ${t.tile}  (geometry ${t.geometry_tile})\nChip ${p[2]}  ·  Channel ${p[3]}  ·  Pixel ${id}\nX ${(t.x_min+col*t.pitch).toFixed(2)} mm  /  Y ${(t.y_min+row*t.pitch).toFixed(2)} mm\n${asicTime()?"ASIC age at playback":"Last arrival"}: ${age}`;
      $('tooltip').textContent+=window.rawTriggerUI?.hover(id,now)??'';
      $('tooltip').hidden=false;$('tooltip').style.left=Math.min(e.clientX+14,window.innerWidth-320)+'px';$('tooltip').style.top=Math.min(e.clientY+14,window.innerHeight-125)+'px';return;
    }
    $('tooltip').hidden=true;
  }
}
function pixel(id){const at=id*8;return [pixels.getUint16(at,true),pixels.getUint16(at+2,true),pixels.getUint8(at+4),pixels.getUint8(at+5)];}
function connect(){
  const scheme=location.protocol==='https:'?'wss:':'ws:';
  socket=new WebSocket(`${scheme}//${location.host}/ws?geometry=${meta.geometry_id}&sync83=1&trigger_windows=1&trigger_sources=1`);socket.binaryType='arraybuffer';
  socket.onopen=()=>{window.rawTriggerUI?.reset();syncLinkUp=true;syncMetaAt=-Infinity;for(let i=1;i<=8;i++)sync83State[i]={count:null,age:null,at:0,pulseUntil:0};viewAudit.connections++;seen.fill(asicTime()?-1:0);lastFrameAt=performance.now();$('connection').textContent='Stream connected';};
  socket.onmessage=event=>{
    try{
      if(typeof event.data==='string'){receiveSync83(JSON.parse(event.data));return;}
      if(window.rawTriggerUI?.receiveFrame(event.data))return;
      const d=new DataView(event.data);
      const now=performance.now();
      if(d.byteLength<12)throw new Error('Short display frame');
      const magic=d.getUint32(0,false),seq=d.getUint32(4,true),count=d.getUint32(8,true);
      if(magic===0x52445032){
        if(!asicTime()||d.byteLength!==204+count*12)throw new Error('ASIC frame/version mismatch; reload page');
        for(let iog=1;iog<=8;iog++){
          const o=12+(iog-1)*24,base=d.getFloat64(o,true),frontier=d.getFloat64(o+8,true),run=d.getFloat64(o+16,true);
          if(!Number.isFinite(base)||!Number.isFinite(frontier))throw new Error('Bad detector clock');
          clocks[iog]={base,frontier,running:run===1,at:now};
        }
        for(let j=0;j<count;j++){
          const o=204+j*12,id=d.getUint32(o,true),t=d.getFloat64(o+4,true);
          if(id>=seen.length||!Number.isFinite(t)||t< -1)throw new Error('Bad ASIC hit time');
          seen[id]=t<0?-1:t*1000;
        }
      }else if(magic===0x52445031){
        if(asicTime()||d.byteLength!==20+count*8)throw new Error('Host frame/version mismatch; reload page');
        for(let j=0;j<count;j++){
          const off=20+j*8,id=d.getUint32(off,true),age=d.getFloat32(off+4,true);
          if(id>=seen.length||!Number.isFinite(age)||age<0)throw new Error('Bad pixel update');
          seen[id]=age>1e8?0:now-age*1000;
          if(seen[id]===0&&age<=1e8)seen[id]=-.001;
        }
      }else throw new Error('Unsupported display frame');
      if(viewAudit.frames)viewAudit.maxFrameGapMs=Math.max(viewAudit.maxFrameGapMs,now-lastFrameAt);
      viewAudit.frames++;
      lastFrameAt=now;socket.send(String(seq));
    }catch(err){$('notice').textContent=err.message;socket.close();}
  };
  socket.onclose=e=>{window.rawTriggerUI?.disconnect();syncLinkUp=false;viewAudit.closes++;if(e.code===4009){location.reload();return;}$('connection').textContent='Disconnected · retrying';if(!asicTime())seen.fill(0);clearTimeout(retry);retry=setTimeout(connect,1500);};
  socket.onerror=()=>socket.close();
}
async function updateStatus(){
  try{
    const res=await fetch('/api/status',{cache:'no-store'});if(!res.ok)throw new Error('Status unavailable');
    status=await res.json();const now=performance.now(),dt=(now-oldStatusAt)/1000;
    window.rawTriggerUI?.receiveStatus(status,now);
    let total=0,receiving=0,bad=0,unknown=0,malformed=0;
    for(const s of status.sources){
      const before=oldStatus?.sources.find(p=>p.iog===s.iog);
      const rate=before&&dt>0?Math.max(0,s.mapped_hits-before.mapped_hits)/dt:0;
      total+=rate;if(s.rx_age_s!==null&&s.rx_age_s<3)receiving++;
      bad+=s.bad_parity;unknown+=s.unmapped_hits;malformed+=s.malformed;
      $(`rate-${s.iog}`).textContent=s.rx_age_s===null?'waiting':s.rx_age_s>3?'silent':`${rateText(rate)} Hz`;
    }
    $('rate').textContent=fmt(total);$('sources').textContent=`${receiving} / ${meta.iogs.length}`;
    $('errors').textContent=`Since collector start: ${fmt(bad)} bad-parity hits excluded · ${fmt(unknown)} unmapped hits · ${fmt(malformed)} malformed messages.`;
    let notice=meta.mode==='DEMO'?'DEMO — synthetic geometry and activity. No PACMAN connections.':(asicTime()?'ASIC TIME — buffered playback from unrolled chip timestamps · IOG-local playback; trigger epoch qualification is shown separately.':'HOST TIME — arrival-time diagnostic view · no drift reconstruction.');
    if(asicTime()){
      const wait=status.sources.filter(s=>!s.timing?.synchronized).map(s=>s.iog);
      const buffering=status.sources.filter(s=>s.timing?.synchronized&&!s.timing?.running).map(s=>s.iog);
      const dropped=status.sources.reduce((n,s)=>n+(s.timing?.buffer_dropped_hits||0),0);
      const invalid=status.sources.reduce((n,s)=>n+(s.timing?.invalid_times||0)+(s.timing?.invalid_syncs||0),0);
      if(wait.length)notice+=` WAITING FOR PPS: IOG ${wait.join(', ')}.`;
      if(buffering.length)notice+=` BUFFERING/PAUSED: IOG ${buffering.join(', ')}.`;
      if(dropped||invalid)notice+=` TIMING WARNING: ${fmt(dropped)} buffer drops; ${fmt(invalid)} invalid timings/SYNCs.`;
    }
    if(meta.mode!=='DEMO'){
      const cut=meta.timing_config?.min_raw_timestamp??meta.min_raw_timestamp??0;
      const excluded=status.sources.reduce((n,s)=>n+(s.post_sync_filtered_hits||0),0);
      notice+=cut>0?` Display cut: raw ASIC timestamp < ${cut} ticks (${fmt(excluded)} mapped hits excluded).`:' Post-SYNC display cut OFF.';
    }
    if(!status.collector_healthy)notice+='  COLLECTOR NOT HEALTHY — activity may be stale.';
    if(malformed>0)notice+='  Check wire format: malformed messages were rejected.';
    if(unknown>0)notice+='  Some accepted hits are absent from the selected geometry.';
    $('notice').textContent=notice;$('notice').className=(!status.collector_healthy||malformed||unknown)?'warning':'';
    oldStatus=status;oldStatusAt=now;
  }catch(err){viewAudit.statusErrors++;status=null;$('notice').textContent='Status unavailable — do not interpret the view as live.';$('notice').className='warning';}
}
async function main(){
  const response=await fetch('/api/geometry');if(!response.ok)throw new Error('Geometry metadata unavailable');
  meta=await response.json();const b=await (await fetch('/api/geometry.bin')).arrayBuffer();
  if(b.byteLength!==meta.n_pixels*8||meta.record_bytes!==8)throw new Error('Geometry version/length mismatch');
  pixels=new DataView(b);seen=new Float64Array(meta.n_pixels);if(asicTime())seen.fill(-1);
  window.rawTriggerUI?.configure(meta);
  $('mode').textContent=meta.mode;$('mode').classList.toggle('demo',meta.mode==='DEMO');$('pixels').textContent=fmt(meta.n_pixels);
  for(const m of [...new Set(meta.tiles.map(t=>t.module))]){
    const box=document.createElement('section');box.className='module';
    box.innerHTML=`<h2>Module ${m}<span>ANODE ACTIVITY</span></h2><div class="planes"></div><div class="tilehint">Physical tile positions · independent IO groups</div>`;
    $('modules').appendChild(box);modules.set(m,box);
    const opt=document.createElement('option');opt.value=String(m);opt.textContent=`Module ${m}`;$('module').appendChild(opt);
    for(const iog of meta.iogs.filter(i=>Math.floor((i-1)/2)===m))planes.push(new Plane(iog,box.querySelector('.planes')));
  }
  $('module').onchange=()=>{for(const [m,box]of modules)box.hidden=$('module').value!=='all'&&String(m)!==$('module').value;$('modules').classList.toggle('focus',$('module').value!=='all');};
  $('pause').onclick=()=>{paused=!paused;window.rawTriggerUI?.setPaused(paused);if(paused){pausedSeen=seen.slice();pauseTime=performance.now();pauseClocks=clocks.map((c,i)=>displayTime(i,pauseTime));}$('pause').textContent=paused?'Resume live':'Pause view';};
  $('reset').onclick=()=>planes.forEach(p=>p.reset());
  $('full').onclick=()=>{if(document.fullscreenElement)document.exitFullscreen();else document.documentElement.requestFullscreen().catch(()=>{});};
  $('decay').oninput=()=>{$('decayLabel').textContent=`${Number($('decay').value).toFixed(2)} s`;};
  connect();await updateStatus();setInterval(updateStatus,1000);
  let lastDraw=0,nframes=0,fpsStart=performance.now(),lastTau=-1,brightness;
  function render(now){
    requestAnimationFrame(render);
    if(now-lastDraw<1000/Number($('targetFps').value)-1)return;
    renderSync83(now);
    window.rawTriggerUI?.renderBadges(meta,now);
    lastDraw=now;const tau=Number($('decay').value);
    if(tau!==lastTau){brightness=new Uint8Array(Math.ceil(tau*1000*8/16)+1);for(let i=0;i<brightness.length-1;i++)brightness[i]=Math.round(255*Math.exp(-i*16/(tau*1000)));lastTau=tau;}
    const fresh=lastFrameAt>0&&now-lastFrameAt<3500&&status?.collector_healthy;
    $('modules').classList.toggle('offline',!fresh);
    $('connection').textContent=fresh?(paused?'View paused · ingest continues':'Receiving snapshots'):'Stream stale / waiting';
    for(const plane of planes){const t=paused?(asicTime()?pauseClocks[plane.iog]:pauseTime):displayTime(plane.iog,now);plane.draw(t,paused?pausedSeen:seen,brightness);}
    nframes++;if(now-fpsStart>1000){$('fps').textContent=(nframes*1000/(now-fpsStart)).toFixed(0);nframes=0;fpsStart=now;}
  }
  requestAnimationFrame(render);
}
main().catch(err=>{$('notice').textContent=`Startup failed: ${err.message}`;$('notice').className='warning';console.error(err);});
