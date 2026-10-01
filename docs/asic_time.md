# ASIC-time 2D playback: implementation and validation

## Scope

Live rendering now uses ASIC timestamps. It does not perform noise identification,
light/beam selection, absolute epoch synchronization or drift reconstruction.
No change to the PACMAN endpoint file, port, socket type, HWM, drain limit or
command/control behavior is required. Timing computation adds CPU/memory work;
unchanged ZMQ settings are not a guarantee of zero acquisition impact.

## Time unrolling

The implementation refers to `DUNE/ndlar_flow`, commit
`a0eb2f364e35340d67fd73dc09a8e8f847211a58`,
`src/proto_nd_flow/reco/charge/raw_event_builder.py`, `unroll_timestamps()`.
It retains a per-IOG cumulative offset and the previous selected SYNC increment.
A hit uses preceding increments in wire order, not subsequent SYNCs in its batch.
The selected SYNC increment is rounded to an integer number of rollover periods.
A receipt timestamp below the raw ASIC timestamp invokes Flow's crossing-boundary
correction, including when the preceding SYNC arrived in another batch.

SYNC-only batches update state. Heartbeat/other SYNC subtypes do not increment
the selected epoch. Data before the first valid selected SYNC are counted as
warmup and are not silently given host times. Near-zero/implausibly phased SYNCs
and invalid receipt/timestamp relationships have visible counters. The receipt
sanity limit is 32 reset periods; very unusual readout delays require review.
These checks add conservatism beyond the array formula; equivalence tests compare
valid, post-SYNC hits. They do not guarantee recovery from arbitrary dropped or
reordered messages. In particular, a lost SYNC report with no informative later
increment cannot be inferred perfectly from these fields.

The user reports common PPS clock resets. Defaults are 100 ns ticks, 10,000,000
ticks/reset and SYNC subtype 83, matching the referenced Flow convention. These
are explicit configuration values, not measurements of the deployed stream.
Absolute cross-IOG cycle identity is not inferred from connection arrival time.

## Playback

Per-IOG playback starts after at least 1.25 detector seconds have accumulated
following the first observed selected SYNC. It advances at 1×, paced by the host
monotonic clock, but never uses host time as the hit timestamp. Future hits are
held in bounded NumPy-array chunks; due hits update each pixel by maximum detector
timestamp. Thus wire order and geometric duplicates cannot make an old hit young.

The available frontier is based on unrolled receipt timing and SYNC progress.
Exhausting it freezes playback until the reserve is replenished; there is no
catch-up time jump. Host pauses over 0.5 s are recorded and not compressed into
a detector-time leap. Late packets update state only with their original time,
so they are already faded if still relevant. The browser interpolates a reported
clock only up to 250 ms between updates and never beyond the reported frontier.

Default limits are 500,000 pending hits and 8,192 chunks per IOG. On overflow,
new display chunks are dropped with a counter. This does not stop reception, nor
does it claim to measure upstream losses. Eight independent IOG playheads avoid
having a silent/unconfigured source halt the other planes. Their epochs and
playout start times are relative; visual inter-IOG phase is not a calibrated
coincidence measurement. This is deliberately not yet the 3D event-builder clock.

## Protocol and deployment

`RDP2` frames have a 12-byte header (magic, sequence, changed-pixel count), eight
24-byte clock records (cursor, frontier, running), then 12-byte hit records
(uint32 pixel ID, float64 IOG-relative detector seconds). A hit time of -1 means
unseen. It is not a newly arrived hit age. The browser retains support for old
`RDP1` host-time frames in explicit host mode and the no-hardware demo.

All serving paths enforce 127.0.0.1. The new default is selected by both the
console entry point and `python -m raw_display`; `--time-basis host` remains an
explicit comparison, never an error fallback. Refresh the browser after updating.
The optional rate-audit files are now included; its CSV intentionally remains on
the host-consumption clock for comparison.

## Tests actually executed for v0.4.0

Development environment: Python 3.13.5, NumPy 2.3.5, Linux x86_64.

- 40 timing/audit tests passed. They cover byte-level SYNC extraction, per-batch
  state, Flow-formula equivalence under six chunk sizes, missing-period increments,
  late boundary packets, ignored heartbeats, invalid metadata, real simultaneous
  hits versus delivery-bunched hits, duplicate pixels, buffer limits, underrun,
  host pauses, CLI binding, HTTP/WebSocket and an actual localhost XPUB/SUB path.
- Node protocol/clock regression passed: detector timestamps survive transport;
  a late hit remains old; interpolation stops when paused and is capped on stalls.
- Python compilation and JavaScript syntax checks passed.
- A full graphical browser test was not run: no Chromium executable was available.
- NERSC directory/file access failed from this environment; no real commissioning
  packet file was downloaded or analyzed. The supplied URL was:
  `https://portal.nersc.gov/project/dune/data/2x2/nearline_run3/packet/ColdCommissioning/20260928_Cosmic/`.

## Synthetic performance, not a DAQ acceptance test

Same wire batches, eight IO groups, 8 words/message, about 2.49 million hits at a
simulated aggregate 622 kHz. Fixture generation is excluded. The baseline uses
the new decoder without the unroller/playback layer; both paths include parity,
geometry lookup and state updates. The timing path includes buffered playback.

| Drain batch | Decoder/mapping baseline | With ASIC timing/playback | Added CPU work |
|---|---:|---:|---:|
| 256 messages, three runs | 5.53–5.63 Mhit/s | 3.56–3.95 Mhit/s | 43–56% |
| 32 messages, one run | 2.31 Mhit/s | 1.31 Mhit/s | 76% |

The 256-message comparison's added elapsed CPU work is about 0.19–0.25 s per
2.49 million hits, roughly 5–6 percentage points of a CPU core at the simulated
622 kHz workload. That extrapolation excludes network receive, IPC copies,
web serving and visualization. Real drain sizes, hardware and Python versions
matter; rerun `tools/benchmark_timing.py` on daq03. No buffer drops occurred in
these synthetic tests; aggregate peak pending populations were about 0.89 million
hits. This is neither a production throughput guarantee nor a losslessness test.
