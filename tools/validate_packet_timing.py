#!/usr/bin/env python3
"""Compare streaming unrolling with Flow's array formula on a packet HDF5 sample.

A local path or HTTPS FILE URL is accepted. Remote HDF5 is read with bounded,
ETag-checked HTTP ranges; a server ignoring Range is refused, not fully downloaded.
Requires h5py only for this offline tool, never for the live collector.
"""
import argparse
from collections import OrderedDict
import io
import json
import urllib.request
import numpy as np
from raw_display.timing import TimingConfig, TimestampUnroller


class RangeFile(io.RawIOBase):
    block_size = 1024*1024
    def __init__(self, url, budget=256*1024*1024):
        if not url.startswith('https://'):
            raise ValueError('remote source must be HTTPS')
        self.url, self.pos, self.downloaded, self.budget = url,0,0,budget
        self.cache=OrderedDict()
        with urllib.request.urlopen(urllib.request.Request(url,method='HEAD'),timeout=30) as r:
            self.size=int(r.headers['Content-Length'])
            self.etag=r.headers.get('ETag')
        if self.size<=0: raise ValueError('remote file is empty')
    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.pos
    def seek(self, offset, whence=0):
        target=offset if whence==0 else self.pos+offset if whence==1 else self.size+offset
        if whence not in (0,1,2) or target<0: raise ValueError('invalid seek')
        self.pos=target
        return target
    def _block(self,k):
        if k in self.cache:
            self.cache.move_to_end(k)
            return self.cache[k]
        lo=k*self.block_size;hi=min(self.size,lo+self.block_size)-1
        if self.downloaded+hi-lo+1>self.budget:
            raise ValueError('remote-read byte budget exceeded; use a local file or smaller sample')
        headers={'Range':f'bytes={lo}-{hi}'}
        if self.etag: headers['If-Match']=self.etag
        with urllib.request.urlopen(urllib.request.Request(self.url,headers=headers),timeout=30) as r:
            if r.status!=206 or r.headers.get('Content-Range')!=f'bytes {lo}-{hi}/{self.size}':
                raise ValueError('server did not honor exact HTTP range; refusing a full download')
            if self.etag and r.headers.get('ETag')!=self.etag:
                raise ValueError('remote file changed during validation')
            block=r.read(hi-lo+2)
        if len(block)!=hi-lo+1: raise ValueError('short/oversized range response')
        self.downloaded+=len(block);self.cache[k]=block
        while len(self.cache)>8:self.cache.popitem(last=False)
        return block
    def read(self,n=-1):
        if n<0:n=max(0,self.size-self.pos)
        n=min(n,max(0,self.size-self.pos))
        parts=[]
        while n:
            block=self._block(self.pos//self.block_size)
            off=self.pos%self.block_size
            take=min(n,len(block)-off)
            parts.append(block[off:off+take]);self.pos+=take;n-=take
        return b''.join(parts)
    def readinto(self,b):
        data=self.read(len(b));b[:len(data)]=data;return len(data)


def validate(packets, config, chunk_rows=50000):
    required={'packet_type','io_group','timestamp','receipt_timestamp','trigger_type'}
    if not required<=set(packets.dtype.names or []):
        raise ValueError('packet dataset lacks fields required by Flow unrolling')
    reports={}
    for iog in np.unique(packets['io_group']):
        if not 1<=iog<=8:continue
        p=packets[packets['io_group']==iog]
        data=p['packet_type']==0
        if 'valid_parity' in p.dtype.names:data &= p['valid_parity'].astype(bool)
        sm=(p['packet_type']==6)&(p['trigger_type']==config.sync_type)
        increments=np.zeros(len(p),dtype=np.int64)
        increments[sm]=np.rint(p['timestamp'][sm].astype(np.float64)/config.rollover_ticks).astype(np.int64)*config.rollover_ticks
        offsets=np.cumsum(increments)-increments
        sync_at=np.where(sm,np.arange(len(p)),0)
        last=increments[np.maximum.accumulate(sync_at)]
        reference=(p['timestamp'].astype(np.int64)%config.rollover_ticks+offsets-
                   ((p['receipt_timestamp']<p['timestamp'])&data)*last)
        u=TimestampUnroller(config);n=mismatch=0
        for lo in range(0,len(p),chunk_rows):
            hi=min(len(p),lo+chunk_rows);block=p[lo:hi]
            di=np.flatnonzero(data[lo:hi]);si=np.flatnonzero(sm[lo:hi])
            ticks,valid=u.consume_arrays(block['timestamp'][di],block['receipt_timestamp'][di],
                                         di,si,block['timestamp'][si])
            n+=int(np.count_nonzero(valid))
            mismatch+=int(np.count_nonzero(ticks[valid]!=reference[lo+di[valid]]))
        reports[int(iog)]=dict(compared_hits=n,mismatches=mismatch,**u.stats)
    return reports


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('source',help='local packet HDF5 path or HTTPS file URL, not a directory')
    p.add_argument('--max-packets',type=int,default=2_000_000)
    p.add_argument('--chunk-rows',type=int,default=50000)
    p.add_argument('--rollover-ticks',type=int,default=10_000_000)
    p.add_argument('--sync-type',type=int,default=83)
    args=p.parse_args()
    if not 1<=args.max_packets<=10_000_000 or args.chunk_rows<1:
        p.error('invalid sample/chunk size')
    try:
        import h5py
    except ImportError:
        p.error('offline validation requires h5py: python -m pip install h5py')
    remote=RangeFile(args.source) if args.source.startswith('https://') else None
    try:
        with h5py.File(remote if remote is not None else args.source,'r') as f:
            rows=f['packets'][:args.max_packets]
        result=validate(rows,TimingConfig(rollover_ticks=args.rollover_ticks,sync_type=args.sync_type),args.chunk_rows)
        print(json.dumps(dict(source=args.source,rows=len(rows),groups=result,
                              downloaded_bytes=remote.downloaded if remote else 0),indent=2))
        if not result or any(r['compared_hits']==0 for r in result.values()):
            raise SystemExit('No post-SYNC comparison for at least one group; enlarge the sample')
        if any(r['mismatches'] or r['invalid_syncs'] or r['invalid_times'] for r in result.values()):
            raise SystemExit('Timing mismatch or invalid metadata; review before interpreting playback')
    finally:
        if remote: remote.close()

if __name__=='__main__': main()
