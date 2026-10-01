/* Same-IOG ASIC-time window layer. Receipt badges are NOT event-time gates. */
'use strict';
(function(root){
  const state={enabled:false,mode:'all',tagged:new Float64Array(0),frozen:null,
    paused:false,link:false,at:-Infinity,
    sources:Array.from({length:9},()=>({count:null,age:null,at:0,pulse:0}))};
  const element=id=>typeof document!=='undefined'?document.getElementById(id):null;
  function reset(){
    state.tagged.fill(-1);state.link=true;state.at=-Infinity;
    state.sources=Array.from({length:9},()=>({count:null,age:null,at:0,pulse:0}));
  }
  function configure(meta){
    state.enabled=meta.time_basis==='asic'&&meta.trigger_view_enabled===true;
    state.tagged=new Float64Array(meta.n_pixels);state.tagged.fill(-1);
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
    const note=element('trigger-scope');
    if(note)note.textContent=state.enabled?
      `Trigger windows: SAME IOG ONLY; ${meta.trigger_pre_us??0} µs before to ${meta.trigger_post_us??300} µs after a selected T word. Yellow = time-window candidate, not a complete event. Other IOGs are not aligned.`:
      'TRG RX badges are read-only. Enable RAW_DISPLAY_TRIGGER_VIEW=1 for same-IOG ASIC-time windows.';
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
    if(d.byteLength<4||d.getUint32(0,false)!==0x52445431)return false; // RDT1
    if(!state.enabled||d.byteLength<12)throw Error('Unexpected trigger-window frame');
    const n=d.getUint32(8,true);
    if(d.byteLength!==12+12*n)throw Error('Bad trigger-window frame length');
    for(let j=0;j<n;j++){
      const at=12+j*12,id=d.getUint32(at,true),t=d.getFloat64(at+4,true);
      if(id>=state.tagged.length||!Number.isFinite(t)||t< -1)throw Error('Bad trigger-window record');
      state.tagged[id]=t<0?-1:t*1000;
    }
    return true; // no ACK here: canonical RDP2 follows and owns the ACK
  }
  function badge(iog,now){
    const s=state.sources[iog];
    if(!state.link||now-state.at>3500)return {kind:'unknown',label:'TRG —',count:s.count??0,age:null};
    if(s.count===null||s.age===null||s.count===0)return {kind:'idle',label:'TRG WAIT',count:s.count??0,age:null};
    return {kind:now<s.pulse?'pulse':'idle',label:'TRG RX',count:s.count,age:s.age+Math.max(0,now-s.at)/1000};
  }
  function renderBadges(meta,now){
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
        ' A receipt badge, not a playback-time or beam/light identity claim. SYNC S words never trigger it.';
    }
  }
  function setPaused(paused){state.paused=paused;state.frozen=paused?state.tagged.slice():null;}
  function paint(rgba,at,id,now,brightness,normalBrightness){
    if(!state.enabled||state.mode==='all')return;
    const t=(state.paused?state.frozen:state.tagged)[id];
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
    const t=(state.paused?state.frozen:state.tagged)[id];
    return state.enabled&&t>=0&&t<=now?`\nLatest same-IOG trigger-window hit: ${((now-t)/1000).toFixed(3)} s old` : '';
  }
  root.rawTriggerUI={state,reset,configure,receiveMeta,receiveFrame,renderBadges,setPaused,paint,hover,badge,
                     disconnect:()=>{state.link=false;}};
})(typeof window!=='undefined'?window:globalThis);
