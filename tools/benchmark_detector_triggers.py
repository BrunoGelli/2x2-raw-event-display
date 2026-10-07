#!/usr/bin/env python3
"""Synthetic CPU benchmark; no sockets, disk data, hardware or geometry downloads.

Includes decoding, detector playback and optional epoch/router/history work.
Excludes fixture creation, geometry lookup, network, IPC snapshots and browser.
20 Hz light + 2 Hz beam are synthetic loads, not measured operating rates.
"""
import argparse
import json
import math
from pathlib import Path
import platform
import struct
import sys
import time
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from raw_display.codec import HEADER, decode_batch, make_message
from raw_display.timing import TimingConfig, DetectorPlayback
from raw_display.trigger_windows import ObserverConfig
from raw_display.detector_triggers import DetectorTriggerRouter

R, UNIX = 10_000_000, 1_790_000_000


def aux(kind, t):
    return struct.pack('<BB2xI8x', ord(kind), 83 if kind=='S' else 2, t)


def fixture(seconds, drain_messages):
    data=[]
    n_hits=drain_messages*8
    n_batches=math.ceil(77824/n_hits)
    for sec in range(seconds):
        batches=[]
        for b in range(n_batches):
            ts=(np.arange(n_hits)+b*n_hits)*R//(n_batches*n_hits)
            raw=bytearray(make_message(np.ones(n_hits,int),np.full(n_hits,11),
                np.arange(n_hits)%64,timestamp=ts,downstream=True)[8:])
            for k,t in enumerate(ts):
                struct.pack_into('<I',raw,16*k+2,int(t))
            frames=[HEADER.pack(b'D',UNIX+sec,8)+raw[k:k+128] for k in range(0,len(raw),128)]
            batches.append((frames,int(ts[n_hits//2])))
        data.append(batches)
    return data


def run(data, enabled, view3d=False):
    normal=np.full(8*64,-1.)
    tc=TimingConfig()
    clocks={i:DetectorPlayback(normal,tc) for i in range(1,9)}
    router=DetectorTriggerRouter(len(normal),range(1,9),tc,ObserverConfig(trigger_view=enabled,view3d=view3d))
    processed=0
    def feed(iog, frames, now):
        nonlocal processed
        h=decode_batch(frames)
        ids=(iog-1)*64+h.channel.astype(np.int32)
        c=clocks[iog]
        initial=(c.unroller.offset,c.unroller.initial_tick is not None)
        ticks,valid,selected=c.ingest(ids,h)
        router.record(iog,ids,h,ticks,selected,initial,c.unroller.frontier_tick,now)
        c.step(now);router.step(iog,c.cursor,now)
        processed+=len(ids)
    cpu=time.process_time();wall=time.perf_counter()
    for sec,batches in enumerate(data):
        for i in range(1,9):
            feed(i,[HEADER.pack(b'D',UNIX+sec,1)+aux('S',R)],10.+sec)
        for b,(frames,t) in enumerate(batches):
            now=10.+sec+(b+1)/len(batches)
            for i in range(1,9):
                feed(i,frames,now)
                hz=2 if i==5 else 20 if i==6 else 0
                if hz and (b+1)*hz//len(batches)>b*hz//len(batches):
                    feed(i,[HEADER.pack(b'D',UNIX+sec,1)+aux('T',t)],now)
    cpu=time.process_time()-cpu;wall=time.perf_counter()-wall
    assert all(c.stats['buffer_dropped_hits']==0 for c in clocks.values())
    assert router.aligner.generation==0
    histories=[m.summary() for m in router.matchers.values()]
    assert all(m['history_capacity_dropped_hits']==0 for m in histories)
    return normal,dict(enabled=enabled,view3d=view3d,processed_hits=processed,cpu_seconds=cpu,wall_seconds=wall,
        million_hits_per_cpu_second=processed/cpu/1e6,
        cpu_core_equivalent_at_fixture_rate=cpu/len(data),
        history_hits=sum(m['history_hits'] for m in histories),
        matched_hits=sum(m['matched_hits'] for m in histories),buffer_dropped_hits=0,
        history_capacity_dropped_hits=0)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--seconds',type=int,default=4)
    p.add_argument('--repeats',type=int,default=3)
    p.add_argument('--drain-messages',type=int,default=256)
    p.add_argument('--include-3d',action='store_true',help='Also benchmark optional hit/t0 pair association')
    a=p.parse_args()
    if not 2<=a.seconds<=20 or not 1<=a.repeats<=10 or not 1<=a.drain_messages<=256:
        p.error('seconds 2..20, repeats 1..10, drain-messages 1..256 required')
    data=fixture(a.seconds,a.drain_messages);results=[]
    for _ in range(a.repeats):
        off,r0=run(data,False);on,r1=run(data,True)
        np.testing.assert_array_equal(off,on)
        results.extend([r0,r1])
        if a.include_3d:
            paired,r2=run(data,True,True)
            np.testing.assert_array_equal(off,paired)
            results.append(r2)
    print(json.dumps(dict(python=platform.python_version(),numpy=np.__version__,
        platform=platform.platform(),simulated_seconds=a.seconds,
        simulated_aggregate_hits_per_second=len(data[0])*a.drain_messages*8*8,
        message_words=8,drain_messages=a.drain_messages,synthetic_light_hz=20,synthetic_beam_hz=2,
        normal_state_identical=True,results=results,
        caveat='CPU-only synthetic workload; not DAQ/PacMon acceptance or an upstream loss test'),indent=2))


if __name__=='__main__':
    main()
