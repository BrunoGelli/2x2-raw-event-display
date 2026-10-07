# 2×2 raw event display

## Current 2D implementation

**Release status: v1.0.0 — accepted 2D event display; see the acceptance record.**

Trigger build: `detector-wide-2d-1`. Clock feature: `pps-playhead-1`.
The package/CLI remains `0.4.2` until the explicit release preparation step.
These sections and the [detector-wide guide](docs/detector_wide_triggers.md)
supersede the historical same-IOG-only/planned-feature descriptions below.

T words on **IOG5 are Beam** and on **IOG6 are Light**. Their time windows are
translated to every qualified target IOG. Numerical subtypes are diagnostic or
optional filters, not source identities. The browser offers independent
All / Highlight / Window-only and Beam / Light / Beam + Light controls. RX
badges identify the physical source stream; they are not duplicated receipts.

Each IOG retains its continuous local ASIC clock and independent buffered
playhead. Two consistent PPS/header observations provisionally identify its
whole-second epoch. Qualified offsets are pinned; suspect/stale epochs stop
participating instead of shifting charge timestamps. A normal hit is selected
for a trigger layer when its unrolled time falls in `[t0, t0+300 us)` by default.
About two seconds of bounded charge history allow late triggers to recover
previously received hits. This is temporal candidate selection, not proof of
causality or a saved 3D event. **The 2D phosphor can contain several triggers and
older fading hits; it is not one serialized event per screen frame.**

### Displayed-time clock

A clock above the controls labels the actual displayed playheads using the
qualified PPS epochs. It shows **the oldest labelled pane**, the time spread
across all configured panes, and an approximate lag range relative to the
**daq03 server clock**, not browser time. Hover for per-IOG timestamps.
Fermilab/Chicago is the default timezone; UTC is selectable. Date, milliseconds,
and timezone are explicit. All configured panes count, even in a focused module
view; an unqualified pane is named and excluded, never silently treated as live.

Pause freezes the label and its **lag at pause**. Stale/disconnected streams or
missing status make live time unavailable. Buffering panes are identified.
A negative lag is shown as a clock-comparison warning, not clamped to zero.
This is a **PPS/header-labelled estimate**, not a measurement of NTP lock. It
inherits any stable message-packaging/PPS epoch bias. Millisecond formatting
is not a millisecond absolute-accuracy claim. The clock does not time individual
fading hits or supply a unique Beam/Light trigger timestamp.

See [clock details and tests](docs/playback_clock.md) and the
[version preparation, push and tag runbook](docs/v1_release.md).

### Latest operator validation (reported 2026-10-06)

Bruno ran the complete deployed checkout tests: **222 passed, 5 skipped**, before
this clock addition. In a 119.99-second interval, IOG6 reported 162,148 mapped hits,
zero pre-cut late hits, and 2,600 received T words. The router reported 2,587
Light triggers to **each of all eight IOGs**. Bruno observed convincing yellow
muon candidates across the system. The full diagnostic file was not provided;
these are the pasted summary and visual report, not independently repeated tests.

The verifier remained **false** because three invalid trigger timestamps were
counted (the per-source and routing entries report the same issue, not six).
No Beam triggers were observed in that interval. Receipt/routing counts come
from separately published snapshots; their difference alone is not a loss
measurement. The exact invalid-trigger reasons, real Beam-path check,
independent PPS-cycle/phase validation, DAQ/PacMon stability and final clock
acceptance remain to be recorded before the stable tag. The helper cannot
authorize a release on its own.

No special IOG6 phase/delay correction exists in the canonical candidate.
The repaired fault remains closed; general 1.25-second buffering, late-hit ages
and raw `<10` noise veto remain intact. The clock changes only presentation and
small HTTP metadata. SUB topology, decoder, unroller, routing, trigger windows,
geometry, hit selection and all existing binary WebSocket formats are unchanged.
HTTP/WebSocket remain on `127.0.0.1`; operate one collector on daq03.

## Historical v0.4.2 baseline documentation

The material below preserves the design and operational history. For current
trigger scope, clock behavior and release status, use the sections above.

**A live, read-only charge-activity display for the ND-LAr 2×2 demonstrator.**

The backend on **acd-daq03** subscribes to PACMAN data streams, decodes packets in NumPy batches, maps electronics addresses onto the existing PacMon geometry, and serves a browser-based phosphor display. The browser runs on **ops01**; shifters use the existing VNC/approved forwarding workflow. Live visualization does not wait for files, Packetizer, Flow, or NERSC.

