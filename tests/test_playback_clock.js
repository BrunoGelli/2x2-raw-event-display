'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {PlaybackClock,epochSeconds,formatTime}=require('../raw_display/static/playback_clock.js');
const R=10000000,U=1791338400,now=10000;
function fixture(){
  const clock=new PlaybackClock();
  clock.configure({mode:'LIVE',time_basis:'asic',iogs:[1,2,3,4,5,6,7,8],
    timing_config:{rollover_ticks:R,tick_seconds:1e-7}});
  clock.reset();clock.frame(now);
  const sources=clock.iogs.map(i=>({iog:i,status:'aligned',last_valid_age_s:.1,
    last_sync_tick:(40-i)*R,last_header_second:U,
    offset_ticks:U*R-(40-i)*R}));
  const data={collector_healthy:true,server_unix_s:U+.3,
    trigger_alignment:{session_id:'one',capture_age_s:.01,
    common_timing:{supported:true,stale_after_s:2.5,sources}}};
  clock.receiveStatus(data,now,clock.token(),10);
  const times=Array.from({length:9},(_,i)=>(39-i)*1000);
  const running=Array(9).fill(true);
  return {clock,data,times,running};
}
function sample(f,at=now){return f.clock.model(f.times,f.running,at);}
test('eight different local epochs label the same displayed instant',()=>{
  const f=fixture(),m=sample(f);
  assert.equal(m.kind,'running');assert.equal(m.rows.length,8);
  assert.equal(m.min,(U-1)*1000);assert.equal(m.max,m.min);
  assert.ok(Math.abs(m.lagMin-1.3)<1e-6);
});
test('published Unix-scale ticks are NOT used even if rounded or wrong',()=>{
  const f=fixture();for(const s of f.data.trigger_alignment.common_timing.sources)s.offset_ticks=1;
  assert.equal(sample(f).min,(U-1)*1000);
});
test('millisecond subsecond position survives conversion',()=>{
  const f=fixture();f.times=f.times.map(t=>t+123.4);
  assert.ok(Math.abs(sample(f).min-((U-1)*1000+123.4))<.001);
});
test('independent playhead spread is not hidden',()=>{
  const f=fixture();f.times[8]+=200;
  const m=sample(f);assert.equal(m.max-m.min,200);assert.ok(Math.abs(m.lagMin-1.1)<1e-6);
});
test('continuous playhead crossing PPS naturally crosses Unix second',()=>{
  const f=fixture();f.times=f.times.map(t=>t+1100);
  assert.equal(sample(f).min,U*1000+100);
});
test('changing consistent PPS anchors does not move the displayed time',()=>{
  const f=fixture();const first=sample(f).min;
  for(const s of f.data.trigger_alignment.common_timing.sources){s.last_header_second++;s.last_sync_tick+=R;}
  assert.equal(sample(f).min,first);
});
test('negative or not-started playheads are excluded',()=>{
  const f=fixture();f.times[6]=-1;
  const m=sample(f);assert.equal(m.kind,'partial');assert.deepEqual(m.missing,[6]);
});
test('suspect group is excluded rather than relabelled',()=>{
  const f=fixture();f.data.trigger_alignment.common_timing.sources[5].status='suspect';
  assert.deepEqual(sample(f).missing,[6]);
});
test('stale PPS age includes publication and HTTP freshness',()=>{
  const f=fixture();f.data.trigger_alignment.common_timing.sources[0].last_valid_age_s=2.49;
  assert.deepEqual(sample(f).missing,[1]);
});
test('invalid PPS values never render a wall-clock date',()=>{
  for(const change of [{last_sync_tick:1},{last_sync_tick:null},{last_sync_tick:2**54},
      {last_header_second:0},{last_header_second:null},{last_valid_age_s:null}]){
    const f=fixture();Object.assign(f.data.trigger_alignment.common_timing.sources[0],change);
    assert.deepEqual(sample(f).missing,[1]);
  }
});
test('duplicate IOG metadata is rejected rather than ambiguously selected',()=>{
  const f=fixture();f.data.trigger_alignment.common_timing.sources.push(f.data.trigger_alignment.common_timing.sources[0]);
  assert.deepEqual(sample(f).missing,[1]);
});
test('missing labels show warming, never browser now',()=>{
  const f=fixture();f.data.trigger_alignment.common_timing.sources=[];
  assert.equal(sample(f).kind,'warming');assert.equal(sample(f).min,null);
});
test('lack of fresh frames makes time unavailable',()=>{
  const f=fixture();assert.equal(sample(f,now+1001).kind,'stale');assert.equal(sample(f,now+1001).min,null);
});
test('fresh frames alone cannot disguise stale status',()=>{
  const f=fixture();f.clock.frame(now+4000);assert.equal(sample(f,now+4000).kind,'stale');
});
test('unhealthy collector does not appear live',()=>{
  const f=fixture();f.data.collector_healthy=false;assert.equal(sample(f).kind,'stale');
});
test('slow status round trip is not presented as live timing',()=>{
  const f=fixture();f.clock.receiveStatus(f.data,now,f.clock.token(),2000);assert.equal(sample(f).kind,'stale');
});
test('buffering is visible while its playhead stays fixed',()=>{
  const f=fixture();f.running[1]=false;
  assert.equal(sample(f).kind,'buffering');assert.deepEqual(sample(f).buffering,[1]);
  assert.equal(sample(f,now+500).rows[0].unixMs,sample(f).rows[0].unixMs);
});
test('pause freezes the displayed time and the lag-at-pause value',()=>{
  const f=fixture();f.clock.setPaused(true,f.times,f.running,now);
  const before=sample(f);f.times=f.times.map(t=>t+10000);
  const after=sample(f,now+10000);
  assert.equal(after.kind,'paused');assert.equal(after.min,before.min);assert.equal(after.lagMax,before.lagMax);
});
test('pause retains the old epoch through disconnect and reconnect',()=>{
  const f=fixture();f.clock.setPaused(true,f.times,f.running,now);const before=sample(f).min;
  f.clock.disconnect();f.clock.reset();
  for(const s of f.data.trigger_alignment.common_timing.sources)s.last_header_second+=100;
  f.clock.receiveStatus(f.data,now+20);f.clock.frame(now+20);
  assert.equal(sample(f,now+20).min,before);
  f.clock.setPaused(false,f.times,f.running,now+20);
  assert.equal(sample(f,now+20).min,before+100000);
});
test('pause before qualification stays explicitly unlabelled',()=>{
  const f=fixture();f.clock.statusFailed();f.clock.setPaused(true,f.times,f.running,now);
  f.clock.receiveStatus(f.data,now);assert.equal(sample(f).min,null);
});
test('responses requested before reconnect cannot restore an old epoch',()=>{
  const f=fixture(),token=f.clock.token();f.clock.disconnect();f.clock.reset();f.clock.frame(now);
  f.clock.receiveStatus(f.data,now,token);assert.equal(sample(f).kind,'stale');
});
test('collector session change requires another actual frame',()=>{
  const f=fixture(),data=structuredClone(f.data);data.trigger_alignment.session_id='two';
  f.clock.receiveStatus(data,now);assert.equal(sample(f).kind,'stale');
  f.clock.frame(now);assert.equal(sample(f).kind,'running');
});
test('failed status invalidates clock but not frozen view',()=>{
  const f=fixture();f.clock.statusFailed();assert.equal(sample(f).kind,'stale');
});
test('host/demo and non-second reset modes never invent absolute time',()=>{
  const f=fixture();f.clock.meta.mode='DEMO';assert.equal(sample(f).kind,'unavailable');
  f.clock.meta.mode='LIVE';f.clock.meta.time_basis='host';assert.equal(sample(f).kind,'unavailable');
  f.clock.meta.time_basis='asic';f.clock.meta.timing_config.tick_seconds=2e-7;assert.equal(sample(f).kind,'unavailable');
});
test('browser Date.now never participates in the timestamp or lag',()=>{
  const f=fixture(),original=Date.now;Date.now=()=>{throw Error('browser clock used');};
  try{assert.equal(sample(f).min,(U-1)*1000);assert.ok(formatTime(sample(f).min,'UTC').includes('UTC'));}
  finally{Date.now=original;}
});
test('negative lag is exposed as a comparison warning, not clamped',()=>{
  const f=fixture();f.data.server_unix_s=U-2;
  const m=sample(f);assert.equal(m.kind,'warning');assert.equal(m.lagMin,-1);
});
test('server clock missing leaves data time but no claimed lag',()=>{
  const f=fixture();delete f.data.server_unix_s;assert.equal(sample(f).lagMin,null);assert.notEqual(sample(f).min,null);
});
test('UTC/Chicago labels include date, zone, milliseconds and midnight',()=>{
  const stamp=Date.UTC(2026,9,7,1,32,10,123);
  assert.equal(formatTime(stamp,'UTC'),'2026-10-07 01:32:10.123 UTC');
  assert.equal(formatTime(stamp,'America/Chicago'),'2026-10-06 20:32:10.123 CDT');
  assert.equal(formatTime(Date.UTC(2026,9,7,0),'UTC'),'2026-10-07 00:00:00.000 UTC');
});
test('Chicago DST fold is explicit in the timezone abbreviation',()=>{
  assert.equal(formatTime(Date.UTC(2026,10,1,6,30),'America/Chicago'),'2026-11-01 01:30:00.000 CDT');
  assert.equal(formatTime(Date.UTC(2026,10,1,7,30),'America/Chicago'),'2026-11-01 01:30:00.000 CST');
});
test('clock reads playheads without mutating them',()=>{
  const f=fixture();Object.freeze(f.times);Object.freeze(f.running);const before=f.times.slice();
  sample(f);assert.deepEqual(f.times,before);
});
