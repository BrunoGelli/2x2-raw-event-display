/* Lazy, locally vendored renderer. Flow x/y/z coordinates in mm. */
import * as THREE from './vendor/three.module.min.js';
import {OrbitControls} from './vendor/OrbitControls.js';

export async function createDetector3D(meta,rawPixels,holder){
  const g=meta.geometry3d,response=await fetch('/api/geometry3d.bin',{cache:'no-cache'});
  if(!response.ok)throw Error('3D geometry unavailable');
  const bytes=await response.arrayBuffer(),n=meta.n_pixels;
  if(bytes.byteLength!==16*n||g.record_bytes!==16||g.coordinate_units!=='mm')throw Error('3D geometry format mismatch');
  if(globalThis.crypto?.subtle){
    const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
    if(hash!==g.served_sha256)throw Error('3D geometry hash mismatch');
  }
  const view=new DataView(bytes),xyz=new Float32Array(n*3),signs=new Int8Array(n),iogs=new Float32Array(n);
  for(let id=0;id<n;id++){
    for(let k=0;k<3;k++){
      const v=view.getFloat32(id*16+k*4,true);if(!Number.isFinite(v))throw Error('Invalid 3D coordinate');xyz[id*3+k]=v;
    }
    signs[id]=view.getInt8(id*16+12);iogs[id]=rawPixels.getUint8(id*8+6)-1;
    if(Math.abs(signs[id])!==1||iogs[id]<0||iogs[id]>7)throw Error('Invalid 3D drift direction/IOG');
  }
  const renderer=new THREE.WebGLRenderer({antialias:true,alpha:false,powerPreference:'low-power'});
  renderer.setPixelRatio(Math.min(devicePixelRatio||1,1.5));renderer.setClearColor('#07121a');
  renderer.domElement.setAttribute('aria-label','3D detector: drag to orbit, wheel to zoom');
  holder.replaceChildren(renderer.domElement);
  let failed=null;
  renderer.domElement.addEventListener('webglcontextlost',event=>{event.preventDefault();failed='WebGL context lost. Reload to retry 3D.';});
  const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(43,1,1,20000);
  const controls=new OrbitControls(camera,renderer.domElement);
  controls.enableDamping=true;controls.dampingFactor=.12;controls.minDistance=300;controls.maxDistance=6500;
  const target=new THREE.Vector3(0,0,0);
  const bounds=new THREE.Box3(new THREE.Vector3(...g.detector_bounds_mm[0]),new THREE.Vector3(...g.detector_bounds_mm[1]));
  bounds.getCenter(target);
  function reset(){camera.position.copy(target).add(new THREE.Vector3(1850,1250,2150));controls.target.copy(target);controls.update();}
  reset();renderer.domElement.addEventListener('dblclick',reset);
  function outline(min,max,color,opacity){
    const size=new THREE.Vector3(...max).sub(new THREE.Vector3(...min));
    const geometry=new THREE.BoxGeometry(size.x,size.y,size.z);
    const line=new THREE.LineSegments(new THREE.EdgesGeometry(geometry),new THREE.LineBasicMaterial({color,transparent:true,opacity}));
    line.position.copy(new THREE.Vector3(...min).add(new THREE.Vector3(...max)).multiplyScalar(.5));scene.add(line);geometry.dispose();
  }
  function plane(x,min,max,color,opacity){
    const mesh=new THREE.Mesh(new THREE.PlaneGeometry(max[2]-min[2],max[1]-min[1]),
      new THREE.MeshBasicMaterial({color,side:THREE.DoubleSide,transparent:true,opacity,depthWrite:false}));
    mesh.rotation.y=Math.PI/2;mesh.position.set(x,(min[1]+max[1])/2,(min[2]+max[2])/2);scene.add(mesh);
    outline([x,min[1],min[2]],[x,max[1],max[2]],color,.35);
  }
  function label(text,position,color='#bed0dc'){
    const c=document.createElement('canvas');c.width=256;c.height=64;
    const ctx=c.getContext('2d');ctx.font='30px sans-serif';ctx.fillStyle=color;ctx.textAlign='center';ctx.fillText(text,128,42);
    const sprite=new THREE.Sprite(new THREE.SpriteMaterial({map:new THREE.CanvasTexture(c),transparent:true,depthTest:false}));
    sprite.position.copy(position);sprite.scale.set(260,65,1);scene.add(sprite);
  }
  for(const module of g.modules){
    const [min,max]=module.bounds_mm;
    outline(min,max,0x8ca7b9,.28);
    plane(min[0],min,max,0x45bdcb,.035);plane(max[0],min,max,0x45bdcb,.035);
    plane(module.cathode_x_mm,min,max,0x8893b0,.04);
    label(`Module ${module.module}`,new THREE.Vector3((min[0]+max[0])/2,max[1]+50,(min[2]+max[2])/2));
  }
  const axisOrigin=new THREE.Vector3(bounds.min.x-160,bounds.min.y-30,bounds.min.z-160);
  for(const [axis,direction,color] of [['x · drift',[1,0,0],0xdd8d8d],['y · up',[0,1,0],0x89caa0],['z · beam',[0,0,1],0x81b0e6]]){
    const dir=new THREE.Vector3(...direction);scene.add(new THREE.ArrowHelper(dir,axisOrigin,190,color,18,9));
    label(axis,axisOrigin.clone().addScaledVector(dir,240));
  }
  const uniforms={uTime:{value:new Float32Array(8)},uTau:{value:.65},uSize:{value:4*renderer.getPixelRatio()}};
  function cloud(color){
    const geometry=new THREE.BufferGeometry(),positions=xyz.slice(),times=new Float32Array(n);times.fill(-1e6);
    geometry.setAttribute('position',new THREE.BufferAttribute(positions,3).setUsage(THREE.DynamicDrawUsage));
    geometry.setAttribute('aHit',new THREE.BufferAttribute(times,1).setUsage(THREE.DynamicDrawUsage));
    const groups=new Float32Array(n),lookup=new Int32Array(n);lookup.fill(-1);
    geometry.setAttribute('aIog',new THREE.BufferAttribute(groups,1).setUsage(THREE.DynamicDrawUsage));
    geometry.setDrawRange(0,0);
    const material=new THREE.ShaderMaterial({uniforms:{...uniforms,uColor:{value:new THREE.Color(color)}},
      transparent:true,depthWrite:false,blending:THREE.AdditiveBlending,
      vertexShader:`attribute float aHit; attribute float aIog;
        uniform float uTime[8]; uniform float uTau; uniform float uSize; varying float glow;
        void main(){float age=uTime[int(aIog)]-aHit;
          glow=(aHit < -100000.0 || age < 0.0 || age > 8.0*uTau)?0.0:exp(-age/uTau);
          gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);gl_PointSize=uSize;}`,
      fragmentShader:`uniform vec3 uColor; varying float glow;
        void main(){float r=length(gl_PointCoord-vec2(0.5));if(r>0.5||glow<0.002)discard;
          gl_FragColor=vec4(uColor,glow*(1.0-smoothstep(0.12,0.5,r)));}`});
    const points=new THREE.Points(geometry,material);points.frustumCulled=false;scene.add(points);
    return {points,positions,times,groups,lookup,geometry};
  }
  const clouds={normal:cloud(0x61f5be),beam:cloud(0xffe923),light:cloud(0xffe923)};
  let origin=new Float64Array(8).fill(NaN),normalVersion=null,pairVersion=null,lastSize='',pausedBefore=null,lastTau=null;
  const diagnostics={clampedCandidates:0,rejectedCandidates:0,draws:0,geometryPixels:n};
  function render({normal,clocks,normalRevision,paused,tau,mode,source,fresh}){
    if(failed)throw Error(failed);
    if(window.rawDriftData.state.error)throw Error(window.rawDriftData.state.error);
    const width=Math.max(1,holder.clientWidth),height=Math.max(1,holder.clientHeight),size=`${width}:${height}`;
    if(lastSize!==size){renderer.setSize(width,height,false);camera.aspect=width/height;camera.updateProjectionMatrix();lastSize=size;}
    let rebased=pausedBefore!==paused||lastTau!==tau;pausedBefore=paused;lastTau=tau;
    for(let i=0;i<8;i++){
      const t=clocks[i+1]/1000,newOrigin=t<0?0:Math.floor(t/64)*64;
      if(origin[i]!==newOrigin){origin[i]=newOrigin;rebased=true;}
      uniforms.uTime.value[i]=t-origin[i];
    }
    uniforms.uTau.value=tau;
    function upload(c,count){
      c.geometry.setDrawRange(0,count);
      if(count)for(const [key,size] of [['position',3],['aHit',1],['aIog',1]]){
        const a=c.geometry.attributes[key];a.clearUpdateRanges();a.addUpdateRange(0,count*size);a.needsUpdate=true;
      }
    }
    if(rebased||normalVersion!==normalRevision){
      const c=clouds.normal;let count=0;c.lookup.fill(-1);
      for(let id=0;id<n;id++){
        if(normal[id]<0||clocks[iogs[id]+1]/1000-normal[id]/1000>8*tau)continue;
        c.lookup[id]=count;c.times[count]=normal[id]/1000-origin[iogs[id]];c.groups[count]=iogs[id];
        c.positions[count*3]=xyz[id*3];c.positions[count*3+1]=xyz[id*3+1];c.positions[count*3+2]=xyz[id*3+2];count++;
      }
      upload(c,count);diagnostics.normalPoints=count;normalVersion=normalRevision;
    }
    const pairs=window.rawDriftData.current(),revision=window.rawDriftData.version();
    if(pairs&&(rebased||pairVersion!==revision)){
      let clamped=0,rejected=0;
      for(const name of ['beam','light']){
        const c=clouds[name],p=pairs[name];let count=0;c.lookup.fill(-1);
        for(let id=0;id<n;id++){
          if(p.hits[id]<0)continue;
          const drift=window.rawDriftData.driftDistance(p.delta[id],meta.timing_config.tick_seconds,g);
          clamped+=Number(drift.clamped);rejected+=Number(!drift.valid);
          if(!drift.valid||clocks[iogs[id]+1]/1000-p.hits[id]>8*tau)continue;
          c.lookup[id]=count;c.times[count]=p.hits[id]-origin[iogs[id]];c.groups[count]=iogs[id];
          c.positions[count*3]=xyz[id*3]+signs[id]*drift.distance;
          c.positions[count*3+1]=xyz[id*3+1];c.positions[count*3+2]=xyz[id*3+2];count++;
        }
        upload(c,count);diagnostics[name+'Points']=count;
      }
      diagnostics.clampedCandidates=clamped;diagnostics.rejectedCandidates=rejected;pairVersion=revision;
    }
    clouds.normal.points.visible=mode!=='only';
    clouds.beam.points.visible=mode!=='all'&&(source==='both'||source==='beam');
    clouds.light.points.visible=mode!=='all'&&(source==='both'||source==='light');
    holder.classList.toggle('offline',!fresh);
    controls.update();renderer.render(scene,camera);diagnostics.draws++;
  }
  return {render,reset,diagnostics,position:(name,id)=>{const c=clouds[name],at=c.lookup[id];return at<0?null:Array.from(c.positions.subarray(at*3,at*3+3));},
    visibility:()=>Object.fromEntries(Object.entries(clouds).map(([name,c])=>[name,c.points.visible]))};
}
