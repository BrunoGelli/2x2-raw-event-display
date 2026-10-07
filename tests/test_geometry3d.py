import json
from pathlib import Path
import numpy as np
import pytest
from raw_display.geometry import load_geometry
from raw_display.geometry3d import DEFAULT_DIRECTORY, RECORD3D, Geometry3D, load_geometry3d, reconstruct, sha256


@pytest.fixture(scope='module')
def artifact():
    meta=json.loads((DEFAULT_DIRECTORY/'metadata.json').read_text())
    blob=(DEFAULT_DIRECTORY/'pixels.bin').read_bytes()
    assert sha256(blob)==meta['artifact_sha256']
    return Geometry3D(np.frombuffer(blob,dtype=RECORD3D),meta)


def test_all_pixels_bounds_anodes_and_cathodes(artifact):
    a,m=artifact.pixels,artifact.metadata
    assert len(a)==m['n_pixels']==sum(v['count'] for v in m['raw_iogs'].values())
    assert m['axes']==dict(x='drift',y='vertical',z='beam')
    assert m['validation']['electronics_aliases_checked']==4*len(a)
    for name in ['x_anode_mm','y_mm','z_mm']:
        assert np.isfinite(a[name]).all()
    assert np.isin(a['drift_dir'],[-1,1]).all()
    for iog in range(1,9):
        group=m['raw_iogs'][str(iog)]; p=a[group['start']:group['start']+group['count']]
        module=m['modules'][(iog-1)//2]; lo,hi=module['bounds_mm']
        assert len(np.unique(p['x_anode_mm']))==1
        np.testing.assert_allclose(p['x_anode_mm']+p['drift_dir']*m['max_drift_distance_mm'],module['cathode_x_mm'],atol=1e-4)
        assert np.all((p['y_mm']>=lo[1])&(p['y_mm']<=hi[1]))
        assert np.all((p['z_mm']>=lo[2])&(p['z_mm']<=hi[2]))
        assert np.isclose(p['x_anode_mm'][0],lo[0]) or np.isclose(p['x_anode_mm'][0],hi[0])


def test_flow_reference_samples(artifact):
    for sample in artifact.metadata['validation']['reference_samples']:
        p=artifact.pixels[sample['id']]
        for field in ['x_anode_mm','y_mm','z_mm','drift_dir']:
            assert p[field]==sample[field]


@pytest.mark.parametrize('dt_us',[0,10,100,189.9,190.6])
def test_nominal_reconstruction_both_signs_all_iogs(artifact,dt_us):
    ids=[artifact.metadata['raw_iogs'][str(i)]['start'] for i in range(1,9)]
    dt=round(dt_us*10); t0=np.full(8,10**12,dtype=np.int64)
    xyz,valid,clamped=reconstruct(artifact,ids,t0+dt,t0)
    assert valid.all() and not clamped.any()
    p=artifact.pixels[ids]
    np.testing.assert_allclose(xyz[:,0],p['x_anode_mm']+p['drift_dir']*dt_us*artifact.metadata['v_drift_mm_per_us'],atol=1e-4)
    np.testing.assert_array_equal(xyz[:,1],p['y_mm'])
    np.testing.assert_array_equal(xyz[:,2],p['z_mm'])


def test_tiny_boundary_clamp_and_large_rejection(artifact):
    max_ticks=artifact.metadata['max_drift_time_us']*10
    dt=np.array([-1,round(max_ticks),round(max_ticks)+1,round(max_ticks)+100],np.int64)
    xyz,valid,clamped=reconstruct(artifact,np.zeros(4,int),dt,np.zeros(4,np.int64))
    assert valid.tolist()==[False,True,True,False]
    assert clamped.tolist()==[False,False,True,False]
    assert np.isnan(xyz[[0,3]]).all()


def test_pinned_raw_geometry_and_selected_iogs(artifact):
    directory=Path(__file__).resolve().parents[1]/'layout'
    if not (directory/'geometry_mod0_v4.json').exists():
        pytest.skip('run raw-display fetch-geometry for exhaustive electronics identity validation')
    raw=load_geometry(directory,range(1,9)); g=load_geometry3d(raw)
    np.testing.assert_array_equal(g.pixels,artifact.pixels)
    selected=load_geometry(directory,[2,5,8]); subset=load_geometry3d(selected)
    assert len(subset.pixels)==len(selected.pixels)
    for sample in artifact.metadata['validation']['reference_samples']:
        for alias in range(4):
            raw_id=raw.lut[sample['iog'],sample['io_channel']+alias,sample['chip'],sample['channel']]
            assert raw_id==sample['id']
    selected.metadata['provenance']['geometry_mod0_v4.json']='changed'
    with pytest.raises(ValueError,match='source layout mismatch'):
        load_geometry3d(selected)


def test_corrupt_artifact_rejected_before_use(tmp_path,artifact):
    (tmp_path/'metadata.json').write_text(json.dumps(artifact.metadata))
    (tmp_path/'pixels.bin').write_bytes(b'bad')
    with pytest.raises(ValueError,match='format/hash'):
        load_geometry3d(None,tmp_path)
