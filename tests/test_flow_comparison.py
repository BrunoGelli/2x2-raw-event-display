"""Development-only check of the file comparator with actual pinned Flow LUTs.

The generated HDF5 fixture is explicitly synthetic, not a Run-3 data sample.
"""
import json
import os
from pathlib import Path
import sys

import numpy as np
import pytest


@pytest.mark.skipif(not os.environ.get('RAW_DISPLAY_FLOW_ROOT'),
                    reason='optional pinned Flow development checkout')
def test_stored_flow_geometry_comparator(tmp_path):
    h5py = pytest.importorskip('h5py')
    yaml = pytest.importorskip('yaml')
    root = Path(os.environ['RAW_DISPLAY_FLOW_ROOT']).resolve()
    sys.path.insert(0, str(root / 'src'))
    from proto_nd_flow.resources.geometry import Geometry
    from raw_display.geometry3d import DEFAULT_DIRECTORY
    from tools.compare_flow_geometry3d import compare

    params = yaml.safe_load((root / 'yamls/proto_nd_flow/resources/GeometryData.yaml').read_text())['params']
    params['det_geometry_file'] = str(root / params['det_geometry_file'])
    params['crs_geometry_files'] = [str(root / p) for p in params['crs_geometry_files']]
    flow = Geometry(classname='Geometry', data_manager=None, **params)
    flow._load_charge_geometry()
    samples = json.loads((DEFAULT_DIRECTORY / 'metadata.json').read_text())['validation']['reference_samples']
    packets = np.array([(s['iog'], s['io_channel'] + alias, s['chip'], s['channel'])
                        for s in samples for alias in range(4)],
                       dtype=[(k, 'i4') for k in ('io_group', 'io_channel', 'chip_id', 'channel_id')])
    iog, io, chip, channel = [packets[k] for k in packets.dtype.names]
    tiles = flow.tile_id[(iog, io)]
    zy = flow.pixel_coordinates_2D[(iog, io, chip, channel)]
    hits = np.zeros(len(packets), dtype=[(k, 'f8') for k in ('x_pix', 'y_pix', 'z_pix')])
    hits['x_pix'] = flow.anode_drift_coordinate[(tiles,)].reshape(-1)
    hits['y_pix'], hits['z_pix'] = zy[:, 1], zy[:, 0]
    order = np.random.default_rng(17).permutation(len(hits))
    refs = np.column_stack((order, order))
    path = tmp_path / 'synthetic-flow-reference.h5'
    with h5py.File(path, 'w') as f:
        f.create_dataset('charge/raw_hits/data', data=hits)
        f.create_dataset('charge/packets/data', data=packets)
        f.create_dataset('charge/raw_hits/ref/charge/packets/ref', data=refs)
        for name in ('tile_id', 'anode_drift_coordinate', 'drift_dir'):
            meta, data = getattr(flow, name).to_array()
            group = f.create_group('geometry_info/' + name)
            group.attrs['meta'] = meta
            group.create_dataset('data', data=data)
        f.create_group('lar_info').attrs['v_drift'] = [1.583]
    result = compare(path, root)
    assert result['passed'] and result['mapped_hits'] == 768
    assert result['file_v_drift_mm_per_us'] == [1.583]
    # Reverse reference direction and bounded sampling must also work.
    with h5py.File(path, 'a') as f:
        del f['charge/raw_hits/ref/charge/packets/ref']
        f.create_dataset('charge/packets/ref/charge/raw_hits/ref', data=refs[:, ::-1])
    result = compare(path, root, max_hits=113, offset=19, dt_us=189.9)
    assert result['passed'] and result['sampled_hits'] == 113
    # A millimetre-scale mapping error must fail, never be fitted away.
    with h5py.File(path, 'a') as f:
        hits['x_pix'] += .1
        f['charge/raw_hits/data'][:] = hits
    result = compare(path, root)
    assert not result['passed'] and result['maximum_anode_xyz_error_mm'] > .99
