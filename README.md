# 2×2 raw event display — v0.4.0

A read-only, batched PACMAN observer on **acd-daq03**, with Canvas rendering in
ops01's browser and the existing approved forwarding/VNC workflow. Live serving
now defaults to **unrolled ASIC-time playback**, not packet arrival time.
This remains a 2D phosphor display, not a triggered event builder.

## Network boundary

The web CLI accepts **only `127.0.0.1`**. Keep the existing approved tunnel to
that loopback endpoint. The project never opens a public/subnet-facing web
listener. ops01 needs only its browser; all assets are served locally, without
a CDN. Nothing here installs or changes PACMAN, PacMon, or production DAQ services.

## Install / update

Use a separate virtual environment, Python 3.9 or newer:

```bash
git clone https://github.com/BrunoGelli/2x2-raw-event-display.git
cd 2x2-raw-event-display
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest -q
raw-display --version
```

For an existing clean checkout, use `git pull --ff-only`, then the install/test
commands above. The earlier optional rate-audit files are now included in main.
If that patch is still applied locally, save it before pulling rather than
forcing an overwrite:

```bash
git status --short
git stash push -u -m "before-asic-time-update" -- raw_display tools tests docs README.md pyproject.toml
git pull --ff-only
```

Keep that stash as a backup; do **not** automatically pop it or reapply the old
patch over this release. The detector-time collector already integrates the
host-time rate audit. Data files outside these source paths are not stashed.

## Start the display

```bash
raw-display check --geometry-dir layout
raw-display probe --geometry-dir layout --iog 1 --seconds 10
raw-display serve --geometry-dir layout --host 127.0.0.1 --port 8765
```

`serve` and `probe` default to `--time-basis asic`. The normal transport settings
remain **HWM 4096 messages/source, bounded drain 256 messages/source, SUB on 5556**.
There are no extra PACMAN subscribers for timing, rate capture, or web clients.
The additional timestamp/buffer computation is not zero-cost; validate CPU and
normal acquisition behavior on daq03 as usual.

At startup, each IOG waits for a valid PPS SYNC and then builds a detector-time
reserve. `WAITING FOR PPS` or `BUFFERING` is intentional, not silent packet loss.
There is no automatic fallback to host timestamps. Source packet rates include
received/mapped hits even while playback is warming up.

Timing settings, shown explicitly here with their defaults:

```bash
raw-display serve --geometry-dir layout --host 127.0.0.1 --port 8765 \
  --time-basis asic --tick-ns 100 --rollover-ticks 10000000 \
  --sync-type 83 --playback-delay 1.25 --max-pending-hits 500000
```

The default subtype 83 is ASCII `S`, matching Flow's selected SYNC subtype;
heartbeat subtype 72 (`H`) does not increment the PPS epoch. Confirm the tick,
reset period and subtype against the deployed stream; they are not autodetected.
The 1.25-second setting is a **minimum initial/rebuffer reserve**, not a promise
of exact end-to-end latency. Increase it explicitly if late deliveries/underruns
show that the reserve is insufficient. Do not tune it to hide real charge bursts.

Use `--time-basis host` explicitly to compare with the previous arrival-time
view. The no-hardware demo remains a clearly labeled synthetic host-time view:

```bash
raw-display demo --host 127.0.0.1 --port 8765
```

Reload the browser after upgrading: ASIC mode uses the new `RDP2` binary frame
format carrying detector-time hit timestamps and per-IOG playback clocks.

## What ASIC-time playback does

The decoder retains SYNC words and accepted-hit positions in original wire
order. A stateful unroller per IO group follows the rollover arithmetic in
Flow's `RawEventBuilder.unroll_timestamps()`, retaining the preceding SYNC across
receive batches. Receipt timestamps help correct data that crossed a reset
while traversing the tile; they are **not** substituted for ASIC hit timestamps.

Future hits wait in bounded arrays until their detector playback time. Late
hits retain their real age, and an older packet cannot replace a pixel's newer
timestamp. Repeated hits are reduced by timestamp maximum when presented.
A common arrival batch therefore does not imply common full-brightness flashes.
Genuinely simultaneous ASIC timestamps remain simultaneous; there is no noise
filter, PPS veto, phase smearing or event-rate smoothing.

Host monotonic time only paces animation at 1× detector time. The playhead never
jumps forward merely because a new packet batch arrived. An empty reserve freezes
playback and visibly re-buffers. Long collector pauses are not compressed into
one fresh flash. The pending buffer is bounded per IOG in both hits and chunks;
overflow discards display work with visible counters, never waits on a browser.

