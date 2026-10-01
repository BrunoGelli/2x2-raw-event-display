#!/usr/bin/env python3
"""Offline comparison using the SAME synthetic wire batches, eight IO groups.

Includes decode/parity/LUT, and for the timed path: streaming PPS unrolling,
buffering, late-hit handling and playback state publication work. Excludes ZMQ,
shared-memory copy, HTTP, browser/VNC. Fixture generation is not timed.
"""
import argparse
import json
import platform
import struct
import time
import numpy as np
from raw_display.codec import HEADER, WORD, make_message, decode_batch
from raw_display.geometry import demo_geometry, load_geometry
from raw_display.timing import TimingConfig, DetectorPlayback


def fixtures(geo, seconds, total_rate, words_per_message, batch_messages):
    groups = geo.metadata['iogs']
    rate = total_rate/len(groups)
    n = words_per_message*batch_messages
    rng = np.random.default_rng(104)
    pools = {i: np.flatnonzero(geo.pixels['iog']==i) for i in groups}
    batches = []
    waves = max(1,int(np.ceil(seconds*rate/n)))
    for wave in range(waves):
        ticks = np.floor((np.arange(n)+wave*n)*10_000_000/rate).astype(np.int64)
        cuts = np.r_[0, np.flatnonzero(np.diff(ticks//10_000_000))+1, n]
        for iog in groups:
            px = geo.pixels[rng.choice(pools[iog], n)]
            raw = make_message((px['tile'].astype(int)-1)*4+1, px['chip'], px['channel'],
                               timestamp=ticks%10_000_000, downstream=True)
            data = np.frombuffer(raw[8:],dtype=WORD).copy()
            data['receipt'] = ticks%10_000_000
            parts=[]
            for lo,hi in zip(cuts[:-1],cuts[1:]):
                if wave==0 and lo==0 or lo>0 or lo==0 and ticks[0]//10_000_000 != (ticks[0]-int(10_000_000/rate))//10_000_000:
                    parts.append(struct.pack('<2cxxI8x',b'S',b'S',10_000_000))
                parts.append(data[lo:hi].tobytes())
            body=b''.join(parts)
            frames=[HEADER.pack(b'D',0,len(body[j:j+16*words_per_message])//16)+body[j:j+16*words_per_message]
                    for j in range(0,len(body),16*words_per_message)]
            batches.append((iog, frames, (wave+1)*n/rate))
    return batches


def run(geo, batches, timed):
    state = np.full(len(geo.pixels),-1.)
    clocks = {i:DetectorPlayback(state,TimingConfig()) for i in geo.metadata['iogs']} if timed else {}
    hits_total=0; pub_at=0.
    start=time.perf_counter()
    for iog, frames, now in batches:
        hits=decode_batch(frames,strict=True)
        ids=geo.lookup(iog,hits)
        hits_total+=len(ids)
        if timed:
            clocks[iog].ingest(ids,hits)
            if now-pub_at>=.1:
                for c in clocks.values(): c.step(now)
                pub_at=now
        else:
            state[ids]=now
    elapsed=time.perf_counter()-start
    return dict(seconds=elapsed,hits=hits_total,hits_per_second=hits_total/elapsed,
                peak_pending_hits=sum(c.stats['peak_pending_hits'] for c in clocks.values()),
                buffer_drops=sum(c.stats['buffer_dropped_hits'] for c in clocks.values()))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--geometry-dir')
    p.add_argument('--seconds',type=float,default=4.)
    p.add_argument('--rate',type=float,default=622000.)
    p.add_argument('--words',type=int,default=8)
    p.add_argument('--batch-messages',type=int,default=256)
    p.add_argument('--repeats',type=int,default=3)
    args=p.parse_args()
    if not (0<args.seconds<=30 and 0<args.rate<=2e6 and 1<=args.words<=1024 and
            1<=args.batch_messages<=1024 and args.words*args.batch_messages<=65535 and 1<=args.repeats<=10):
        p.error('benchmark parameters exceed safety limits')
    geo=load_geometry(args.geometry_dir,range(1,9)) if args.geometry_dir else demo_geometry()
    batches=fixtures(geo,args.seconds,args.rate,args.words,args.batch_messages)
    results=[]
    for _ in range(args.repeats):
        baseline=run(geo,batches,False);timed=run(geo,batches,True)
        results.append(dict(baseline=baseline,asic_timing=timed,
                            overhead_fraction=timed['seconds']/baseline['seconds']-1))
    print(json.dumps(dict(python=platform.python_version(),numpy=np.__version__,
                         workload=vars(args),results=results,
                         caveat='synthetic offline CPU comparison, not DAQ-machine or end-to-end throughput'),indent=2))

if __name__=='__main__': main()
