#!/usr/bin/env python3
"""Two loopback HTTP snapshots; no ZMQ, detector access, or server is started."""
import argparse
import json
import math
import time
import urllib.parse
import urllib.request


def fetch(origin):
    parts=urllib.parse.urlsplit(origin)
    if parts.scheme!='http' or parts.hostname!='127.0.0.1' or parts.username or parts.password or parts.path not in ('','/') or parts.query or parts.fragment:
        raise ValueError('--url must be a loopback origin such as http://127.0.0.1:8765')
    with urllib.request.urlopen(origin.rstrip('/')+'/api/timing-audit',timeout=5) as response:
        raw=response.read(512001)
    if len(raw)>512000:raise ValueError('Diagnostic response exceeded its bound')
    data=json.loads(raw)
    if not data.get('session_id') or data.get('capture_age_s') is None or data['capture_age_s']>5:
        raise ValueError('No fresh audit snapshot. Enable RAW_DISPLAY_LATE_AUDIT_IOGS on the existing collector.')
    return data


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url',default='http://127.0.0.1:8765')
    p.add_argument('--seconds',type=float,default=15.)
    p.add_argument('--iog',type=int,default=6)
    p.add_argument('--json-out',help='Optional local report path')
    a=p.parse_args()
    if not math.isfinite(a.seconds) or not 1<=a.seconds<=600 or a.iog not in range(1,9):p.error('invalid interval or IO group')
    try:
        before=fetch(a.url)
        if a.iog not in before.get('enabled_iogs',[]):raise ValueError(f'IOG {a.iog} audit is not enabled')
        time.sleep(a.seconds)
        after=fetch(a.url)
        if before['session_id']!=after['session_id']:raise ValueError('Collector restarted during measurement; retry')
        b=next(d for d in before['audits'] if d['iog']==a.iog)
        e=next(d for d in after['audits'] if d['iog']==a.iog)
        dt=after['captured_monotonic']-before['captured_monotonic']
        if dt<=0:raise ValueError('Audit snapshot did not advance')
        keys=['selected_hits','late_hits','raw_ge_period','late_raw_ge_period','receipt_ge_period','crossed_raw_receipt','late_crossed_raw_receipt']
        def delta(old,new):
            d={k:new[k]-old[k] for k in keys}
            if any(v<0 for v in d.values()):raise ValueError('Counters decreased; retry after a stable interval')
            d['late_fraction']=d['late_hits']/d['selected_hits'] if d['selected_hits'] else None
            return d
        totals=delta(b['totals'],e['totals'])
        tiles=[dict(tile=x['tile'],**delta(y,x)) for x,y in zip(e['tiles'],b['tiles'])]
        report=dict(iog=a.iog,interval_s=dt,totals=totals,tiles=tiles,
                    latest_cumulative_top_late_chips=e['top_late_chips'],recent_late_samples=e['samples'],
                    histogram_labels=e['histogram_labels'],
                    interval_lateness_counts=[x-y for x,y in zip(e['lateness_counts'],b['lateness_counts'])],
                    interval_inferred_asic_to_receipt_counts=[x-y for x,y in zip(e['inferred_asic_to_receipt_counts'],b['inferred_asic_to_receipt_counts'])],
                    caveat=e['caveat'])
        print(f"IOG {a.iog}; {dt:.2f} s of collector diagnostics. Counts are post-cut, timing-valid mapped hits.")
        print('Tile    selected      late     late %   raw>=period   late raw>=period   receipt>=period')
        for d in tiles:
            percent='    n/a' if d['late_fraction'] is None else f"{100*d['late_fraction']:7.2f}"
            print(f"{d['tile']:4d} {d['selected_hits']:11d} {d['late_hits']:9d} {percent} {d['raw_ge_period']:13d} {d['late_raw_ge_period']:18d} {d['receipt_ge_period']:17d}")
        print('\nLargest cumulative late-hit contributors:')
        for d in e['top_late_chips'][:10]:
            print(f"tile {d['tile']} chip {d['chip']}: late={d['late_hits']} selected={d['selected_hits']}; late raw>=period={d['late_raw_ge_period']}")
        print('\nRecent late samples (a bounded diagnostic subset, not an unbiased distribution):')
        print(json.dumps(e['samples'],indent=2))
        if a.json_out:
            with open(a.json_out,'x') as f:json.dump(report,f,indent=2)
        print('\nNo clock corrections were applied. Inferred lag can reflect an epoch-model error, not actual transport delay.')
    except (ValueError,OSError,KeyError,StopIteration) as exc:p.exit(1,f'ERROR: {exc}\n')

if __name__=='__main__':main()
