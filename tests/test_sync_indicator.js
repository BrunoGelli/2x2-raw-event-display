/* Tests the actual served JS without a browser or real detector. */
'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const source=fs.readFileSync(path.join(__dirname,'../raw_display/static/display.js'),'utf8')
 .replace(/main\(\)\.catch\([\s\S]*$/,'');
let now=1000;
const elements={};
const ctx={console,DataView,ArrayBuffer,Float64Array,Uint8Array,Uint32Array,Int32Array,Map,Set,
 performance:{now:()=>now},location:{protocol:'http:',host:'127.0.0.1:8765'},window:{},
 document:{getElementById:id=>elements[id]||(elements[id]={})},setTimeout:()=>0,clearTimeout:()=>{},
 WebSocket:class {constructor(url){ctx.ws=this;this.url=url;this.acks=[];}send(s){this.acks.push(s);}close(){this.closed=true;}}};
vm.createContext(ctx);vm.runInContext(source,ctx);
vm.runInContext("meta={time_basis:'asic',geometry_id:'test',iogs:[1,6]};seen=new Float64Array(3);seen.fill(-1);connect();",ctx);
ctx.ws.onopen();
assert(ctx.ws.url.endsWith('&sync83=1'));
function send(count,age,group=1){ctx.ws.onmessage({data:JSON.stringify({type:'sync83',version:1,sources:[{iog:group,count,age_s:age}]})});}
function view(iog=1,t=now){return vm.runInContext(`sync83View(${iog},${t})`,ctx);}
send(100,.1);assert.strictEqual(view().kind,'live'); // initial history is NOT a new flash
send(101,.02);assert.strictEqual(view().kind,'pulse');assert.strictEqual(ctx.ws.acks.length,0);
assert.strictEqual(view(6).kind,'waiting'); // independent IOG state
now+=300;send(101,.32);assert.strictEqual(view().kind,'live'); // duplicate reports do not retrigger
now+=100;send(102,.8);assert.strictEqual(view().kind,'live'); // old delayed update is not a fresh pulse
now+=1800;send(102,2.6);assert.strictEqual(view().kind,'stale');
assert.strictEqual(view(1,now+4000).kind,'unknown'); // lost browser transport is not a missing-PPS claim
now+=100;send(1,.03);assert.strictEqual(view().kind,'live'); // collector restart, counter decreases
send(2,.02,6);assert.strictEqual(view(6).kind,'live');
send(3,.02,6);assert.strictEqual(view(6).kind,'pulse');
ctx.ws.onclose({code:1000});assert.strictEqual(view().kind,'unknown');
vm.runInContext('connect()',ctx);ctx.ws.onopen();send(103,.04);assert.strictEqual(view().kind,'live');
// Existing RDP2 clock/age path is unchanged by SYNC arrival metadata.
function frame(cursor,times){
 const a=new ArrayBuffer(204+times.length*12),d=new DataView(a);
 d.setUint32(0,0x52445032,false);d.setUint32(4,17,true);d.setUint32(8,times.length,true);
 for(let i=0;i<8;i++){d.setFloat64(12+i*24,cursor,true);d.setFloat64(20+i*24,4,true);d.setFloat64(28+i*24,1,true);}
 times.forEach((t,j)=>{d.setUint32(204+j*12,j,true);d.setFloat64(208+j*12,t,true);});return a;
}
now=5000;ctx.ws.onmessage({data:frame(2,[1,1.8,-1])});
assert.strictEqual(vm.runInContext('seen[0]',ctx),1000);
assert.strictEqual(vm.runInContext('displayTime(1,5100)',ctx),2100);
assert.strictEqual(vm.runInContext('displayTime(1,20000)',ctx),2250);
assert.strictEqual(vm.runInContext('displayTime(1,5000)-seen[0]',ctx),1000);
assert.deepStrictEqual(ctx.ws.acks,['17']);
vm.runInContext('clocks[1].running=false',ctx);assert.strictEqual(vm.runInContext('displayTime(1,5100)',ctx),2000);
// No fabricated 1Hz flashes: without new counters, only aging/stale states change.
assert.notStrictEqual(view(1,now+1000).kind,'pulse');
console.log('SYNC-83 counter pulses, per-IOG isolation, stale/reconnect, and RDP2 clock regression passed');
