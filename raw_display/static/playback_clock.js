/* Read-only wall-clock labels for the ACTUAL per-IOG display playheads.
 * No detector timing, hit selection, socket, polling loop or playback is changed.
 * Never use JSON Unix-scale tick values: reconstruct whole epoch seconds from
 * the qualified PPS anchor, then add the existing local playhead milliseconds.
 */
'use strict';
(function(root){
  const finite=x=>typeof x==='number'&&Number.isFinite(x);
  const age=x=>finite(x)&&x>=0;
  const el=id=>typeof document==='undefined'?null:document.getElementById(id);
  const formatters=new Map();
  function formatTime(ms,zone='America/Chicago'){
    if(!finite(ms)||Math.abs(ms)>8.64e15)return '—';
    if(!formatters.has(zone))formatters.set(zone,new Intl.DateTimeFormat('en-US',{
      timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',
      minute:'2-digit',second:'2-digit',fractionalSecondDigits:3,
      hourCycle:'h23',timeZoneName:'short'}));
    const p=Object.fromEntries(formatters.get(zone).formatToParts(new Date(ms)).map(x=>[x.type,x.value]));
    return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second}.${p.fractionalSecond} ${p.timeZoneName}`;
  }
  function epochSeconds(source,period){
    // No conversion of offset_ticks (~1.8e16) to a JS Number is used here.
    if(!Number.isSafeInteger(period)||period<=0||
       !Number.isSafeInteger(source?.last_sync_tick)||source.last_sync_tick<0||
       source.last_sync_tick%period!==0||
       !Number.isSafeInteger(source.last_header_second)||source.last_header_second<=0)return null;
    const offset=source.last_header_second-source.last_sync_tick/period;
    return Number.isSafeInteger(offset)&&Number.isSafeInteger(offset*1000)?offset:null;
  }
  class PlaybackClock {
    constructor(){this.epoch=0;this.zone='America/Chicago';this.frozen=null;this.paused=false;this.configure({});}
    configure(meta){
      this.meta=meta;this.iogs=Array.isArray(meta.iogs)?meta.iogs.slice():[];
      this.reset(false);this.paused=false;this.frozen=null;
    }
    reset(link=true){
      this.epoch++;this.link=link;this.frameAt=-Infinity;this.statusAt=-Infinity;
      this.data=null;this.requestMs=0;this.lastModel=null;this.lastRender=-Infinity;
      // Frozen view survives reconnects. Its label retains its original epoch.
    }
    token(){return this.epoch;}
    disconnect(){this.link=false;this.epoch++;this.data=null;this.lastRender=-Infinity;}
    frame(now){this.frameAt=now;}
    receiveStatus(data,now,token=this.epoch,requestMs=0){
      if(token!==this.epoch)return; // response started before a reconnect
      if(this.data?.trigger_alignment?.session_id && data?.trigger_alignment?.session_id &&
         this.data.trigger_alignment.session_id!==data.trigger_alignment.session_id)this.frameAt=-Infinity;
      this.data=data;this.statusAt=now;this.requestMs=Math.max(0,requestMs);
    }
    statusFailed(token=this.epoch){if(token===this.epoch){this.data=null;this.lastRender=-Infinity;}}
    sample(times,running,now){
      const unavailable=(kind,reason)=>({kind,reason,rows:[],missing:this.iogs.slice(),buffering:[],min:null,max:null,lagMin:null,lagMax:null});
      if(this.meta.time_basis!=='asic'||this.meta.mode==='DEMO')return unavailable('unavailable','No absolute detector clock in host/demo mode');
      if(!this.link)return unavailable('stale','Stream disconnected');
      if(!finite(this.frameAt)||now-this.frameAt>1000)return unavailable('stale','Waiting for a fresh display frame');
      if(!this.data||now-this.statusAt>3500||this.requestMs>1500)return unavailable('stale','Clock/status metadata unavailable or stale');
      if(this.data.collector_healthy!==true)return unavailable('stale','Collector unhealthy');
      const a=this.data.trigger_alignment,c=a?.common_timing;
      const tc=this.meta.timing_config,period=tc?.rollover_ticks;
      if(!tc||!finite(tc.tick_seconds)||!Number.isSafeInteger(period)||
         Math.abs(period*tc.tick_seconds-1)>1e-9)return unavailable('unavailable','Absolute labels require one-second PPS periods');
      if(!c||c.supported!==true||!Array.isArray(c.sources)||!age(a.capture_age_s)||
         !age(c.stale_after_s)||c.stale_after_s===0)return unavailable('warming','Waiting for qualified PPS labels');
      const elapsed=Math.max(0,(now-this.statusAt)/1000);
      // Full HTTP round trip is a conservative bound, not a latency correction.
      const extraAge=a.capture_age_s+elapsed+this.requestMs/1000;
      const rows=[],missing=[],buffering=[];
      for(const iog of this.iogs){
        const entries=c.sources.filter(s=>s.iog===iog),s=entries.length===1?entries[0]:null;
        const offset=epochSeconds(s,period),t=times[iog];
        if(!s||s.status!=='aligned'||!age(s.last_valid_age_s)||
           s.last_valid_age_s+extraAge>c.stale_after_s||offset===null||!age(t)){
          missing.push(iog);continue;
        }
        const unixMs=offset*1000+t;
        if(!finite(unixMs)||unixMs<0||unixMs>8.64e15){missing.push(iog);continue;}
        rows.push({iog,unixMs});if(running[iog]!==true)buffering.push(iog);
      }
      if(!rows.length)return unavailable('warming','No qualified, current playhead timestamps');
      const min=Math.min(...rows.map(r=>r.unixMs)),max=Math.max(...rows.map(r=>r.unixMs));
      const server=age(this.data.server_unix_s)?this.data.server_unix_s+elapsed:null;
      const lagMin=server===null?null:server-max/1000,lagMax=server===null?null:server-min/1000;
      let kind=missing.length?'partial':buffering.length?'buffering':'running';
      if(lagMin!==null&&lagMin<-.05)kind='warning';
      return {kind,reason:'Provisional header/PPS-labelled playheads',rows,missing,buffering,min,max,lagMin,lagMax};
    }
    setPaused(on,times,running,now){
      if(on&&!this.paused)this.frozen=this.sample(times,running,now);
      if(!on)this.frozen=null;
      this.paused=on;this.lastRender=-Infinity;
    }
    model(times,running,now){
      if(this.paused)return {...(this.frozen||this.sample(times,running,now)),kind:'paused'};
      return this.sample(times,running,now);
    }
    render(times,running,now){
      if(now-this.lastRender<100)return; // labels at <=10 Hz, inside existing render loop
      this.lastRender=now;
      const box=el('playback-clock');if(!box)return;
      const m=this.model(times,running,now);this.lastModel=m;box.dataset.state=m.kind;
      const title=el('playback-clock-time'),detail=el('playback-clock-detail');
      if(!title||!detail)return;
      const prefix=this.paused?'PAUSED · ':m.kind==='stale'?'STALE · ':m.kind==='warning'?'CLOCK WARNING · ':'';
      title.textContent=prefix+(m.min===null?'Displayed time unavailable':formatTime(m.min,this.zone));
      let text=m.min===null?m.reason:`Oldest of ${m.rows.length}/${this.iogs.length} labelled panes · pane spread ${((m.max-m.min)/1000).toFixed(3)} s`;
      if(m.lagMin!==null){
        const lag=`${m.lagMin.toFixed(2)}–${m.lagMax.toFixed(2)} s`;
        text+=m.lagMin<-.05?` · server comparison ${lag}; check clocks/epoch labels`:
          ` · ≈ ${lag} behind daq03${this.paused?' at pause':''}`;
      }else if(m.min!==null)text+=' · server lag unavailable';
      if(m.missing.length)text+=` · unlabelled IOG ${m.missing.join(', ')}`;
      if(m.buffering.length)text+=` · BUFFERING IOG ${m.buffering.join(', ')}`;
      if(this.paused&&!this.link)text+=' · stream disconnected; frozen view retained';
      detail.textContent=text;
      box.title=m.rows.map(r=>`IOG ${r.iog}: ${formatTime(r.unixMs,this.zone)}`).join('\n')+
        '\nTime of the playback cursor, not a unique event or every fading hit. All configured panes are included, even when a module is hidden.'+
        '\nPPS/header-labelled estimate, NOT a measured NTP lock or absolute timing certification.'+
        '\nLag uses the daq03 server clock, not browser time; return-network latency is not removed.';
    }
  }
  const clock=new PlaybackClock();
  const configure=clock.configure.bind(clock);
  clock.configure=meta=>{configure(meta);const select=el('playback-clock-zone');if(select){
    select.value=clock.zone;select.onchange=()=>{clock.zone=select.value==='UTC'?'UTC':'America/Chicago';clock.lastRender=-Infinity;};}};
  root.rawPlaybackClock=clock;
  if(typeof module!=='undefined'&&module.exports)module.exports={PlaybackClock,epochSeconds,formatTime};
})(typeof window!=='undefined'?window:globalThis);
