"""Static Flow-derived geometry. No Flow, YAML or HDF5 imports at runtime."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import numpy as np

RECORD3D = np.dtype([('x_anode_mm', '<f4'), ('y_mm', '<f4'), ('z_mm', '<f4'),
                     ('drift_dir', 'i1'), ('reserved', 'u1', (3,))])
DEFAULT_DIRECTORY = Path(__file__).with_name('geometry3d')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


@dataclass
class Geometry3D:
    pixels: np.ndarray
    metadata: dict


def load_geometry3d(raw_geometry, directory=DEFAULT_DIRECTORY):
    """Reject incompatible/corrupt artifacts; callers can keep serving 2D."""
    directory = Path(directory)
    meta = json.loads((directory / 'metadata.json').read_text())
    data = (directory / 'pixels.bin').read_bytes()
    if (meta.get('version') != 1 or meta.get('record_bytes') != RECORD3D.itemsize
            or meta.get('coordinate_units') != 'mm'
            or meta.get('axes') != {'x': 'drift', 'y': 'vertical', 'z': 'beam'}
            or len(data) != meta['n_pixels'] * RECORD3D.itemsize
            or sha256(data) != meta['artifact_sha256']):
        raise ValueError('3D geometry format/hash mismatch')
    records = np.frombuffer(data, dtype=RECORD3D)
    if (not all(np.isfinite(records[k]).all() for k in ('x_anode_mm', 'y_mm', 'z_mm'))
            or not np.isin(records['drift_dir'], [-1, 1]).all()):
        raise ValueError('3D geometry has invalid coordinates/drift signs')
    for key in ('v_drift_mm_per_us', 'max_drift_distance_mm', 'boundary_tolerance_mm'):
        if not np.isfinite(meta[key]) or meta[key] <= 0:
            raise ValueError('Invalid 3D geometry ' + key)
    if meta['boundary_tolerance_mm'] > .5:
        raise ValueError('3D boundary tolerance exceeds 0.5 mm')
    pieces = []
    for iog in raw_geometry.metadata['iogs']:
        group = meta['raw_iogs'][str(iog)]
        raw_pixels = raw_geometry.pixels[raw_geometry.pixels['iog'] == iog]
        if len(raw_pixels) != group['count'] or sha256(raw_pixels.tobytes()) != group['raw_pixels_sha256']:
            raise ValueError(f'3D pixel identity mismatch for IOG {iog}; regenerate geometry')
        filename = f'geometry_mod{(iog-1)//2}_v4.json'
        if raw_geometry.metadata['provenance'].get(filename) != meta['raw_geometry_sources'][filename]:
            raise ValueError(f'3D source layout mismatch for IOG {iog}; regenerate geometry')
        pieces.append(records[group['start']:group['start'] + group['count']])
    pixels = np.concatenate(pieces)
    if len(pixels) != len(raw_geometry.pixels):
        raise ValueError('3D pixel count mismatch')
    selected_modules = {(i - 1) // 2 for i in raw_geometry.metadata['iogs']}
    return Geometry3D(pixels, {**meta, 'n_pixels': len(pixels),
        'geometry_id': raw_geometry.metadata['geometry_id'],
        'served_sha256': sha256(pixels.tobytes()),
        'modules': [m for m in meta['modules'] if m['module'] in selected_modules]})


def reconstruct(geometry, ids, hit_ticks, t0_ticks, tick_seconds=1e-7):
    """Offline reference used by validation tools. Subtract integer ticks first.

    Returns positions, validity and tiny-boundary-clamp masks. Invalid points
    become NaN, never plausible points on a cathode or anode.
    """
    p = geometry.pixels[np.asarray(ids)]
    dt = np.asarray(hit_ticks, dtype=np.int64) - np.asarray(t0_ticks, dtype=np.int64)
    distance = dt * (tick_seconds * 1e6 * geometry.metadata['v_drift_mm_per_us'])
    maximum = geometry.metadata['max_drift_distance_mm']
    valid = (dt >= 0) & np.isfinite(distance) & (distance <= maximum + geometry.metadata['boundary_tolerance_mm'])
    clamped = valid & (distance > maximum)
    xyz = np.column_stack((p['x_anode_mm'] + p['drift_dir'] * np.minimum(distance, maximum), p['y_mm'], p['z_mm']))
    xyz[~valid] = np.nan
    return xyz, valid, clamped
