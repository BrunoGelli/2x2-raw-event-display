#!/usr/bin/env python3
"""Two synthetic tracks across all four modules, through the actual backend.

No sockets or hardware in build_fixture(). CLI serves a static synthetic snapshot
on loopback for camera/renderer inspection; it creates no PACMAN collector.
"""
import argparse
import multiprocessing as mp
from pathlib import Path
import struct
import sys
import time
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from raw_display.codec import HEADER, decode_batch, make_message
from raw_display.geometry import load_geometry
from raw_display.geometry3d import load_geometry3d
from raw_display.runtime import make_shared, FIELDS, COL
from raw_display.timing import TimingConfig, DetectorPlayback, TIMING_FIELDS, TIMING_COL
from raw_display.trigger_windows import ObserverConfig
from raw_display.observer_runtime import attach_observers, Observers


def build_fixture(geometry_dir='layout'):
    geo=load_geometry(geometry_dir,range(1,9)); g=load_geometry3d(geo)
    ctx=mp.get_context('spawn'); shared=make_shared(ctx,len(geo.pixels))
    config=TimingConfig(); cfg=ObserverConfig(trigger_view=True,view3d=True)
    attach_observers(ctx,shared,len(geo.pixels),range(1,9),cfg)
    shared.timing=ctx.RawArray('d',9*len(TIMING_FIELDS));shared.cpu_ratio=ctx.RawValue('d',0.)
    shared.rate_ring=None
    normal=np.full(len(geo.pixels),-1.)
    clocks={i:DetectorPlayback(normal,config) for i in range(1,9)}
    observers=Observers(len(normal),range(1,9),config,cfg,shared)
    period=config.rollover_ticks; unix=int(time.time())-2; now=time.monotonic()
    def aux(kind,t):return struct.pack('<BB2xI8x',ord(kind),83 if kind=='S' else 2,t)
    def feed(iog,second,words):
        clock=clocks[iog]; initial=(clock.unroller.offset,clock.unroller.initial_tick is not None)
        hits=decode_batch([HEADER.pack(b'D',second,len(words)//16)+words])
        ids=geo.lookup(iog,hits); result=clock.ingest(ids,hits)
        observers.record(iog,ids,hits,result,clock,initial,now)
    # Different local epoch origins but the same physical labelled PPS.
    for iog in range(1,9):
        for second in range(unix-(iog%3),unix+2):feed(iog,second,aux('S',period))
    t0_phase=100000
    feed(5,unix+1,aux('T',t0_phase));feed(6,unix+1,aux('T',t0_phase))
    expected=[]
    for iog in range(1,9):
        ids=np.flatnonzero(geo.pixels['iog']==iog); p=g.pixels[ids]
        module=(iog-1)//2; center=g.metadata['modules'][module]['cathode_x_mm']
        anode=float(p['x_anode_mm'][0]);direction=int(p['drift_dir'][0])
        # Known x points. y/z are snapped to the nearest physical readout pixel;
        # this quantized geometric target is independent of the timing pipeline.
        points=np.linspace(anode+direction*3,center-direction*3,55)
        packet_words=[]; used=set()
        for x in points:
            y=.8*x;z=335. if module in (0,2) else -335.
            local=int(np.argmin((p['y_mm']-y)**2+(p['z_mm']-z)**2));pid=int(ids[local])
            if pid in used:continue
            used.add(pid)
            distance=(x-anode)*direction
            delta=int(round(distance/g.metadata['v_drift_mm_per_us']/(config.tick_seconds*1e6)))
            if not 0<=delta<1900:raise ValueError('Synthetic target outside default trigger window')
            raw=geo.pixels[pid];phase=t0_phase+delta
            word=bytearray(make_message((int(raw['tile'])-1)*4+1,int(raw['chip']),int(raw['channel']),timestamp=phase,downstream=True)[8:])
            struct.pack_into('<I',word,2,phase);packet_words.append(word)
            expected.append(dict(id=pid,iog=iog,dt_ticks=delta,x_mm=float(x),y_mm=float(p['y_mm'][local]),z_mm=float(p['z_mm'][local])))
        feed(iog,unix+1,b''.join(packet_words))
        # Advance the real buffered playback with synthetic detector progress.
        clock=clocks[iog]
        clock.unroller.frontier_tick=clock.unroller.offset+2*period
        clock.cursor=(clock.unroller.offset+t0_phase+1900)*config.tick_seconds
        clock.running=False;clock.step(now)
        observers.step(iog,clock.cursor,now)
        summary=clock.summary()
        np.frombuffer(shared.timing).reshape(9,-1)[iog]=[summary[k] for k in TIMING_FIELDS]
        metrics=np.frombuffer(shared.metrics).reshape(9,-1)
        metrics[iog,COL['last_rx']]=now;metrics[iog,COL['mapped_hits']]=len(used)
    # Freeze snapshot clocks explicitly; this demo is NOT real-time telemetry.
    np.frombuffer(shared.timing).reshape(9,-1)[:,TIMING_COL['running']]=0
    np.frombuffer(shared.seen)[:]=normal
    with shared.lock:observers.publish_state()
    shared.heartbeat.value=now;observers.publish_diagnostics(now)
    return geo,g,shared,config,expected


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--geometry-dir',default='layout')
    parser.add_argument('--port',type=int,default=8766)
    args=parser.parse_args()
    geo,g,shared,config,expected=build_fixture(args.geometry_dir)
    from raw_display.timed_server import create_timed_app
    import uvicorn
    class Synthetic:
        def is_alive(self):return False
    print(f'SYNTHETIC STATIC SNAPSHOT: {len(expected)} points. No collector or PACMAN connections.')
    app=create_timed_app(geo,shared,Synthetic(),config,geometry3d=g,mode='SYNTHETIC')
    uvicorn.run(app,host='127.0.0.1',port=args.port,access_log=False)
