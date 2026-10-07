#!/usr/bin/env python3
"""Read-only interval check of the EXISTING loopback HTTP service; no PACMAN sockets.

A software PASS is NOT a v1 release approval or a proof of common hardware phase.
Run after startup warmup. JSON output is created exclusively, never overwritten.
"""
import argparse
import json
import math
from pathlib import Path
import sys
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

FEATURE = 'detector-wide-2d-1'
MAX_RESPONSE = 1024*1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, 'redirects are not allowed', headers, fp)


def read_json(opener, port, route):
    with opener.open(f'http://127.0.0.1:{port}{route}', timeout=5) as response:
        raw = response.read(MAX_RESPONSE+1)
    if len(raw)>MAX_RESPONSE:
        raise ValueError('HTTP response exceeded 1 MiB limit')
    value=json.loads(raw)
    if not isinstance(value,dict):
        raise ValueError('expected JSON object')
    return value


def counters(value, prefix=''):
    """Only known cumulative counters, not clock frontiers or rate estimates."""
    names={'invalid_times','invalid_syncs','buffer_dropped_hits','underruns',
           'pacing_stalls','history_capacity_dropped_hits','association_resets',
           'window_limit_drops','matched_buffer_dropped_hits','invalid_trigger_times',
           'trigger_batch_limit_drops','uncertain_batch_triggers','source_unaligned_triggers',
           'unexpected_source_triggers','unaligned_target_skips','invalid_projected_ticks',
           'outside_history_triggers','inconsistent_observations','invalid_observations',
           'stale_transitions','malformed'}
    result={}
    if isinstance(value,dict):
        for key,v in value.items():
            path=prefix+'/'+str(key)
            if key in names and isinstance(v,(int,float)):
                result[path]=v
            elif isinstance(v,(dict,list)):
                result.update(counters(v,path))
    elif isinstance(value,list):
        for index,v in enumerate(value):
            key=v.get('iog',index) if isinstance(v,dict) else index
            result.update(counters(v,prefix+'/'+str(key)))
    return result


