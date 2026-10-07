/* Optional R3D1 state. No renderer, request or array allocation when off. */
'use strict';
(function(root){
  const state={enabled:false,wanted:false,paused:false,pairs:null,frozen:null,version:0,frozenVersion:0,
    error:null,frames:0,bytes:0,invalidFrames:0,hasSnapshot:false,frozenHasSnapshot:false};
  function pairs(n){
    const result={};for(const name of ['beam','light']){
      result[name]={hits:new Float64Array(n),delta:new Float64Array(n)};
      result[name].hits.fill(-1);result[name].delta.fill(-1);
    }return result;
  }
  function configure(meta){
    state.enabled=meta.view3d_enabled===true&&meta.time_basis==='asic';
    state.pairs=state.enabled?pairs(meta.n_pixels):null;state.version++;
  }
  function reset(){
    if(state.pairs)for(const p of Object.values(state.pairs)){p.hits.fill(-1);p.delta.fill(-1);}
    state.hasSnapshot=false;state.version++; // frozen pause state intentionally survives reconnects
  }
  function setPaused(paused){
    state.paused=paused;
    state.frozen=paused&&state.pairs?Object.fromEntries(Object.entries(state.pairs).map(([k,p])=>
      [k,{hits:p.hits.slice(),delta:p.delta.slice()}])):null;
    state.frozenVersion=state.version;state.frozenHasSnapshot=state.hasSnapshot;
  }
  function receiveFrame(buffer){
    const d=new DataView(buffer);
    if(d.byteLength<4||d.getUint32(0,false)!==0x52334431)return false;
    if(state.error)return true;
    try{
      if(!state.enabled||d.byteLength<12)throw Error('Unexpected R3D1 frame');
      const n=d.getUint32(8,true),size=state.pairs.beam.hits.length;
      if(n>2*size||d.byteLength!==12+24*n)throw Error('Invalid R3D1 length');
      // Validate the whole frame before changing any hit/t0 pair.
      for(let j=0;j<n;j++){
        const at=12+24*j,id=d.getUint32(at,true),source=d.getUint32(at+4,true);
        const hit=d.getFloat64(at+8,true),delta=Number(d.getBigInt64(at+16,true));
        if(id>=size||source>1||!Number.isFinite(hit)||hit< -1||!Number.isSafeInteger(delta)||delta< -1)
          throw Error('Invalid R3D1 pair');
      }
      for(let j=0;j<n;j++){
        const at=12+24*j,id=d.getUint32(at,true),p=state.pairs[d.getUint32(at+4,true)===0?'beam':'light'];
        p.hits[id]=d.getFloat64(at+8,true);p.delta[id]=Number(d.getBigInt64(at+16,true));
      }
      state.hasSnapshot=true;state.frames++;state.bytes+=d.byteLength;state.version++;
    }catch(error){state.error=error.message;state.wanted=false;state.invalidFrames++;}
    return true; // an optional failure must not close the canonical RDP2 socket
  }
  function driftDistance(delta,tickSeconds,metadata){
    const distance=delta*tickSeconds*1e6*metadata.v_drift_mm_per_us;
    if(!Number.isSafeInteger(delta)||delta<0||!Number.isFinite(distance)||
       distance>metadata.max_drift_distance_mm+metadata.boundary_tolerance_mm)return {valid:false,distance:NaN,clamped:false};
    return {valid:true,distance:Math.min(distance,metadata.max_drift_distance_mm),
      clamped:distance>metadata.max_drift_distance_mm};
  }
  root.rawDriftData={state,configure,reset,setPaused,receiveFrame,driftDistance,
    current:()=>state.paused?state.frozen:state.pairs,
    version:()=>state.paused?state.frozenVersion:state.version};
})(typeof window!=='undefined'?window:globalThis);