**Runtime documented:** v0.4.2, source commit [`28a12e9`](https://github.com/BrunoGelli/2x2-raw-event-display/commit/28a12e935eccda591373191373a90de5fd5ab818), feature build `trigger-audit-1`. This documentation update changes no runtime code.

> **Operational update: IOG 6 timing is resolved.** Bruno reports that the actual timing problem was found and fixed. The earlier late-hit investigation is retained as development history, not an outstanding deployment blocker or a prescribed calibration. The exact intervention and post-fix measurements were not supplied. Do not apply the historical inferred 0.55156 s/1.55156 s offsets as corrections. The normal 1.25 s playback reserve is general scheduling protection, not an IOG 6 compensation.

For a standalone handoff to another developer or conversation, read **[Project context](docs/PROJECT_CONTEXT.md)**. This project is separate from `BrunoGelli/2x2-live-event-display`, the NERSC/nearline/Flow-based display.

## Contents

- [Capabilities and boundaries](#capabilities-and-boundaries)
- [Install and run](#install-and-run)
- [Architecture and high-rate ingestion](#architecture-and-high-rate-ingestion)
- [Wire format and geometry](#wire-format-and-geometry)
- [ASIC-time unrolling and playback](#asic-time-unrolling-and-playback)
- [Post-SYNC noise cut and receipt indicators](#post-sync-noise-cut-and-receipt-indicators)
- [Trigger-window visualization](#trigger-window-visualization)
- [Configuration reference](#configuration-reference)
- [APIs and diagnostics](#apis-and-diagnostics)
- [ZMQ and acquisition safety](#zmq-and-acquisition-safety)
- [Validation and performance](#validation-and-performance)
- [Deployment troubleshooting](#deployment-troubleshooting)
- [Source map and next steps](#source-map-and-next-steps)

## Capabilities and boundaries

| Available now | Meaning |
|---|---|
| Eight-source raw observation | One data SUB socket per selected PACMAN; addresses come from the CRS configuration. |
| Physical 2D phosphor view | Four module views, separate anode/IOG planes, local fading, pan/zoom, hover, module selection, pause and fullscreen. |
| Batched Packet_v2 decoding | Legacy PACMAN framing, odd-parity checking, bulk field extraction and geometry lookup. |
| Stateful ASIC-time playback | Per-IOG PPS unrolling, delayed presentation, late-hit aging and bounded pending buffers. |
| Post-SYNC display veto | Raw ASIC timestamps 0–9 excluded by default; tick 10 retained. Raw acquisition is untouched. |
| SYNC and trigger badges | S83 and T-word receipt indicators, not inferred hardware locks. |
| Optional trigger layer | All hits, yellow window highlighting, or window-only display; same-IOG temporal matching. |
| Optional diagnostics | Host-time tile rates, collector timing, trigger subtype counts, and per-tile/chip late-hit audits. |

**Not implemented in this baseline:** detector-wide trigger propagation, calibrated cross-IOG epochs, a Beam/Light/Both source selector, completed/saved event building, drift-coordinate 3D reconstruction, or calibrated charge. Plotly/Three.js and a 3D camera are not part of the current Canvas frontend. Packet_v3 and new24 PACMAN framing are deliberately unsupported.

The phosphor image retains a pixel's latest displayed hit time; it is not a lossless packet history. Yellow hits are time-window candidates, not proof of a common interaction.

## Install and run

### Deployment boundary

**Bind HTTP/WebSocket only to `127.0.0.1`.** The CLI rejects other bind addresses. Do not expose the application directly to the subnet or Internet. Use the existing approved loopback-forwarding arrangement to make daq03's service accessible to the ops01 browser. The application does not configure that tunnel or provide authentication/TLS itself.

Only a modern browser is required on ops01. JavaScript, CSS, geometry and binary updates are served by daq03; no Python, ZMQ, Node, npm, or external CDN is required there.

Use one canonical deployed checkout:

```text
/data/2x2-raw-event-display
/data/2x2-raw-event-display/.venv
```

Use a separate development checkout when needed, but never accidentally run two display collectors against the detector.

### First installation

Python 3.9 or newer is required. Use a separate environment from production CRS software:

```bash
git clone https://github.com/BrunoGelli/2x2-raw-event-display.git /data/2x2-raw-event-display
cd /data/2x2-raw-event-display
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest -q
python -m raw_display --version
```

Runtime dependencies are NumPy, PyZMQ, FastAPI, Uvicorn and the WebSocket transport. HDF5 support is optional for offline validation; Matplotlib is optional for plotting captured diagnostics. Neither belongs in the live packet hot path.

### Existing checkout update

Stop the event-display process before changing its imported source. Do not stop PacMon or DAQ services. Inspect `git status --short` first; preserve local changes before updating. For a clean canonical branch:

```bash
cd /data/2x2-raw-event-display
git pull --ff-only origin main
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest -q
python -m raw_display --version
```

A non-fast-forward failure is a reason to inspect history, not to force-push or reapply an old patch. The former bundles/worktrees are historical alternatives, not additional installation prerequisites.

### Geometry and configuration check: no sockets

Reuse the existing PacMon layout directory with `--geometry-dir`, or explicitly obtain the pinned JSON snapshot once:

```bash
cd /data/2x2-raw-event-display
source .venv/bin/activate
raw-display fetch-geometry --geometry-dir layout
raw-display check --geometry-dir layout
```

`fetch-geometry` accesses GitHub and validates Git blob hashes. Normal live startup never downloads geometry or frontend assets. `check` reads configuration/geometry but opens no PACMAN or web sockets.

### No-hardware browser demo

```bash
raw-display demo --host 127.0.0.1 --port 8765
```

The demo is explicitly synthetic: it uses synthetic geometry, synthetic activity and host-time animation. It is not evidence of correct detector mapping, PPS behavior or live throughput. Do not leave the demo occupying the live server's port.

### Normal live display

From the canonical checkout, with any old display instance stopped:

```bash
cd /data/2x2-raw-event-display
/data/2x2-raw-event-display/.venv/bin/python -m raw_display serve \
    --time-basis asic \
    --geometry-dir /data/2x2-raw-event-display/layout \
    --host 127.0.0.1 \
    --port 8765
```

ASIC-time mode is the default; it is written explicitly here. This serves the phosphor view and receipt badges. The optional trigger-history matcher and late audit are disabled unless enabled at process startup.

To enable yellow trigger-window candidates on the **same single instance**:

```bash
cd /data/2x2-raw-event-display
RAW_DISPLAY_TRIGGER_VIEW=1 \
/data/2x2-raw-event-display/.venv/bin/python -m raw_display serve \
    --time-basis asic \
    --geometry-dir /data/2x2-raw-event-display/layout \
    --host 127.0.0.1 --port 8765
```

Refresh the browser after frontend updates. The default rendering target is 30 FPS, with 15 and 60 FPS choices. Backend snapshots default to 10 Hz; rendering and acquisition rates are independent.

### Confirm the process actually serving the page

```bash
curl -fsS http://127.0.0.1:8765/api/geometry |
jq '{time_basis, feature_build, trigger_view_enabled, timing_config}'
```

Expect `time_basis: "asic"` and `feature_build: "trigger-audit-1"`. `trigger_view_enabled` reflects the startup environment. A version number alone did not distinguish earlier v0.4.1 variants; inspect the serving API and imported paths when uncertain.

## Architecture and high-rate ingestion

```text
PACMAN data PUB sockets (:5556)
              |
              v
acd-daq03: one collector process
  bounded receive drains -> batch decode/parity -> geometry LUT
              |
              +-> stateful per-IOG ASIC-time unroller
              |       -> display-only raw-tick selection
              |       -> bounded playback -> latest displayed pixel times
              |
              +-> optional trigger history/matching -> separate yellow layer
              +-> optional host-rate and late-hit diagnostics
              |
              v
fixed/shared state -> FastAPI process on 127.0.0.1
              |
     approved forwarding / existing WebSocket
              v
ops01 browser: Canvas drawing, fading, hover and controls
              |
              v
existing VNC workflow for shifters
```

Source: [collector](raw_display/timed_runtime.py), [playback](raw_display/timing.py), [web server](raw_display/timed_server.py), [observers](raw_display/observer_runtime.py).

### Why Python is adequate for this design

The required work is not one browser draw call per detector packet. It is fast ingestion followed by compact state updates. An early estimate was about 330,000 hits/s; an uploaded commissioning rate capture averaged about 622,391 mapped hits/s. Those are observed/planning workloads, not a guaranteed ceiling.

The important optimization was to stop running a separate NumPy decode for each tiny ZMQ message. A source probe showed roughly 7,400–8,000 messages/s and commonly only 5–10 words/message. An eight-word, one-message-at-a-time benchmark on daq03 achieved only about 70,992 hits/s despite much faster large-message benchmarks.

The current collector drains **up to 256 immediately available messages per source visit**, validates their headers, joins their word bodies, and runs one vectorized decode over the combined words. It does not wait for a batch to fill. That maximum is a fairness limit, not a statement that real batches always contain 256 messages.

The hot path uses structured NumPy views, bit masks, bulk parity calculation, a dense geometry lookup and array-based timing. It does not construct a rich LArPix object or a pandas row for every hit. There is still per-message receive/header work and occasional per-batch bookkeeping; Python overhead has not disappeared.

The poll timeout is 20 ms when no socket is ready. The collector normally publishes state at approximately 100 ms boundaries. It uses fixed-size state arrays and bounded presentation/history structures, not an indefinitely growing packet list. Performance must be evaluated with realistic **messages/s, words/message, batch sizes and burstiness**, not hits/s alone.

### Process and browser isolation

The collector owns all PACMAN subscriptions. FastAPI handles HTTP/WebSockets in a separate process. Clients read copies of shared state; mouse interaction, local pause and rendering do not request additional detector data.

The main snapshot uses a short shared lock; it is not a lock-free system. Never hold that lock while waiting for browser/network I/O. Optional diagnostic publications use their own bounded structures and nonblocking writer locks.

Each WebSocket has one acknowledged snapshot sequence in flight. A slow client skips intermediate states; its next update is diffed against its own last delivered state. Unresponsive clients time out instead of accumulating an unbounded per-client event queue. Geometry identity changes require a page reload. These protections bound downstream work; they do not eliminate shared-machine CPU/network contention.

## Wire format and geometry

### Supported packet path

The deployed decoder assumes:

| Layer | Layout |
|---|---|
| PACMAN message header | 8 bytes, `struct <cIxH`: type, Unix seconds, reserved byte, word count. |
| PACMAN word | 16 bytes. |
| DATA word | Kind at byte 0; IO channel at byte 1; 32-bit receipt counter at bytes 2–5; 64-bit ASIC payload at bytes 8–15. |
| SYNC/TRIGGER word | `S`/`T` at byte 0; subtype at byte 1; counter at bytes 4–7. Do not decode this as a DATA receipt field. |
| ASIC payload | Packet_v2-compatible 64-bit layout for 2/2a/2b/2d-family configurations. |

For charge payloads, packet type is bits 0–1, chip bits 2–9, channel bits 10–15, timestamp bits 16–46, ADC bits 48–55, downstream marker bit 62 and parity bit 63. DATA packet type is 0. The ASIC timestamp is 31 bits; its native wrap is not the same concept as a PPS reset period.

Only valid-odd-parity DATA payloads become charge hits. The downstream marker is counted **but is not a rejection cut**. The first implementation incorrectly rejected downstream-marked data and consequently drew almost nothing; the live probe showed almost all valid data carried that marker. Matching PacMon's acceptance policy fixed the apparent missing-data problem.

Message lengths and supported header types are checked. Recognized non-D messages are counted, not painted. Unsupported framing is rejected with bounded/rate-limited diagnostics. Packet_v3/new24 and FIFO-diagnostic timestamp interpretation are not autodetected. Confirm the selected ASIC mode rather than weakening validation when the stream changes.

Sources: [codec](raw_display/codec.py), [PacMon wire definitions](https://github.com/BrunoGelli/2x2Pacmon/blob/2cf0e2c7db056dd205efb7f41616c1795fa9ea67/pkg/message.go).

### Authoritative configuration

PACMAN addresses are read from this file at startup:

```text
/home/acd/acdaq/CRS_DAQ/daq0/crs_daq/io/pacman.json
```

Expected structure: `io_class: "PACMAN_IO"` and `io_group: [[integer, host], ...]`. There is no separate maintained address list in this project. `--pacman-config` is an explicit override. Each selected host is connected on data port 5556; no command socket on port 5555 is created.

ASIC-family compatibility is checked once using `io_group_asic_version_` in:

```text
/home/acd/acdaq/CRS_DAQ/daq0/crs_daq/RUN_CONFIG.json
```

`--run-config` overrides the file. Unknown/incompatible families fail rather than selecting a different packet decoder midstream. Configuration files are read, never modified; changes take effect after an intentional restart.

### PacMon mapping, not a second detector geometry

The loader reads `geometry_mod0_v4.json` through `geometry_mod3_v4.json` from the chosen layout directory. Its pinned downloader references PacMon commit `2cf0e2c7db056dd205efb7f41616c1795fa9ea67` and checks each expected Git blob hash. Local geometry files also receive SHA256 provenance fingerprints.

The mapping reproduces PacMon:

```python
module = (io_group - 1) // 2
geometry_io_group = 2 - (io_group % 2)
local_tile = (io_channel - 1) // 4 + 1
geometry_tile = local_tile + 8 * (1 - (io_group % 2))
```

Example: IOG 6, local tile 5 uses module 2, geometry IO group 2, geometry tile 13. All four Hydra channels of a tile address the same physical pixels. Rerouting within that tile therefore does not create duplicate geometry pixels.

A dense `int32` lookup table maps `(iog, io_channel, chip, channel)` to a pixel ID. Missing addresses remain `-1`; they are counted, never drawn at a guessed origin. Missing tiles, nonfinite coordinates, off-grid pixels and physical overlaps fail geometry construction.

The browser receives an 8-byte record per pixel: raster column/row, chip, channel, IOG and tile. Tile metadata supplies origin, pitch and dimensions. Separate tile rasters preserve physical gaps and the coordinates/orientations encoded by PacMon; positive geometry Y is drawn upward.

Do not hard-code a total pixel count. Logs in this development session reported 39,200 pixels for an IOG-1 subset and 337,600 for a later full live layout; the synthetic demo used a different total. The geometry count is neither the enabled-channel count nor the number currently firing. Source: [geometry.py](raw_display/geometry.py).

## ASIC-time unrolling and playback

### Four different notions of time

| Quantity | Used for |
|---|---|
| Raw ASIC timestamp | Assigning charge-hit time, subject to the deployed reset/counter convention. |
| PACMAN receipt counter | Reset-boundary interpretation and received-data timing frontier; not a replacement charge timestamp. |
| PACMAN message Unix seconds | Ignored by the current hit-time decoder. An NTP offset is not directly added to ASIC hit times. |
| Host monotonic time | Pacing presentation and reporting receipt/health ages, not timestamping the physical charge signal. |

The original phosphor version stamped every drained batch with host-consumption time. The ASIC-time upgrade removed that artificial time grouping: packet generation/ASIC time and packet delivery time are no longer deliberately equated.

### Stateful reset-aware unrolling

The implementation is based on `RawEventBuilder.unroll_timestamps()` in [ndlar_flow at the referenced commit](https://github.com/DUNE/ndlar_flow/blob/a0eb2f364e35340d67fd73dc09a8e8f847211a58/src/proto_nd_flow/reco/charge/raw_event_builder.py), adapted to persist state across live batches.

Defaults are 100 ns/tick, `P = 10,000,000` ticks/reset, and selected SYNC subtype 83. Subtype 72 is heartbeat and does not advance this reset epoch. These are explicit configuration assumptions, not parameters automatically measured from the hardware.

For each IOG independently:

1. Keep the original word order and the positions of accepted DATA hits. Preserve SYNC-only batches too.
2. For a selected SYNC counter `s`, calculate `q = round(s/P) * P`. The code accepts a positive increment within `max(1, P//20)` ticks of that multiple; invalid SYNCs are counted.
3. Assign each hit the sum of **preceding** valid increments, including the cumulative offset from previous batches. A later SYNC in the same batch cannot change the earlier hit's epoch.
4. Apply the current crossing-boundary rule, using signed 64-bit arithmetic:

```python
crossed = receipt < raw_asic
unrolled_hit = (raw_asic % P) + preceding_offset - crossed * previous_sync_increment
unrolled_receipt = preceding_offset + receipt
```

5. Retain initialized, nonnegative times with `unrolled_receipt >= unrolled_hit` and `receipt < 32*P`. Count warmup hits and invalid relationships separately.

The implementation retains the last SYNC increment for cross-batch corrections. A multi-period increment can account for a missed report when that information is encoded in a later SYNC. A backwards-moving charge timestamp alone does not trigger a guessed rollover. Out-of-order hits are counted.

**Limit:** this arithmetic assumes the deployed counter/reset relationship is compatible with the model. It is not a universal calibration for arbitrarily free-running or differently phased ASICs, and cannot recover every dropped/duplicated/reordered SYNC. Passing the sanity checks is not a proof of correct absolute time. The historical IOG 6 problem illustrated that distinction; it is now reported fixed, not compensated by an undocumented software offset.

Sources: [timing.py](raw_display/timing.py), [timing implementation notes](docs/asic_time.md).

### Detector-time playback, not immediate flashes on receipt

Each IOG maintains an independent playback cursor and a frontier formed from the newest valid unrolled receipt/SYNC progress. Once the initial frontier exceeds the first valid SYNC by the configured reserve (default **1.25 s**), playback starts at the relative detector origin and advances at 1× speed, paced by host monotonic elapsed time.

The reserve is an initial/rebuffer target, not a fixed guaranteed end-to-end latency. A healthy frontier does not prove all older charge packets have arrived.

Future hits wait in bounded NumPy-array chunks ordered by minimum timestamp. At presentation, each pixel is updated with the maximum due timestamp using `np.maximum.at`. An older or duplicated arrival cannot overwrite a newer displayed time.

A hit behind the cursor is counted as late and applied at its **original age**, not flashed at full brightness. If the cursor would outrun the frontier, it freezes and re-buffers. A host pacing gap above 0.5 s increments `pacing_stalls` and is not compressed into a detector-time jump.

The default limits are **500,000 pending hits and 8,192 pending chunks per IOG**. Overflow drops display work with a counter; it does not wait for a browser. Silent or buffering sources do not freeze every other IOG.

Brightness is conceptually:

```text
I(pixel, Tplay) = exp(-(Tplay - latest_due_hit_time(pixel)) / persistence)
```

This is age-based activity, not ADC/charge brightness. The browser uses lookup tables/raster updates rather than recalculating an expensive exponential per hit. It extrapolates each reported detector clock by at most 250 ms and no farther than its frontier; pause holds the displayed state/clocks while ingestion continues.

### Cross-IOG timing is not calibrated yet

The first SYNC seen on each subscription need not be the same physical PPS cycle. Matching receipt badges, similar buffer depths, or `synchronized = 1` do not establish a shared absolute epoch. Here, `synchronized` means a valid reset reference initialized the unroller, not measured NTP lock or phase lock.

The planned shared-PPS calibration must establish common cycle identity, retain unrounded interval measurements for frequency corrections, and validate residual offsets/uncertainty. The current code rounds reset increments and does not implement that calibration. General buffering and true-age handling should remain even after a hardware timing fault is fixed.

## Post-SYNC noise cut and receipt indicators

### Exact display-only policy

The user observed recurring anode flashes on v2a populations. They persisted under ASIC-time playback. Stephen Greenberg's operational guidance supplied in the development conversation identified known SYNC-induced self-trigger noise and the processing practice of excluding the first ten ticks.

The implemented rule is literally:

```python
display_selected = mapped & (raw_asic_timestamp >= minimum)
# minimum defaults to 10; minimum=0 disables this display cut
```

Ticks 0–9 are excluded; tick 10 survives. The raw timestamp is **not reduced modulo P for this cut**, and no host-time veto window is opened after receiving S83. A raw value `P+5` is not removed by a minimum of 10.

In ASIC mode the unroller first consumes all accepted hits/SYNCs; display selection follows. Reset state, frontier calculation, pre-cut counters and rate-audit input remain unfiltered. SYNC/trigger words are not charge hits and are not removed by this policy. Recorded data and ASIC threshold/reset settings are unchanged.

The user reported that the live flashes disappeared after this cut. That is an operational success of the visualization policy, not evidence that the display removed the physical pickup at its source. See [post-SYNC semantics](docs/post_sync.md).

### Badges

`SYNC 83 RX` responds only to a PACMAN `S` word with subtype 83, ignoring heartbeat 72. It pulses for a newly observed recent count, is subdued between pulses, goes stale after 2.5 s without one, and becomes unknown on stale/disconnected telemetry. There is no autonomous 1 Hz animation.

`TRG RX` responds only to PACMAN `T` words. Lack of a recent trigger is normal on sources that do not carry triggers. Neither badge is a hardware lock claim. Both show **collector receipt**, so they can precede the corresponding buffered charge by the playback reserve. Pausing the image does not pause receipt indicators.

Small metadata messages ride the existing WebSocket; they do not create detector subscriptions. Sources: [post_sync.py](raw_display/post_sync.py), [trigger frontend](raw_display/static/trigger_view.js).

## Trigger-window visualization

Enable with `RAW_DISPLAY_TRIGGER_VIEW=1` before starting the one ASIC-time collector. This adds bounded recent-hit history and a separate matched-pixel layer. Browser modes are **All hits**, **Highlight windows** (yellow), and **Window hits only**.

Triggers are projected using their counter at PACMAN word bytes 4–7 and preceding valid SYNCs in the same source. Triggers before an established epoch, or trigger counters at/above one reset period, are counted as invalid for matching rather than guessed. Trigger words do not advance the canonical charge frontier.

The default half-open window is `[t0, t0 + 300 µs)`, equivalent to 3,000 ticks at 100 ns/tick. Configured pre/post durations are quantized to detector ticks. Selection uses corrected detector time, never the next 300 µs of host arrivals.

A late trigger can recover previously received candidates from retained history. Overlapping windows are merged and history carries matched flags to avoid repeatedly counting the same retained hit. Future matched hits wait for the **same canonical playback cursor**; no second independent detector clock is introduced. Their independent last-hit layer means unrelated later activity on a pixel does not erase its trigger-window history from window-only mode.

Defaults per IOG: 2 detector seconds of recent history, 500,000 retained hits, 4,096 history chunks and 2,048 merged windows. The matched presentation queue is bounded too. Capacity/coverage failures have explicit counters; retained history is not a guarantee of complete event data. Matching is disabled by default because it adds computation and memory.

**Same IOG only:** a trigger on IOG 6 selects only IOG 6 charge. Per the user's routing description, IOG 5 carries beam triggers and IOG 6 carries light triggers. These labels are operational context, not a hard-coded subtype interpretation. Inspect observed T subtypes; a subtype filter alone cannot distinguish sources using the same value. A Beam/Light/Both source selector and propagation to the other IOGs remain future work.

Sources: [trigger_windows.py](raw_display/trigger_windows.py), [observer_runtime.py](raw_display/observer_runtime.py), [detailed trigger/audit notes](docs/trigger_audit.md).

## Configuration reference

### Principal CLI options

| Option | Default | Notes |
|---|---|---|
| `--host` | `127.0.0.1` | Only accepted web bind address. |
| `--port` | `8080` | Deployment examples use `8765`. |
| `--geometry-dir` | `layout` | Relative to working directory unless absolute. |
| `--pacman-config` | CRS `io/pacman.json` path above | Read at startup. |
| `--run-config` | CRS `RUN_CONFIG.json` path above | Packet-family check. |
| `--iog 1 6` | All configured IOGs | Restricts live/probe sources. |
| `--time-basis` | `asic` | `host` is an explicit comparison, never automatic fallback. |
| `--hwm` | `4096` | Receive queue limit in messages per source. |
| `--batch-messages` | `256` | Maximum immediately available frames drained/source visit. |
| `--frame-hz` | `10` | Web snapshots; does not change the collector's nominal 100 ms publication boundary. |
| `--max-clients` | `4` | WebSocket admission limit. |
| `--tick-ns` | `100` | Detector tick assumption. |
| `--rollover-ticks` | `10000000` | PPS/reset-period model. |
| `--sync-type` | `83` | Selected unrolling subtype; S83 badge remains specifically S83. |
| `--playback-delay` | `1.25` | Initial/rebuffer reserve in detector seconds. |
| `--max-pending-hits` | `500000` | Per-IOG playback capacity. |
| `--min-raw-timestamp` | `10` | Display cut; 0 disables. This is the canonical name, not older patch flags. |
| `probe --seconds` | `30` | Probe opens live SUB connections; never run alongside the serving collector as a casual diagnostic. |

Use `raw-display serve --help` for parser details. Startup validates ranges before normal operation; these are not live detector-control settings.

### Optional environment settings

| Variable | Default | Meaning |
|---|---|---|
| `RAW_DISPLAY_TRIGGER_VIEW` | `0` | Exactly `1` enables history/matching; badges do not require it. |
| `RAW_DISPLAY_TRIGGER_PRE_US` | `0` | 0–100000 µs before trigger. |
| `RAW_DISPLAY_TRIGGER_POST_US` | `300` | Greater than 0, up to 100000 µs after trigger. |
| `RAW_DISPLAY_TRIGGER_TYPES` | Empty | All T subtypes; otherwise comma-separated byte values. |
| `RAW_DISPLAY_TRIGGER_HISTORY_S` | `2` | 0.1–10 detector seconds of history. |
| `RAW_DISPLAY_TRIGGER_MAX_HITS` | `500000` | Retained-hit capacity/IOG; at most 2,000,000. |
| `RAW_DISPLAY_LATE_AUDIT_IOGS` | Empty | Comma-separated selected IOGs for optional timing audit. Normally off after commissioning. |
| `RAW_DISPLAY_RATE_AUDIT` | Off | Exactly `1` enables the ASIC collector's host-time tile-rate control measurement. |

These are read when the process starts. Setting them in another terminal does not change an already-running server. Optional trigger/late-audit routes belong to ASIC mode; the host-time comparison/demo does not claim these features.

## APIs and diagnostics

### Routes

| Route | Purpose |
|---|---|
| `GET /` and `/static/...` | Local frontend assets. |
| `GET /api/geometry` | Geometry metadata, hash, timing settings and feature identity. |
| `GET /api/geometry.bin` | Fixed 8-byte pixel records. |
| `GET /api/status` | Received counters, collector health/CPU and per-IOG playback state. |
| `GET /healthz` | Collector liveness/freshness; not a detector-quality or losslessness certification. |
| `GET /api/tile-rates` | Optional host-time rate ring; disabled status when not enabled. |
| `GET /api/observers` | Trigger subtype/matching counters and bounded audit snapshots. |
| `GET /api/timing-audit` | Selected IOG timing audit; registered even when the audit is disabled. |
| `WS /ws` | Acknowledged normal state plus opt-in metadata/matched layer. |

Sources: [ASIC server](raw_display/timed_server.py), [host/demo server](raw_display/server.py).

### Counter meanings

`mapped_hits` is pre-display-cut mapped data; `unmapped_hits` is accepted data without geometry. Top-level `post_sync_filtered_hits` and `display_selected_hits` separate the raw-tick cut. Earlier local patches called these `sync_vetoed_hits` and `display_eligible_hits`; use the canonical names in current code.

Inside `timing`, `timing_eligible_hits` additionally requires initialized/valid timing, `pre_cut_late_hits` preserves lateness before the display veto, and `late_hits` counts selected hits already behind the cursor. `post_sync_filtered_hits` and `display_selected_hits` at this level have the narrower timing-valid denominator. Do not assume top-level and timing-nested counters count identical populations.

`buffer_seconds = frontier_s - cursor_s` after startup; `pending_hits` is the current queued population. `buffer_dropped_hits`, `invalid_times`, `invalid_syncs`, `underruns` and `pacing_stalls` identify different failure modes. Many counters are cumulative since collector start. Use interval differences and the appropriate selected/timing-valid denominator, not the mere fact that a count increases.

SYNC receipt fields are `sync83_packets` and `sync83_age_s`. Their similar values across IOGs do not prove calibrated phase alignment. The status explicitly reports that upstream transport loss is not measurable from this stream.

### Host-time tile-rate control measurement

Start the existing ASIC server with `RAW_DISPLAY_RATE_AUDIT=1`, then capture from another terminal:

```bash
cd /data/2x2-raw-event-display
.venv/bin/python tools/tile_rate_audit.py capture \
    --url http://127.0.0.1:8765 --seconds 60 --out tile-rates-60s.jsonl
# Plot afterward, preferably off the DAQ-support host:
python tools/tile_rate_audit.py plot tile-rates-60s.jsonl --outdir tile-rate-plots
```

The rate recorder uses the exact collector publication boundaries, counts repeated mapped packets and distinct pixels separately, and retains pre-cut input. Rates divide by actual `end-begin`, not an assumed exact 100 ms. It is still a **host-consumption-clock** measurement after the image moved to ASIC time.

The ring holds 600 rows; a busy reader can cause explicitly skipped diagnostic rows rather than block the writer. Gaps are not zero-rate measurements. Full-drain counts indicate that a servicing cap was reached, not a measured ZMQ queue depth. The live collector does not write diagnostic files; the HTTP capture tool does.

### Optional late-hit investigation

IOG 6 is resolved; this remains a reusable diagnostic, not a required workaround. To investigate a source, enable `RAW_DISPLAY_LATE_AUDIT_IOGS=6` on the existing process and run:

```bash
cd /data/2x2-raw-event-display
.venv/bin/python tools/inspect_timing_audit.py \
    --iog 6 --seconds 15 --json-out /tmp/iog6-timing-audit.json
```

The client makes two loopback HTTP reads and rejects stale/restarted/disabled captures. The output file is created exclusively; use a new name if it exists. It reports interval tile fractions, cumulative chip contributors, raw counters versus reset period, crossing corrections and bounded samples/histograms. Inferred ASIC-to-receipt lag can reflect an epoch-model error; it is not an independent measurement of physical or network delay.

### WebSocket timing contract

`RDP1` is the host/demo protocol: header plus pixel ID/age updates. `RDP2` carries detector-time pixel timestamps and eight IOG clocks: a 12-byte header, eight 24-byte `(cursor, frontier, running)` records, then 12-byte `(pixel_id, detector_seconds)` records. Unseen ASIC-time pixels use `-1`.

Optional `RDT1` carries the matched-layer changes with the same detector-time interpretation. Optional JSON receipt telemetry precedes the normal frame. One RDP2 sequence acknowledgement covers its preceding optional data; older clients receive only formats they request. Network receive time is not substituted for a packet's detector timestamp.

## ZMQ and acquisition safety

### What the code does guarantee about its own actions

The collector uses data SUB connections only. It does not send PACMAN commands, change registers, power-cycle anything, configure ASICs, alter production files, or deploy services to hardware. It requests lower CPU priority where available. HWM/drain settings and the subscription count remained unchanged when timing, the raw-tick veto and the trigger/audit extensions were added.

All web clients share this one collector. Audit tools use the existing HTTP API; they do not add live detector readers. Diagnostics/matched buffers are bounded. Slow browsers can time out or lose intermediate display updates without an unbounded queue being fed back into collection.

### What it cannot guarantee merely by being a SUB client

**Read-only is not zero-cost.** Another PACMAN subscriber adds traffic and publisher work. Buffering, CPU scheduling, sockets, and host resources can still couple an observer to PacMon or acquisition. Do not describe this application as incapable of affecting DAQ under every failure mode.

The development review found public legacy PACMAN dataserver code with an application-level wait around reuse/release of a zero-copy message buffer (`msg_ready`/`clear_msg`), including a one-second timeout in one implementation. This concern is distinct from ordinary PUB high-water behavior. A ZeroMQ buffer-release callback is not an acknowledgement that the DAQ/display application processed an event, but application logic around the callback can still influence publisher progress.

The exact deployed dataserver build was not established in this conversation. Treat that as an implementation-specific coupling to review, not proof that it caused the observed noise or current timing behavior. The earlier suggestion to deliberately stall a subscriber during normal acquisition was withdrawn; such stress tests belong in an isolated or approved commissioning setup.

### Observed improvement and operating discipline

The early per-message implementation noticeably disturbed PacMon. After batching, increasing the receive HWM from 128 to 4096 messages/source, increasing bounded drains from 8 to 256 messages/source, and specializing the decoder, the user reported normal-looking PacMon behavior. These changes were not isolated experimentally, so batching is a reasoned principal explanation, not a measured attribution of every improvement.

Do not aggressively throttle the receiver to reduce impact: slowing its drain can make queueing worse. Drain promptly, bound/drop downstream display work, and compare independent DAQ/PACMAN health indicators during approved changes. Larger HWM is buffering, not a no-loss or latency guarantee.

Do not run a probe alongside the serving collector, increase subscriber count casually, or use a second port to work around an occupied port. Preserve one instance. Do not reboot/reset PACMANs to fix a web deployment issue. A future nonblocking mirror of already-received data is an architectural option, not currently implemented.

These operational constraints and observations come from the development conversation; no facility-wide policy audit or production safety certification is claimed here.

## Validation and performance

Run the full suite in the canonical environment:

```bash
cd /data/2x2-raw-event-display
.venv/bin/python -m pytest -q
# Optional frontend regressions when Node is available:
node tests/test_frontend_clock.js
node tests/test_sync_indicator.js
node tests/test_trigger_view.js
```

Coverage includes independent wire examples, parity and geometry aliases, ordered SYNC extraction, cross-batch unrolling, malformed input, pre/post-cut semantics, retained late-hit ages, buffer limits, trigger boundaries/history, source isolation, HTTP/WebSocket behavior and localhost publisher/collector integration. Some optional tests skip without Node or HDF5 support. Do not equate fixture browser tests with ops01/VNC acceptance.

The user reported **137 passed, 2 skipped** after installing the trigger/audit feature, before the subsequent client-error tests and version-label change. That is a dated result for that checkout, not the expected invariant test count of every revision. No new runtime test result is claimed by this documentation-only update.

Decoder-only benchmark:

```bash
.venv/bin/python -m raw_display benchmark --words 8 --messages 50000 --batch-messages 256
```

Same-input timing comparison:

```bash
.venv/bin/python tools/benchmark_timing.py --words 8 --batch-messages 256 --rate 622000
.venv/bin/python tools/benchmark_timing.py --words 8 --batch-messages 32 --rate 622000
```

The decoder benchmark excludes the playback layer. The timing tool includes synthetic unrolling/buffering; both exclude real ZMQ/NIC behavior, IPC/web work and VNC. Actual batches can be much smaller than the drain cap. Development reports reached several million hits/s with batching, but varied with code version and workload; do not combine unrelated benchmark numbers into one production headroom claim.

Optional recorded-file validation:

```bash
.venv/bin/python -m pip install -e '.[validation]'
.venv/bin/python tools/validate_packet_timing.py /path/to/packet-file.h5 --max-packets 2000000
```

This compares the streaming unroller with its referenced Flow formula on a bounded packet sample; agreement is not independent proof that hardware reset assumptions hold. The tool also supports constrained HTTPS range reads of a direct file URL. The commissioning directory offered during development could not be accessed from that development environment; no successful real-file validation from that URL was claimed.

## Deployment troubleshooting

### 404 on `/api/timing-audit`, no trigger badges

In the new ASIC server the route exists even with auditing disabled. An absent route plus `feature_build: null` indicates an old/different serving application or wrong endpoint, not merely a missing audit environment variable.

The actual deployment failure was Python importing an old checkout while using the new checkout's interpreter. **An absolute interpreter path does not override current-directory module resolution.** Check all paths from the intended directory:

```bash
cd /data/2x2-raw-event-display
.venv/bin/python -c 'import sys, raw_display, raw_display.timed_server as s; print(sys.executable); print(raw_display.__file__); print(s.__file__)'
git log -1 --oneline
curl -fsS http://127.0.0.1:8765/api/geometry | jq '{time_basis, feature_build, trigger_view_enabled}'
```

All imports must agree with the intended checkout. Reinstalling `-e .` in the old directory simply reinstalls the old source. Both virtual environments can show the identical `(.venv)` prompt.

A parenthesized shell block `( ... )` also does not preserve its `cd` or environment activation in the parent terminal. Start the server from the canonical directory explicitly. Changing environment variables does not update an old process. Stop only the display instance, then restart; hard-refresh the browser afterward.

### Branch divergence

Separate local bundles and independently published implementations repeatedly created sibling commits with similar version labels. Normal Git rejection protected remote work; the graphical askpass warning and pip-upgrade notice were not the non-fast-forward cause.

The settled workflow is one canonical `main` deployment, one matching venv, verified commit/feature metadata, and reviewed incremental changes. Do not force-push, reset away unpreserved work, reapply historical patches, or delete backup worktrees blindly. See [deployment notes](docs/deployment_troubleshooting.md) and the handoff history.

### Other common checks

An unmapped warning can remain after a handful of historical packets; measure its recent rate. No visible trigger candidates can mean no T words, rejected T times/subtypes, no hits in the window, insufficient history, or the deliberate same-IOG limitation. Use `/api/observers`, not a larger arbitrary window, to distinguish these cases.

`ss -ltnp '( sport = :8765 )'` is a read-only listener check when a stale process is suspected. Do not kill unrelated processes. Launching a new collector before discovering that a port is occupied can briefly create duplicate subscriptions; confirm the old instance has stopped first.

## Source map and next steps

| File | Responsibility |
|---|---|
| `raw_display/codec.py` | Legacy framing, vectorized fields/parity, ordered words/indices. |
| `raw_display/geometry.py` | PacMon imports, provenance, raster records and dense lookup. |
| `raw_display/runtime.py`, `server.py` | Shared metrics and explicit host/demo path. |
| `raw_display/timing.py` | Stateful ASIC unroller and bounded playback. |
| `raw_display/timed_runtime.py`, `timed_server.py`, `timing_cli.py` | ASIC collector, API/protocol and startup path. |
| `raw_display/post_sync.py` | Display-only raw-tick policy and receipt metadata. |
| `raw_display/trigger_windows.py`, `observer_runtime.py` | Same-IOG matching, matched state and observer publication. |
| `raw_display/rate_audit.py`, `late_audit.py` | Bounded host-rate and detailed timing diagnostics. |
| `raw_display/static/` | Canvas frontend, clock interpolation, badges and trigger controls. |
| `tools/` | Capture, audit inspection, offline validation and benchmarks. |
| `tests/` | Python/Node regression and localhost integration tests. |
| `deploy/raw-display.service.example` | Reviewable service template, not automatically installed. |

The next feature is a **separate triggered-event builder and 3D view**, fed from decoded/unrolled hits before reduction to last-pixel state. Planned requirements are common PPS cycle alignment, measured clock scale/offsets, configurable beam/light source identity, a bounded per-hit event buffer, late-arrival-aware completion, detector geometry/drift direction and a justified drift velocity/window. Start with `(transverse position, t_hit-t0)` if calibration is not yet ready; do not present it as calibrated spatial reconstruction.

No per-IOG timing offset was added to fix IOG 6 in the reviewed code. Its operational repair does not automatically implement cross-IOG trigger calibration. Preserve the general buffering, true-age handling, PPS veto and no-extra-subscriber architecture while adding 3D.

### Documentation provenance

Implementation statements were checked against runtime commit `28a12e935eccda591373191373a90de5fd5ab818`. Measurements and the IOG 6 resolution are identified as reports from the development conversation. Proposed calibration and 3D functionality are explicitly future work. Existing version-specific notes and [VALIDATION.md](VALIDATION.md) are historical evidence, not proof that every later configuration was tested identically.

PacMon geometry/wire conventions and the referenced `ndlar_flow` timing code are upstream sources; retain their attribution in [NOTICE.md](NOTICE.md). This README does not establish a new license for material with no supplied licensing decision.