def evaluate(samples, iogs):
    if len(samples)<2:
        raise ValueError('at least two samples required')
    issues=set(); sessions=set(); offsets={}; last_capture=None
    for sample in samples:
        status,obs=sample['status'],sample['observers']
        if not status.get('collector_healthy'):
            issues.add('collector unhealthy')
        session=obs.get('session_id');sessions.add(session)
        if not session:
            issues.add('missing observer session')
        if obs.get('feature_build')!=FEATURE or obs.get('enabled') is not True:
            issues.add('wrong build or trigger view disabled')
        common=obs.get('common_timing',{})
        if not common.get('supported'):
            issues.add('PPS period unsupported')
        if set(s['iog'] for s in status.get('sources',[]))!=set(iogs):
            issues.add('status IOG set differs from geometry')
        age=obs.get('capture_age_s')
        if not isinstance(age,(int,float)) or not math.isfinite(age) or not 0<=age<2:
            issues.add('observer diagnostics stale/unknown')
        else:
            for src in common.get('sources',[]):
                i=src['iog'];old_age=src.get('last_valid_age_s')
                if (src.get('status')!='aligned' or not isinstance(old_age,(int,float)) or
                        not math.isfinite(old_age) or not 0<=old_age+age<=common.get('stale_after_s',0)):
                    issues.add(f'IOG {i} epoch not freshly qualified')
                off=src.get('offset_ticks')
                if type(off) is not int:
                    issues.add(f'IOG {i} has no integer epoch offset')
                elif i in offsets and offsets[i]!=off:
                    issues.add(f'IOG {i} offset changed')
                offsets[i]=off
        if set(s['iog'] for s in common.get('sources',[]))!=set(iogs):
            issues.add('alignment IOG set differs from geometry')
        capture=obs.get('captured_monotonic')
        if last_capture is not None and capture is not None and capture<last_capture:
            issues.add('observer capture clock went backwards')
        last_capture=capture
    if len(sessions)!=1:
        issues.add('collector session changed during capture')
    first,last=samples[0],samples[-1]
    before,after=counters(first),counters(last)
    increments={k:after[k]-before[k] for k in before.keys()&after.keys() if after[k]!=before[k]}
    if increments:
        issues.add('diagnostic counters changed; inspect counter_deltas')
    def groups(sample):
        return {s['iog']:s for s in sample['status'].get('sources',[])}
    a,b=groups(first),groups(last);rates=[]
    for i in sorted(set(a)&set(b)):
        row={'iog':i}
        for key in ('mapped_hits','display_selected_hits','triggers'):
            row['delta_'+key]=b[i].get(key,0)-a[i].get(key,0)
        ta,tb=a[i].get('timing',{}),b[i].get('timing',{})
        eligible=tb.get('timing_eligible_hits',0)-ta.get('timing_eligible_hits',0)
        late=tb.get('pre_cut_late_hits',0)-ta.get('pre_cut_late_hits',0)
        row['delta_pre_cut_late_hits']=late
        row['pre_cut_late_fraction']=late/eligible if eligible>0 else None
        rates.append(row)
    route_first={s['iog']:s for s in first['observers'].get('trigger_routing',{}).get('targets',[])}
    targets=[]
    for row in last['observers'].get('trigger_routing',{}).get('targets',[]):
        old=route_first.get(row['iog'],{})
        targets.append(dict(iog=row['iog'],beam=row.get('routed_beam',0)-old.get('routed_beam',0),
                            light=row.get('routed_light',0)-old.get('routed_light',0)))
    return dict(software_checks_passed=not issues,issues=sorted(issues),counter_deltas=increments,
                sample_count=len(samples),interval_seconds=last['elapsed_s']-first['elapsed_s'],
                iog_deltas=rates,routed_trigger_deltas=targets,
                v1_release_approved=False,
                manual_checks_remaining=[
                    'Independently verify common PPS cycle labels and relevant relative phase; stable header bias is not detectable here.',
                    'Observe both Beam and Light through all expected IOGs and browser controls; no-trigger intervals are inconclusive.',
                    'Review post-repair IOG6 lateness and independent DAQ/PacMon CPU, memory, rates and stability.',
                    'Run the complete checkout test suite and record operator acceptance before a v1 tag.'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',type=int,default=8765)
    p.add_argument('--seconds',type=int,default=120)
    p.add_argument('--interval',type=float,default=1.)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if not 1<=a.port<=65535 or not 2<=a.seconds<=600 or not math.isfinite(a.interval) or not 1<=a.interval<=a.seconds:
        p.error('port 1..65535, seconds 2..600, interval 1..seconds required')
    if a.output.exists():
        p.error('output already exists; choose a new filename')
    opener=build_opener(ProxyHandler({}),NoRedirect())
    samples=[];geometry={};error=None;start=time.monotonic()
    try:
        geometry=read_json(opener,a.port,'/api/geometry')
        if geometry.get('feature_build')!=FEATURE or geometry.get('trigger_view_enabled') is not True:
            raise ValueError('wrong feature build or trigger view disabled')
        while True:
            status=read_json(opener,a.port,'/api/status')
            obs=read_json(opener,a.port,'/api/observers')
            # The optional detailed late-audit payload is unnecessary here.
            obs.pop('audits',None)
            samples.append(dict(elapsed_s=time.monotonic()-start,status=status,observers=obs))
            if samples[-1]['elapsed_s']>=a.seconds:
                break
            time.sleep(min(a.interval,max(0.,a.seconds-samples[-1]['elapsed_s'])))
        summary=evaluate(samples,geometry.get('iogs',[]))
    except (OSError,ValueError,KeyError,TypeError) as exc:
        error=f'{type(exc).__name__}: {exc}'
        summary=dict(software_checks_passed=False,v1_release_approved=False,issues=[error])
    report=dict(summary=summary,geometry=geometry,samples=samples,
                note='Local HTTP diagnostics only. No PACMAN subscription or detector command.')
    with a.output.open('x',encoding='utf-8') as out:
        json.dump(report,out,indent=2,allow_nan=False);out.write('\n')
    print(json.dumps(summary,indent=2))
    return 0 if summary['software_checks_passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
