#!/usr/bin/env python3
"""Development-only generator using the pinned, actual Flow Geometry resource.

Requires PyYAML, scipy, h5flow and a local ndlar_flow checkout, never installed
on the DAQ host. See docs/3d_display.md. Does not read detector data or sockets.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from raw_display.geometry import load_geometry, PACMON_COMMIT
from raw_display.geometry3d import RECORD3D, sha256

FLOW_COMMIT = '5d63843212cd32d88de2c15d5ffa95b8d43b50a5'
GEOMETRY_CONFIG = 'yamls/proto_nd_flow/resources/GeometryData.yaml'
LAR_CONFIG = 'yamls/proto_nd_flow/resources/LArData.yaml'


def generate(flow_root, raw_directory, output):
    root = Path(flow_root).resolve()
    commit = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    if commit != FLOW_COMMIT:
        raise ValueError(f'Expected Flow {FLOW_COMMIT}, got {commit}; review before updating pin')
    if subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain', '--untracked-files=no'], text=True).strip():
        raise ValueError('Flow checkout must have no tracked modifications')
    sys.path.insert(0, str(root / 'src'))
    from proto_nd_flow.resources.geometry import Geometry
    from proto_nd_flow.resources.lar_data import LArData
    params = yaml.safe_load((root / GEOMETRY_CONFIG).read_text())['params']
    if params['drift_direction'] != 'x' or params['beam_direction'] != 'z' or not params['network_agnostic']:
        raise ValueError('Unexpected Flow coordinate/network convention')
    inputs = [GEOMETRY_CONFIG, LAR_CONFIG, params['det_geometry_file'], *params['crs_geometry_files'],
              'src/proto_nd_flow/resources/geometry.py', 'src/proto_nd_flow/resources/lar_data.py',
              'src/proto_nd_flow/util/lut.py', 'src/proto_nd_flow/util/units.py',
              'src/module0_flow/util/units.py', 'src/proto_nd_flow/reco/combined/drift_reco.py']
    detector = yaml.safe_load((root / params['det_geometry_file']).read_text())
    layouts = [yaml.safe_load((root / f).read_text()) for f in params['crs_geometry_files']]
    actual_params = {**params, 'det_geometry_file': str(root / params['det_geometry_file']),
                     'crs_geometry_files': [str(root / f) for f in params['crs_geometry_files']]}
    flow = Geometry(classname='Geometry', data_manager=None, **actual_params)
    flow._load_charge_geometry()  # no workflow, HDF5 output, light geometry or event reconstruction
    raw = load_geometry(raw_directory, range(1, 9))
    pixels = raw.pixels
    iog, chip, channel = (pixels[k].astype(int) for k in ('iog', 'chip', 'channel'))
    io = (pixels['tile'].astype(int) - 1) * 4 + 1
    ids = np.arange(len(pixels))
    tile = flow.tile_id[(iog, io)]
    zy = flow.pixel_coordinates_2D[(iog, io, chip, channel)] * 10.
    result = np.zeros(len(pixels), dtype=RECORD3D)
    result['x_anode_mm'] = flow.anode_drift_coordinate[(tile,)].reshape(-1) * 10.
    result['y_mm'], result['z_mm'] = zy[:, 1], zy[:, 0]
    result['drift_dir'] = flow.drift_dir[(tile,)].reshape(-1)
    if not all(np.isfinite(result[k]).all() for k in ('x_anode_mm', 'y_mm', 'z_mm')):
        raise ValueError('Not every raw pixel has a finite Flow coordinate')
    if not np.isin(result['drift_dir'], [-1, 1]).all():
        raise ValueError('Invalid Flow drift sign')
    for alias in range(4):
        if not np.array_equal(raw.lut[iog, io + alias, chip, channel], ids):
            raise ValueError('Raw Hydra alias identity mismatch')
        if not np.array_equal(flow.tile_id[(iog, io + alias)], tile):
            raise ValueError('Flow Hydra alias tile mismatch')
        if not np.array_equal(flow.pixel_coordinates_2D[(iog, io + alias, chip, channel)] * 10., zy):
            raise ValueError('Flow Hydra alias coordinates mismatch')

    # Independent direct-YAML address/rotation check for EVERY pixel, including
    # Flow's current assumption that all modules share the first layout's anodes.
    maximum_error = 0.
    for module in range(4):
        groups = detector['module_to_io_groups'][module + 1]
        if groups != [2 * module + 1, 2 * module + 2]:
            raise ValueError('Unexpected module-to-IOG mapping')
        layout = layouts[params['crs_geometry_to_module'][module]]
        pitch = float(layout['pixel_pitch'])
        grid = layout['chip_channel_to_position']
        size = np.ptp(np.array(list(grid.values())), axis=0) * pitch + pitch
        center = np.array(detector['tpc_offsets'][module]) * 10.
        for flow_tile, mapping in layout['tile_chip_to_io'].items():
            addresses = {(v // 1000 + module * 2, (v % 1000 - 1) // 4 + 1) for v in mapping.values()}
            if len(addresses) != 1:
                raise ValueError('Tile maps to multiple physical raw tiles')
            group, raw_tile = addresses.pop()
            use = (iog == group) & (pixels['tile'] == raw_tile)
            if not np.any(use):
                raise ValueError('Flow tile has no raw pixels')
            pos = np.array(layout['tile_positions'][flow_tile]) + center
            orient = layout['tile_orientations'][flow_tile]
            local = np.array([grid[int(c) * 1000 + int(ch)] for c, ch in zip(chip[use], channel[use])]) * pitch - size / 2 + pitch / 2
            expected = np.column_stack((np.full(np.sum(use), pos[0]), local[:, 1] * orient[1] + pos[1], local[:, 0] * orient[2] + pos[2]))
            actual = np.column_stack([result[k][use] for k in ('x_anode_mm', 'y_mm', 'z_mm')])
            error = float(np.max(np.abs(expected - actual)))
            maximum_error = max(maximum_error, error)
            if error > .0002 or not np.all(result['drift_dir'][use] == orient[0]):
                raise ValueError(f'Flow/direct YAML disagreement: module {module}, tile {flow_tile}')
            cathode = pos[0] + orient[0] * flow.max_drift_distance * 10.
            expected_cathode = center[0] - orient[0] * flow.cathode_thickness * 5.
            if not np.isclose(cathode, expected_cathode, atol=.0002):
                raise ValueError('Full drift does not reach cathode surface')

    lar = LArData(classname='LArData', data_manager=None)
    field_kv_cm, temperature = float(detector['e_field']), float(detector['temperature'])
    velocity = float(lar.electron_mobility(field_kv_cm / 10., temperature) * field_kv_cm / 10.)
    max_drift = float(flow.max_drift_distance * 10.)
    raw_iogs = {}
    for group in range(1, 9):
        indices = np.flatnonzero(iog == group)
        raw_iogs[str(group)] = dict(start=int(indices[0]), count=len(indices),
                                   raw_pixels_sha256=sha256(pixels[indices].tobytes()))
    sample_ids = sorted(set(int(t['start'] + offset) for t in raw.metadata['tiles'] for offset in (0, t['count']//2, t['count']-1)))
    samples = [dict(id=i, iog=int(iog[i]), io_channel=int(io[i]), chip=int(chip[i]), channel=int(channel[i]),
                    x_anode_mm=float(result['x_anode_mm'][i]), y_mm=float(result['y_mm'][i]),
                    z_mm=float(result['z_mm'][i]), drift_dir=int(result['drift_dir'][i])) for i in sample_ids]
    blob = result.tobytes()
    metadata = dict(version=1, record_bytes=16, n_pixels=len(result), coordinate_units='mm',
        axes=dict(x='drift', y='vertical', z='beam'), artifact_sha256=sha256(blob),
        flow_repository='DUNE/ndlar_flow', flow_ref='develop', flow_commit=commit,
        source_sha256={f: sha256((root / f).read_bytes()) for f in inputs},
        detector_geometry_input=params['det_geometry_file'], crs_geometry_inputs=params['crs_geometry_files'],
        raw_geometry_sources=raw.metadata['provenance'], pacmon_commit=PACMON_COMMIT, raw_iogs=raw_iogs,
        v_drift_mm_per_us=velocity, velocity_source='LArData.electron_mobility(E,T) * E; nominal field parameterization',
        e_field_v_per_cm=field_kv_cm * 1000., temperature_k=temperature,
        flow_configured_vdrift_mm_per_us=yaml.safe_load((root / LAR_CONFIG).read_text())['params'].get('vdrift'),
        max_drift_distance_mm=max_drift, max_drift_time_us=max_drift / velocity,
        max_drift_source='Geometry.max_drift_distance: CRS drift_length if present, else half anode spacing',
        detector_yaml_drift_length_mm=detector['drift_length'] * 10.,
        boundary_tolerance_mm=.5, cathode_thickness_mm=float(flow.cathode_thickness * 10.),
        detector_bounds_mm=(flow.lar_detector_bounds * 10.).tolist(),
        modules=[dict(module=m, bounds_mm=(bounds * 10.).tolist(),
                      cathode_x_mm=detector['tpc_offsets'][m][0] * 10.,
                      pixel_pitch_mm=float(flow.pixel_pitch[m] * 10.)) for m, bounds in enumerate(flow.module_RO_bounds)],
        validation=dict(mapped_pixels=len(result), electronics_aliases_checked=len(result)*4,
                        direct_yaml_max_error_mm=maximum_error, reference_samples=samples,
                        run3_file_comparison='not performed; no representative Run-3 Flow file supplied'))
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'pixels.bin').write_bytes(blob)
    (output / 'metadata.json').write_text(json.dumps(metadata, indent=2, sort_keys=True) + '\n')
    print(json.dumps({k: metadata[k] for k in ('n_pixels', 'artifact_sha256', 'v_drift_mm_per_us', 'max_drift_distance_mm', 'max_drift_time_us')}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--flow-root', required=True)
    parser.add_argument('--geometry-dir', default='layout')
    parser.add_argument('--out', default='raw_display/geometry3d')
    args = parser.parse_args()
    generate(args.flow_root, args.geometry_dir, args.out)
