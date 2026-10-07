/* Detector-wide ASIC-time window layers. Receipt badges are NOT event-time gates. */
'use strict';
(function(root){
  const state={enabled:false,mode:'all',tagged:new Float64Array(0),frozen:null,
    paused:false,link:false,at:-Infinity,source:'both',sourceProtocol:false,
    layers:{},frozenLayers:null,routing:{},meta:null,statusAt:-Infinity,alignment:null,healthy:false,
    sources:Array.from({length:9},()=>({count:null,age:null,at:0,pulse:0}))};
  const element=id=>typeof document!=='undefined'?document.getElementById(id):null;
  function reset(){
    state.tagged.fill(-1);for(const a of Object.values(state.layers))a.fill(-1);
    state.link=true;state.at=-Infinity;state.statusAt=-Infinity;
    state.sources=Array.from({length:9},()=>({count:null,age:null,at:0,pulse:0}));
  }
  function configure(meta){
    state.enabled=meta.time_basis==='asic'&&meta.trigger_view_enabled===true;
    state.tagged=new Float64Array(meta.n_pixels);state.tagged.fill(-1);
    state.meta=meta;state.routing=meta.trigger_sources??{};
    state.sourceProtocol=meta.trigger_source_protocol===1;state.source='both';
    state.layers={beam:new Float64Array(meta.n_pixels),light:new Float64Array(meta.n_pixels)};
    for(const a of Object.values(state.layers))a.fill(-1);
    const nav=typeof document!=='undefined'?document.querySelector('nav'):null;
    if(nav&&!element('trigger-mode')){
      const label=document.createElement('label');label.textContent='Trigger view ';
      const select=document.createElement('select');select.id='trigger-mode';
      for(const [value,text]of [['all','All hits'],['highlight','Highlight windows'],['only','Window hits only']]){
        const option=document.createElement('option');option.value=value;option.textContent=text;select.appendChild(option);
      }
      select.onchange=()=>{state.mode=state.enabled?select.value:'all';};
      label.appendChild(select);nav.appendChild(label);
      const note=document.createElement('div');note.id='trigger-scope';note.className='trigger-scope';nav.after(note);
    }
    const select=element('trigger-mode');if(select)select.disabled=!state.enabled;
    if(nav&&!element('trigger-source')){
      const label=document.createElement('label');label.textContent='Source ';
      const source=document.createElement('select');source.id='trigger-source';
      for(const [value,text] of [['both','Beam + Light'],['beam','Beam'],['light','Light']]){
        const option=document.createElement('option');option.value=value;option.textContent=text;source.appendChild(option);
      }
      source.onchange=()=>{state.source=source.value;}; // browser-local: no subscription or queue changes
      label.appendChild(source);nav.appendChild(label);
    }
    const source=element('trigger-source');if(source)source.disabled=!state.enabled||!state.sourceProtocol;
    renderAlignment(0);
    state.mode='all';
  }
  function receiveMeta(packet,now){
    state.at=now;
    for(const source of packet.sources){
      if(!Number.isInteger(source.iog)||source.iog<1||source.iog>8)throw Error('Bad trigger IOG');
      const count=source.trigger_count,age=source.trigger_age_s;
      if(count===undefined)continue; // backward-compatible sync-only server
      if(!Number.isSafeInteger(count)||count<0||(age!==null&&(!Number.isFinite(age)||age<0)))throw Error('Bad trigger telemetry');
      const old=state.sources[source.iog];
      const increment=old.count!==null&&count>old.count;
      state.sources[source.iog]={count,age,at:now,
        pulse:increment&&age!==null&&age<.6?now+250:(count===old.count?old.pulse:0)};
    }
  }
  function receiveFrame(buffer){
    const d=new DataView(buffer);
    if(d.byteLength<4)return false;
    const magic=d.getUint32(0,false),source={0x52444231:'beam',0x52444c31:'light'}[magic];
    if(magic!==0x52445431&&!source)return false; // RDT1 / RDB1 / RDL1
    if(source&&!state.sourceProtocol)throw Error('Unexpected source-specific frame');
    const layer=source?state.layers[source]:state.tagged;
    if(!state.enabled||d.byteLength<12)throw Error('Unexpected trigger-window frame');
    const n=d.getUint32(8,true);
    if(d.byteLength!==12+12*n)throw Error('Bad trigger-window frame length');
    for(let j=0;j<n;j++){
      const at=12+j*12,id=d.getUint32(at,true),t=d.getFloat64(at+4,true);
      if(id>=state.tagged.length||!Number.isFinite(t)||t< -1)throw Error('Bad trigger-window record');
      layer[id]=t<0?-1:t*1000;
    }
    return true; // no ACK here: canonical RDP2 follows and owns the ACK
  }
  function badge(iog,now){
    const s=state.sources[iog];
    const name=Object.keys(state.routing).find(k=>state.routing[k]===iog)?.toUpperCase()??'TRG';
    if(!state.link||now-state.at>3500)return {kind:'unknown',label:`${name} —`,count:s.count??0,age:null};
    if(s.count===null||s.age===null||s.count===0)return {kind:'idle',label:`${name} WAIT`,count:s.count??0,age:null};
    return {kind:now<s.pulse?'pulse':'idle',label:`${name} RX`,count:s.count,age:s.age+Math.max(0,now-s.at)/1000};
  }
  function renderBadges(meta,now){
    renderAlignment(now);
    for(const iog of meta.iogs){
      let el=element(`trigger-${iog}`);
      if(!el){
        const sync=element(`sync83-${iog}`);if(!sync)continue;
        el=document.createElement('span');el.id=`trigger-${iog}`;
        sync.parentElement.appendChild(el);
      }
      const b=badge(iog,now);el.className=`trigger-indicator ${b.kind}`;
      el.textContent=`● ${b.label}`;
      el.title=`IOG ${iog}: ${b.count} PACMAN T words received. `+
        (b.age===null?'No recent trigger receipt information.':`Last received ${b.age.toFixed(2)} s ago.`)+
        ' Physical receipt badge, not an event-time gate or a clock-lock claim. Eligible windows propagate to all qualified IO groups. SYNC S words never trigger it.';
    }
  }
  function setPaused(paused){
    state.paused=paused;state.frozen=paused?state.tagged.slice():null;
    state.frozenLayers=paused?{beam:state.layers.beam.slice(),light:state.layers.light.slice()}:null;
  }
  function hitTime(id){
    if(!state.sourceProtocol)return (state.paused?state.frozen:state.tagged)[id];
    const layers=state.paused?state.frozenLayers:state.layers;
    return state.source==='both'?Math.max(layers.beam[id],layers.light[id]):layers[state.source][id];
  }
  function receiveStatus(packet,now){
    state.statusAt=now;state.healthy=packet.collector_healthy===true;
    state.alignment=packet.trigger_alignment??null;
  }
  function alignmentView(now){
    const a=state.alignment,common=a?.common_timing;
    const capture=a?.capture_age_s;
    if(!state.link||!state.healthy||now-state.statusAt>3500||!common||
       !Number.isFinite(capture)||capture+(now-state.statusAt)/1000>3.5)return {known:false,qualified:[]};
    const elapsed=capture+Math.max(0,now-state.statusAt)/1000;
    const qualified=common.sources.filter(s=>s.status==='aligned'&&Number.isFinite(s.last_valid_age_s)&&
      s.last_valid_age_s+elapsed<=common.stale_after_s).map(s=>s.iog);
    return {known:true,qualified};
  }
  function renderAlignment(now){
    const note=element('trigger-scope');if(!note||!state.meta)return;
    const meta=state.meta,v=alignmentView(now),iogs=meta.iogs??[];
    let text;
    if(!state.enabled){
      text='Receipt badges are read-only. Enable RAW_DISPLAY_TRIGGER_VIEW=1 for detector-wide candidate windows.';
    }else if(!state.sourceProtocol){
      text='Legacy same-IOG trigger server; reload after upgrading the server for detector-wide Beam/Light selection.';
    }else{
      const missing=iogs.filter(i=>!v.qualified.includes(i));
      const timing=v.known?`${iogs.length-missing.length}/${iogs.length} PPS epochs qualified`:'PPS qualification unknown / waiting';
      text=`Detector-wide windows: ${meta.trigger_pre_us??0} µs before to ${meta.trigger_post_us??300} µs after t0. `+
        `Beam = IOG ${state.routing.beam}; Light = IOG ${state.routing.light}. ${timing}. `+
        (v.known&&missing.length?`Unqualified IOGs: ${missing.join(', ')}. `:'')+
        'Yellow = temporal candidate, not a complete event. Header labels are provisional; hardware phase is not verified.';
    }
    if(note.textContent!==text)note.textContent=text;
  }
  function paint(rgba,at,id,now,brightness,normalBrightness){
    if(!state.enabled||state.mode==='all')return;
    const t=hitTime(id);
    // Future timestamps never become visible before the detector playhead.
    const index=t<0||t>now?brightness.length-1:Math.min(brightness.length-1,Math.floor((now-t)/16));
    const v=brightness[index],level=v/255;
    if(state.mode==='only'){
      rgba[at]=Math.round(4+251*level);rgba[at+1]=Math.round(13+220*level);rgba[at+2]=Math.round(20+15*level);
    }else if(v){
      const mix=Math.min(1,v/Math.max(1,normalBrightness));
      const target=[4+251*level,13+220*level,20+15*level];
      for(let k=0;k<3;k++)rgba[at+k]=Math.round(rgba[at+k]*(1-mix)+target[k]*mix);
    }
  }
  function hover(id,now){
    const t=hitTime(id);
    return state.enabled&&t>=0&&t<=now?`\nLatest ${state.sourceProtocol?state.source+' detector-wide':'same-IOG'} trigger-window hit: ${((now-t)/1000).toFixed(3)} s old` : '';
  }
  root.rawTriggerUI={state,reset,configure,receiveMeta,receiveStatus,receiveFrame,renderBadges,setPaused,paint,hover,badge,hitTime,alignmentView,
                     disconnect:()=>{state.link=false;}};
})(typeof window!=='undefined'?window:globalThis);
