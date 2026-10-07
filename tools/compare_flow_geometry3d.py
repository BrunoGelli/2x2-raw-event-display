#!/usr/bin/env python3
"""Read-only comparison with stored Flow raw-hit geometry (not corrected hits).

Development tool: h5py and a pinned ndlar_flow checkout + h5flow are required.
Samples a contiguous block of hit->packet references. Uses a chosen synthetic
drift time to compare the SAME simple equation against stored Flow geometry;
it does not validate a real interaction t0 or calibrate beam/clock offsets.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import h5py
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from raw_display.geometry import load_geometry
from raw_display.geometry3d import load_geometry3d, reconstruct


def rows(dataset,indices):
    unique,inverse=np.unique(indices,return_inverse=True)
    return dataset[unique][inverse]


def compare(filename,flow_root,geometry_dir='layout',max_hits=100000,offset=0,dt_us=100.):
    raw=load_geometry(geometry_dir,range(1,9)); geometry=load_geometry3d(raw)
    root=Path(flow_root).resolve()
    commit=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    if commit!=geometry.metadata['flow_commit']:raise ValueError('Use the artifact-pinned Flow checkout for comparison')
    sys.path.insert(0,str(root/'src'))
    from proto_nd_flow.resources.geometry import Geometry
    from proto_nd_flow.util.lut import LUT
    reference=Geometry(classname='Geometry',data_manager=None)
    with h5py.File(filename,'r') as f:
        path='charge/raw_hits/ref/charge/packets/ref'
        if path in f:refs=f[path][offset:offset+max_hits]
        else:refs=f['charge/packets/ref/charge/raw_hits/ref'][offset:offset+max_hits][:,::-1]
        if not len(refs):raise ValueError('No raw-hit/packet references in requested sample')
        hits=rows(f['charge/raw_hits/data'],refs[:,0]);packets=rows(f['charge/packets/data'],refs[:,1])
        address=[packets[k].astype(np.int64) for k in ('io_group','io_channel','chip_id','channel_id')]
        iog,io,chip,channel=address
        in_range=(iog>=1)&(iog<=8)&(io>=1)&(io<=32)&(chip>=0)&(chip<256)&(channel>=0)&(channel<64)
        if not in_range.all():raise ValueError('File contains out-of-range electronics in sample; inspect before comparing')
        ids=raw.lut[tuple(address)]
        good=ids>=0
        if not good.any():raise ValueError('No sampled Flow addresses are present in the raw layout')
        p=geometry.pixels[ids[good]]
        ours=np.column_stack([p[k] for k in ('x_anode_mm','y_mm','z_mm')])
        stored=np.column_stack([hits[k][good] for k in ('x_pix','y_pix','z_pix')])*10.
        if not np.isfinite(stored).all():raise ValueError('Stored Flow raw-hit geometry has nonfinite values')
        position_error=float(np.max(np.abs(ours-stored)))
        for field in ('tile_id','anode_drift_coordinate','drift_dir'):
            group=f['geometry_info/'+field]
            setattr(reference,'_'+field,LUT.from_array(group.attrs['meta'],group['data'][:]))
        dt_ticks=int(round(dt_us*10));chosen_dt=dt_ticks*.1
        ours_xyz,valid,_=reconstruct(geometry,ids[good],np.full(good.sum(),dt_ticks),np.zeros(good.sum(),np.int64))
        if not valid.all():raise ValueError('Chosen comparison drift time exceeds physical display range')
        distance_cm=chosen_dt*geometry.metadata['v_drift_mm_per_us']/10.
        flow_x=reference.get_drift_coordinate(iog[good],io[good],np.full(good.sum(),distance_cm))*10.
        drift_error=float(np.max(np.abs(ours_xyz[:,0]-flow_x)))
        stored_v=np.asarray(f['lar_info'].attrs.get('v_drift',[])).tolist() if 'lar_info' in f else None
    return dict(sampled_hits=len(refs),mapped_hits=int(good.sum()),unmapped_hits=int((~good).sum()),
        maximum_anode_xyz_error_mm=position_error,maximum_basic_drift_x_error_mm=drift_error,
        chosen_dt_us=chosen_dt,nominal_v_drift_mm_per_us=geometry.metadata['v_drift_mm_per_us'],
        file_v_drift_mm_per_us=stored_v,
        passed=bool(position_error<=.0002 and drift_error<=.0002 and good.all()),
        note='No field/space-charge corrections or arbitrary offsets; chosen synthetic dt is not an observed t0.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file');parser.add_argument('--flow-root',required=True)
    parser.add_argument('--geometry-dir',default='layout')
    parser.add_argument('--max-hits',type=int,default=100000);parser.add_argument('--offset',type=int,default=0)
    parser.add_argument('--dt-us',type=float,default=100.)
    a=parser.parse_args()
    if not 1<=a.max_hits<=1000000 or a.offset<0 or not 0<=a.dt_us<=190:parser.error('Invalid sample size, offset or drift time')
    result=compare(a.file,a.flow_root,a.geometry_dir,a.max_hits,a.offset,a.dt_us)
    print(json.dumps(result,indent=2));sys.exit(0 if result['passed'] else 1)
