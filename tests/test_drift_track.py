from pathlib import Path
import sys
import numpy as np
import pytest
from raw_display.drift_state import encode_drift_frame, RECORD
from raw_display.geometry3d import reconstruct
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from synthetic_3d import build_fixture


@pytest.fixture(scope='module')
def track():
    directory=Path(__file__).resolve().parents[1]/'layout'
    if not (directory/'geometry_mod0_v4.json').exists():
        pytest.skip('raw-display fetch-geometry is needed for the full-detector synthetic track')
    return build_fixture(directory)


def test_geometrical_track_through_decode_lookup_unroll_router_publication_and_binary(track):
    geo,g,shared,timing,expected=track
    current={name:(np.frombuffer(shared.trigger_source_seen[name]),np.frombuffer(shared.drift_ticks[name],dtype=np.int64)) for name in ('beam','light')}
    empty={name:(np.full(len(geo.pixels),-1.),np.full(len(geo.pixels),-1,dtype=np.int64)) for name in current}
    records=np.frombuffer(encode_drift_frame(empty,current,1),dtype=RECORD,offset=12)
    assert set(e['iog'] for e in expected)==set(range(1,9))
    assert len(expected)>=400
    assert len(records)==len(expected)*2
    ids=np.array([e['id'] for e in expected]);delta=np.array([e['dt_ticks'] for e in expected])
    targets=np.array([[e[k] for k in ('x_mm','y_mm','z_mm')] for e in expected])
    for name in ('beam','light'):
        np.testing.assert_array_equal(current[name][1][ids],delta)
        xyz,valid,_=reconstruct(g,ids,current[name][1][ids],np.zeros(len(ids),np.int64),timing.tick_seconds)
        assert valid.all()
        # Half of a 100 ns detector tick is the expected x quantization bound.
        assert np.max(np.abs(xyz[:,0]-targets[:,0]))<=g.metadata['v_drift_mm_per_us']*.05+.0001
        np.testing.assert_array_equal(xyz[:,1:],targets[:,1:])
