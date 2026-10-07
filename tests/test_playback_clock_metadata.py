"""Endpoint-body unit tests; synthetic state, not an HTTP/collector integration."""
import ast
import copy
from pathlib import Path
from types import SimpleNamespace
import numpy as np


def endpoint(name, namespace):
    path = Path(__file__).resolve().parents[1]/'raw_display'/'timed_server.py'
    tree = ast.parse(path.read_text())
    outer = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'create_timed_app')
    function = copy.deepcopy(next(n for n in outer.body if isinstance(n, ast.FunctionDef) and n.name == name))
    function.decorator_list = []
    unit = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    exec(compile(unit, str(path), 'exec'), namespace)
    return namespace[name]


def setup():
    fields = ('mapped_hits','last_rx','last_trigger','last_sync83')
    stats = np.zeros((9,len(fields)))
    times = np.zeros((9,2)); times[6] = [38.,39.25]
    observers = dict(session_id='one',captured_monotonic=99.8,
                     common_timing=dict(sources=[dict(iog=6,status='aligned')]))
    wall = [1791338400.3]
    ns = dict(geometry=SimpleNamespace(metadata=dict(iogs=[6])),
              FIELDS=fields,COL={k:j for j,k in enumerate(fields)},
              TIMING_FIELDS=('cursor_s','frontier_s'),
              hub=dict(stats=stats,timing=times,heartbeat=99.9,cpu=.1,clients=1,clients3d=0,
                       drift_bytes_sent=0,drift_transport_errors=0,websocket_bytes_sent=0),
              enabled3d=False,geometry3d=None,geometry3d_error=None,mode='LIVE',
              time=SimpleNamespace(monotonic=lambda:100.,time=lambda:wall[0]),
              shared=object(),read_observers=lambda _:observers,
              proc=SimpleNamespace(is_alive=lambda:True),config=SimpleNamespace(min_raw_timestamp=10))
    return ns,wall,observers


def test_server_wall_time_is_metadata_only_not_assigned_to_hit_or_cursor():
    ns,wall,observers=setup(); fn=endpoint('status',ns)
    first=fn(); wall[0]+=1; second=fn()
    assert first['server_unix_s']+1==second['server_unix_s']
    assert first['sources']==second['sources']
    assert first['sources'][0]['timing']['cursor_s']==38.
    assert first['trigger_alignment']['session_id']=='one'
    assert first['trigger_alignment']['common_timing'] is observers['common_timing']
    assert first['collector_healthy']


def test_clock_feature_marker_does_not_replace_trigger_protocol():
    ns,_,_=setup()
    ns.update(FEATURE_BUILD='detector-wide-2d-1',TRIGGER_SOURCES={5:'beam',6:'light'},
              asdict=lambda _:dict(tick_seconds=1e-7,rollover_ticks=10_000_000),frame_hz=10.)
    data=endpoint('metadata',ns)()
    assert data['playback_clock']=='pps-playhead-1'
    assert data['feature_build']=='detector-wide-2d-1'
    assert data['protocol']==2 and data['trigger_source_protocol']==1
    assert data['trigger_sources']=={'beam':5,'light':6}
