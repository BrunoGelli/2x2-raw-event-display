'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const c={console,Float64Array,Uint8Array,DataView,ArrayBuffer,Math,Number,Set};
vm.createContext(c);vm.runInContext(fs.readFileSync(path.join(__dirname,'../raw_display/static/trigger_view.js'),'utf8'),c);
const u=c.rawTriggerUI;
u.configure({time_basis:'asic',trigger_view_enabled:true,trigger_source_protocol:1,
  trigger_sources:{beam:5,light:6},n_pixels:4,iogs:[1,5,6]});u.reset();
function frame(magic,id,t){
  const b=new ArrayBuffer(24),d=new DataView(b);
  d.setUint32(0,magic,false);d.setUint32(4,1,true);d.setUint32(8,1,true);
  d.setUint32(12,id,true);d.setFloat64(16,t,true);return b;
}
u.receiveFrame(frame(0x52444231,1,1.25));u.receiveFrame(frame(0x52444c31,1,1.5));
u.state.source='beam';assert.equal(u.hitTime(1),1250);
u.state.source='light';assert.equal(u.hitTime(1),1500);
u.state.source='both';assert.equal(u.hitTime(1),1500);
u.setPaused(true);u.receiveFrame(frame(0x52444231,1,1.75));
u.state.source='beam';assert.equal(u.hitTime(1),1250);
u.setPaused(false);assert.equal(u.hitTime(1),1750);
const brightness=new Uint8Array([255,128,0]);let rgba=new Uint8Array([1,2,3,255]);
u.state.mode='only';u.paint(rgba,0,1,1749,brightness,255);assert.deepEqual([...rgba],[4,13,20,255]);
u.paint(rgba,0,1,1750,brightness,255);assert(rgba[0]>240&&rgba[1]>200);
u.receiveFrame(frame(0x52444231,1,-1));assert.equal(u.hitTime(1),-1);
u.state.source='both';assert.equal(u.hitTime(1),1500);
for(const [iog,label] of [[5,'BEAM WAIT'],[6,'LIGHT WAIT'],[1,'TRG WAIT']]){
 u.receiveMeta({sources:[{iog,trigger_count:0,trigger_age_s:null}]},100);
 assert.equal(u.badge(iog,100).label,label);
}
u.receiveMeta({sources:[{iog:5,trigger_count:1,trigger_age_s:.01}]},200);
assert.equal(u.badge(5,201).kind,'pulse');assert.equal(u.badge(1,201).kind,'idle');
assert.equal(u.badge(6,201).kind,'idle'); // do not fabricate physical receipt on other PACMANs
u.receiveStatus({collector_healthy:true,trigger_alignment:{capture_age_s:.1,
 common_timing:{stale_after_s:2.5,sources:[{iog:1,status:'aligned',last_valid_age_s:.1},
  {iog:5,status:'aligned',last_valid_age_s:.1},{iog:6,status:'warming',last_valid_age_s:0}]}}},1000);
assert.equal(u.alignmentView(1000).known,true);
assert.deepEqual([...u.alignmentView(1000).qualified],[1,5]);
assert.deepEqual([...u.alignmentView(3500).qualified],[]); // stale PPS cannot remain qualified on screen
assert.equal(u.alignmentView(5000).known,false);
assert.throws(()=>u.receiveFrame(frame(0x52444231,99,1)),/record/);
assert.throws(()=>u.receiveFrame(frame(0x52444c31,1,NaN)),/record/);
u.disconnect();assert.equal(u.badge(5,201).kind,'unknown');assert.equal(u.alignmentView(1000).known,false);
u.reset();assert.equal(u.state.layers.light[1],-1);assert.equal(u.state.layers.beam[1],-1);
console.log('Detector-wide source selection, separate ages, pause, invalidation, receipt provenance and alignment freshness passed.');
