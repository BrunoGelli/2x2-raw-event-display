'use strict';
// Node-only regression: source-specific, 2D-only/highlight, light/dark, pause and ties.
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const context={Float64Array,Uint8Array,DataView,ArrayBuffer,Math,Number,Set};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../raw_display/static/trigger_view.js'),'utf8'),context);
const ui=context.rawTriggerUI;
ui.configure({time_basis:'asic',trigger_view_enabled:true,trigger_source_protocol:1,
  trigger_sources:{beam:5,light:6},n_pixels:4,iogs:[1,5,6]});
const palettes={
  dark:{beam:[255,233,35],light:[255,92,112],base:[4,13,20]},
  light:{beam:[29,89,179],light:[143,95,0],base:[244,248,251]}
};
const brightness=new Uint8Array([255,128,0]);
function frame(magic,id,t){
  const b=new ArrayBuffer(24),d=new DataView(b);
  d.setUint32(0,magic,false);d.setUint32(4,1,true);d.setUint32(8,1,true);
  d.setUint32(12,id,true);d.setFloat64(16,t,true);return b;
}
function draw(id,now,normal=255){
  const rgba=new Uint8Array([24,50,65,255]);
  ui.paint(rgba,0,id,now,brightness,normal);
  return [...rgba].slice(0,3);
}
ui.receiveFrame(frame(0x52444231,0,1)); // Beam on pixel 0
ui.receiveFrame(frame(0x52444c31,1,1)); // Light on pixel 1
ui.state.mode='only';
for(const theme of ['dark','light']){
  ui.setTheme(theme);
  for(const source of ['beam','light']){
    ui.state.source=source;
    assert.deepEqual(draw(source==='beam'?0:1,1000),palettes[theme][source]);
    assert.deepEqual(draw(source==='beam'?1:0,1000),palettes[theme].base,
      'source filter must not paint the other source');
  }
  ui.state.source='both';
  assert.deepEqual(draw(0,1000),palettes[theme].beam);
  assert.deepEqual(draw(1,1000),palettes[theme].light);
  assert.deepEqual(draw(3,1000),palettes[theme].base,'empty pixels stay blank');
  assert.deepEqual(draw(0,999),palettes[theme].base,'future hits are hidden');
  ui.state.mode='highlight';
  assert.deepEqual(draw(1,1000),palettes[theme].light,
    'fully bright candidates replace ordinary hits in highlight mode');
  ui.state.mode='only';
  assert.deepEqual(draw(1,1016),palettes[theme].base.map((v,i)=>
    Math.round(v+(palettes[theme].light[i]-v)*128/255)), 'candidate fade remains intact');
}
ui.setTheme('dark');ui.state.source='both';
ui.receiveFrame(frame(0x52444231,2,1));
ui.receiveFrame(frame(0x52444c31,2,1.016));
assert.equal(ui.hitSource(2),'light');
assert.deepEqual(draw(2,1016),palettes.dark.light,'latest Light wins overlap');
ui.receiveFrame(frame(0x52444231,2,1.032));
assert.equal(ui.hitSource(2),'beam');
assert.deepEqual(draw(2,1032),palettes.dark.beam,'later Beam wins overlap');
ui.receiveFrame(frame(0x52444c31,2,1.032));
assert.equal(ui.hitSource(2),'beam','Beam wins exact ties');
assert.match(ui.hover(2,1032),/beam detector-wide/);
ui.setPaused(true);
ui.receiveFrame(frame(0x52444c31,2,2));
assert.equal(ui.hitTime(2),1032,'pause freezes source times');
assert.equal(ui.hitSource(2),'beam','pause freezes source identity');
assert.deepEqual(draw(2,1032),palettes.dark.beam);
ui.setPaused(false);
assert.equal(ui.hitSource(2),'light');
assert.deepEqual(draw(2,2000),palettes.dark.light);
ui.configure({time_basis:'asic',trigger_view_enabled:true,n_pixels:4});
ui.state.mode='only';ui.state.tagged[0]=1000;ui.setTheme('dark');
assert.deepEqual(draw(0,1000),palettes.dark.beam,'untyped legacy windows keep their original yellow');
ui.setTheme('light');assert.deepEqual(draw(0,1000),palettes.light.beam,
  'legacy fallback uses the default Beam hue');
const source3d=fs.readFileSync(path.join(__dirname,'../raw_display/static/display3d.js'),'utf8');
assert.match(source3d,/window\.rawTriggerUI\.colors/,'3D must use the shared color palette');
assert.match(source3d,/toHex\(triggerColors\[light\?'light':'dark'\]\[name\]\)/);
console.log('Beam/Light palette, source filtering, overlap, fade, pause, legacy and 3D palette bridge passed.');