**Epochs are relative per IOG.** Unrolling is not an absolute cross-IOG calibration.
The first observed PPS can be a different cycle on different connections.
Do not use this display as proof of microsecond cross-IOG coincidence or trigger
association. That calibration remains for the separate 3D/event-building work.
Missing/duplicated/reordered SYNC messages can compromise epoch continuity;
received counters cannot certify lossless transport. See [timing details](docs/asic_time.md).

## Existing configuration and geometry remain authoritative

PACMAN endpoints are read at startup from:

```
/home/acd/acdaq/CRS_DAQ/daq0/crs_daq/io/pacman.json
```

There is no duplicate IP list. `--pacman-config` deliberately overrides this path.
Packet-family compatibility is checked once using `io_group_asic_version_` in:

```
/home/acd/acdaq/CRS_DAQ/daq0/crs_daq/RUN_CONFIG.json
```

`--run-config` overrides that path. This display supports Packet_v2-compatible
2/2a/2b/2d families and legacy PACMAN 8-byte headers with 16-byte words. It does
not add v3 or new24 support. Valid-parity data in both upstream/downstream
marker states are accepted, as in PacMon. Configuration/invalid-parity packets
are not painted; triggers and SYNCs are not charge hits.

Use the existing PacMon `layout` directory, or explicitly fetch pinned geometry:

```bash
raw-display fetch-geometry --geometry-dir layout
```

The four `geometry_mod{0,1,2,3}_v4.json` files retain PacMon tile swaps/orientations.
The downloader verifies blobs pinned to PacMon commit
`2cf0e2c7db056dd205efb7f41616c1795fa9ea67`; it never runs during live acquisition.
The mapping is unchanged:

```python
module = (io_group - 1) // 2
geometry_io_group = 2 - io_group % 2
local_tile = (io_channel - 1) // 4 + 1
geometry_tile = local_tile + 8 * (1 - io_group % 2)
```

The four Hydra channels of a tile share physical pixel IDs. Unknown mappings are
counted, not assigned a guessed coordinate. Geometry pixel totals are not enabled
channel counts.

## Diagnostics and overhead

`/api/status` reports per-IOG PPS, warmup, boundary corrections, invalid timing,
late hits, pending-buffer drops and playback state, plus collector CPU fraction.
In the browser console, `rawDisplayDiagnostics()` reports clocks and connection
health. The buffer frontier comes from detector/SYNC/receipt timing; the browser
never supplies detector timestamps.

The host-time rate audit is retained as an **independent control**, not relabeled
as an ASIC-time measurement. Enable it on the existing ASIC serving instance:

```bash
RAW_DISPLAY_RATE_AUDIT=1 raw-display serve --geometry-dir layout --host 127.0.0.1 --port 8765
python tools/tile_rate_audit.py capture --url http://127.0.0.1:8765 --seconds 60 --out rates.jsonl
# Plot offline; Matplotlib is needed only for plotting:
python tools/tile_rate_audit.py plot rates.jsonl --outdir rate-plots
```

Do not run a second serving instance just to capture rates. An audit-reader
cannot hold up the diagnostic writer; skipped audit rows are explicitly reported.

The old `raw-display benchmark` measures decoding/mapping, not ASIC playback.
Use the new same-input comparison for the timing overhead:

```bash
python tools/benchmark_timing.py --words 8 --batch-messages 256 --rate 622000
python tools/benchmark_timing.py --words 8 --batch-messages 32 --rate 622000
```

These are synthetic offline benchmarks, not NIC, HTTP, browser/VNC or production
DAQ tests. Actual drain sizes can be lower than the configured maximum.

## Recorded-file validation

No HDF5 dependency is added to the live application. The optional offline tool
requires `h5py` and compares streaming unrolling with Flow's array formula:

```bash
python tools/validate_packet_timing.py /path/to/packet-file.h5 --max-packets 2000000
```

It also accepts a direct HTTPS **file** URL and uses bounded HTTP range reads,
refusing servers that ignore Range rather than downloading the entire file.
The supplied NERSC commissioning directory was not retrievable from the development
environment, so **v0.4.0 has not been validated on that real packet sample**.
Do not interpret synthetic tests as evidence about the physical source of the
one-second bursts.

Sources: PacMon mapping/wire definitions; DUNE `ndlar_flow` timing arithmetic
pinned to `a0eb2f364e35340d67fd73dc09a8e8f847211a58`. See `NOTICE.md` and
[ASIC-time implementation and test notes](docs/asic_time.md).
