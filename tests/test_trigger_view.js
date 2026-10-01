'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const context={console,Float64Array,Uint8Array,DataView,ArrayBuffer,Math,Number,Set};
vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../raw_display/static/trigger_view.js'),'utf8'),context);
const ui=context.rawTriggerUI;
ui.configure({time_basis:'asic',trigger_view_enabled:true,n_pixels:4});ui.reset();
function meta(count,age){return {sources:[{iog:1,trigger_count:count,trigger_age_s:age}]};}
ui.receiveMeta(meta(0,null),100);assert.equal(ui.badge(1,100).kind,'idle');
ui.receiveMeta(meta(1,.01),200);assert.equal(ui.badge(1,201).kind,'pulse');
assert.equal(ui.badge(1,500).kind,'idle');assert.equal(ui.badge(1,5000).kind,'unknown');
ui.receiveMeta(meta(1,.5),600);assert.equal(ui.badge(1,601).kind,'idle'); // no fake repeated pulse
ui.receiveMeta(meta(2,2),700);assert.equal(ui.badge(1,701).kind,'idle'); // old delivery not fresh
const b=new ArrayBuffer(24),d=new DataView(b);d.setUint32(0,0x52445431,false);d.setUint32(4,7,true);d.setUint32(8,1,true);d.setUint32(12,1,true);d.setFloat64(16,1.25,true);
assert(ui.receiveFrame(b));assert.equal(ui.state.tagged[1],1250);
const bright=new Uint8Array(100);for(let i=0;i<99;i++)bright[i]=Math.round(255*Math.exp(-i*.1));
let rgba=new Uint8Array([1,2,3,255]);ui.state.mode='all';ui.paint(rgba,0,1,1250,bright,255);assert.deepEqual([...rgba],[1,2,3,255]);
ui.state.mode='only';ui.paint(rgba,0,1,1250,bright,255);assert(rgba[0]>240&&rgba[1]>200&&rgba[2]<60);
ui.paint(rgba,0,1,1200,bright,255);assert.deepEqual([...rgba],[4,13,20,255]); // future invisible
ui.setPaused(true);d.setFloat64(16,1.5,true);ui.receiveFrame(b);assert.equal(ui.state.frozen[1],1250);ui.setPaused(false);
ui.disconnect();assert.equal(ui.badge(1,701).kind,'unknown');
console.log('Trigger badge, subtype-independent receipt logic, matching protocol, yellow rendering and pause tests passed.');
