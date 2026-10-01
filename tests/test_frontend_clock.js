/* Node-only protocol/clock regression; no browser, graphics or network needed. */
'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(require('path').join(__dirname,'../raw_display/static/display.js'),'utf8')
  .replace(/main\(\)\.catch\([\s\S]*$/,'');
const elements={};
const ctx={console,DataView,ArrayBuffer,Float64Array,Uint8Array,Uint32Array,Int32Array,Map,Set,
  performance:{now:()=>1000},location:{protocol:'http:',host:'127.0.0.1:8765'},window:{},
  document:{getElementById:id=>elements[id]||(elements[id]={})},
  setTimeout:()=>0,clearTimeout:()=>{},
  WebSocket:class {constructor(){ctx.ws=this;}send(s){this.ack=s;}close(){}}};
vm.createContext(ctx);vm.runInContext(source,ctx);
vm.runInContext("meta={time_basis:'asic',geometry_id:'test'};seen=new Float64Array(3);seen.fill(-1);connect();",ctx);
function frame(cursor,times){
 const a=new ArrayBuffer(204+times.length*12),d=new DataView(a);
 d.setUint32(0,0x52445032,false);d.setUint32(4,17,true);d.setUint32(8,times.length,true);
 for(let i=0;i<8;i++){d.setFloat64(12+i*24,cursor,true);d.setFloat64(20+i*24,4,true);d.setFloat64(28+i*24,1,true);}
 times.forEach((t,j)=>{d.setUint32(204+j*12,j,true);d.setFloat64(208+j*12,t,true);});
 return a;
}
ctx.ws.onmessage({data:frame(2,[1,1.8,-1])});
assert.strictEqual(vm.runInContext('seen[0]',ctx),1000);
assert.strictEqual(vm.runInContext('displayTime(1,1100)',ctx),2100);
assert.strictEqual(vm.runInContext('displayTime(1,10000)',ctx),2250); // stalled client lead capped
assert.strictEqual(vm.runInContext('displayTime(1,1000)-seen[0]',ctx),1000); // late hit is 1 s old
assert.strictEqual(ctx.ws.ack,'17');
vm.runInContext('clocks[1].running=false',ctx);
assert.strictEqual(vm.runInContext('displayTime(1,1100)',ctx),2000);
console.log('ASIC frontend protocol, late-hit age, freeze and stall pacing passed');
