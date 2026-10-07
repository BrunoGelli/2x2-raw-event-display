# 2×2 raw event display — project handoff and conversation context

## Current continuation — 2026-10-07, optional 3D development

The user-designated accepted baseline is **v1.0.0**, commit
`cd4f5632fe42e05ab41cb9bd5202f1fde3c5228a`. Work is on `feature/3d-events`,
package `1.1.0.dev0`; v1.0.0 has not been rewritten or retagged. This section
supersedes the old release state and proposed full-event architecture below.

The requested first 3D version deliberately retains **latest hit per pixel**:
one ordinary hit and one `(hit, associated t0)` pair for each of Beam and Light.
For overlapping windows, greatest qualifying `t0 <= hit` wins. Equal-hit late
triggers may revise t0. The backend stores exact integer `hit_tick-t0_tick`
beside existing IOG-local hit seconds, updates pairs coherently and preserves
the existing bounded shared history. It does not introduce an event builder.

The normal 2D/3D window is now `[t0,t0+190 us)`; the existing post-window
override remains. `RAW_DISPLAY_3D=1` enables optional paired state and the
**3D detector** view. Gate-off 2D keeps its original state allocation. One
collector still owns the same single PACMAN SUB set; receive/drain, normal
unrolling, raw timestamp <10 cut, PPS alignment, playback reserve and displayed
clock remain. There is no IOG6- or Beam-specific timing correction.

Geometry is generated offline from Flow develop commit
`5d63843212cd32d88de2c15d5ffa95b8d43b50a5`, indexed by all 337,600 raw pixel
IDs and checked over all four Hydra aliases. Axes are x drift, y vertical,
z beam. The actual Geometry resource yields a 304.31 mm maximum drift;
nominal mobility at 500 V/cm, 87.17 K yields 1.596452482154287 mm/us. The guide
explains why these differ from the detector-YAML drift length and configured
data velocity. Do not silently mix those choices or tune arbitrary offsets.
The live runtime loads binary/JSON only; h5flow is not a runtime dependency.

The new R3D1 binary extension is requested only while needed, over the existing
WebSocket. RDP2/RDT1/source protocols remain supported. Local pinned Three.js
renders normal anode activity plus optional drift candidates, with existing
source/mode/persistence/pause controls. Static geometry loads once. Optional
transport/WebGL failure falls back to 2D. This is approximate visualization,
not precision calibrated reconstruction; Beam t0 can be spill-referenced.

Read [3D implementation and commissioning](3d_display.md) and
[validation evidence/remaining gates](3d_validation.md). Synthetic tracks
exercise actual decode/timing/routing and browser reconstruction across all
8 IOGs. A current Run-3 Flow HDF5 file and live DAQ host were not supplied;
the read-only comparison tool and commissioning runbook are provided, but
real cosmics and DAQ/PacMon health have not been verified in this session.

Main new files: `geometry3d.py`, `drift_state.py`, `static/drift_data.js`,
`static/display3d.js`, packaged `geometry3d/`, `tools/generate_geometry3d.py`,
`tools/compare_flow_geometry3d.py`, `tools/synthetic_3d.py` and corresponding
geometry, pairing, protocol and WebGL tests. The test record distinguishes
available regressions from the environment-blocked real-ZMQ integration tests.

## Historical snapshot — 2026-10-06, after detector-wide live check

**State at this handoff:** detector-wide 2D is deployed by Bruno and visually
working; a small clock extension is prepared but still needs his installation
and acceptance. No stable v1 tag was created by this change. The next major
feature is 3D, after the 2D release gate. Do not confuse this project with the
NERSC `2x2-live-event-display` repository.

The following supersedes historical same-IOG-only and planned Beam/Light text.
The candidate trigger identifier remains `detector-wide-2d-1`; the new clock has
its own `/api/geometry.playback_clock = "pps-playhead-1"` marker. CLI version
`0.4.2` before release is intentional, not evidence that the patch failed.

### Implemented semantics

* Source routing is **IOG5 = Beam, IOG6 = Light**, per Bruno. Valid T words are
  propagated to all qualified IOGs after integer whole-PPS epoch translation.
  S83 and S72 are never triggers. Subtype remains diagnostic/filter metadata.
* Common timing uses `offset_i = header_second * R - local_sync_tick_i`, then
  `trigger_j = trigger_i + offset_i - offset_j`. Two consistent, ordered PPS
  observations qualify an epoch; pinned offsets are not silently changed.
  Suspect/stale epochs stop association. No frequency/subsecond phase
  calibration is performed. Header assembly time is not a latched PPS edge:
  stable whole-second bias cannot be excluded by consistency alone.
* Each IOG's existing normal unroller and independent 1.25-second-reserve
  playback are unchanged. Trigger matching uses default `[t0,t0+190 us)` in
  continuous ticks, including rollover, and shared bounded charge history for
  late triggers. Beam and Light retain separate per-pixel last-hit times.
* 2D rendering is a phosphor history, **not a unique event bunch per frame**.
  Different IOG playheads need not be simultaneous on the monitor. 3D must keep
  individual decoded/unrolled hits and trigger identity, not reconstruct from
  the reduced per-pixel phosphor arrays.

### New displayed-time clock

The read-only clock labels the **actual displayed cursor**, not packet arrival,
latest input frontier, trigger time, or browser wall time. It reports oldest
labelled pane, per-pane spread, all-configured-IOG coverage and approximate lag
against daq03 server wall time. Hover exposes each pane. Default Chicago and
optional UTC include full date, timezone and milliseconds.

The browser computes `epoch_seconds = last_header_second - last_sync_tick/R`
from fresh qualified anchors, then `display_unix_ms = 1000*epoch_seconds +
local_playhead_ms`. It deliberately ignores Unix-scale `offset_ticks` numbers
above JavaScript's exact integer range. This is a display label only; matching
stays in the existing Python integer-tick path. The time agrees with the
playhead used by Canvas. Afterglow can contain older hits and multiple triggers.

Pause captures/fixes the clock label and lag-at-pause, including through later
reconnects. Live labels require fresh RDP2 frames and status, collector health,
and qualified PPS metadata. Stale/disconnected/host/demo states never show a
fabricated live detector time. Unlabelled/buffering IOGs and negative lag are
visible. The existing status poll carries the new server wall timestamp and
observer session identity; no polling loop or subscription is added. All binary
protocols and collector algorithms remain unchanged.

### Live evidence supplied by Bruno

On `acd-daq03`, after installing the first detector-wide patch:

- Complete checkout: **222 passed, 5 skipped, 9.75 seconds** (before clock patch).
- Interval: 121 samples over 119.9884 s. IOG6: 162,148 mapped/display-selected
  hits, zero pre-cut late hits, and 2,600 received triggers. IOG5: zero pre-cut
  late hits and no received Beam triggers. Other groups had only small nonzero
  pre-cut late fractions, maximum approximately 4.03e-5.
- Each of IOGs1–8 received 2,587 routed Light triggers. Bruno saw yellow muon
  candidates throughout the display. This is strong qualitative encouragement,
  not a quantitative all-IOG phase/absolute-time calibration.
- `software_checks_passed=false`, solely reported issue: three invalid trigger
  times in both the source6 and aggregate routing counter views. These views
  must not be added as six failures. No reason-level raw trigger samples were
  supplied. Do not assume these were harmless boundary words or waive the flag.
- 2,600 receipt versus 2,587 routed counts were from different publication
  snapshots; the 13 difference is not itself measured trigger loss. Do not
  assert that all of the difference was sampling skew either.

Only the pasted diagnostic summary was supplied; `/tmp/detector-wide-live-check.json`
was not attached. The IOG6 hardware repair remains resolved, with no special
IOG6 timing correction to remove in this canonical candidate. General buffering,
late-hit aging, and raw timestamp `<10` rejection are not PACMAN-specific hacks.

### Release and deployment continuation

Apply the clock patch **on top of** the installed detector-wide candidate, not
bare GitHub main. At the start of this change, the GitHub branch listing still
showed only `main` at `6508a73`; the live feature work had not been pushed there.
Preserve/checkpoint any local changes before applying another patch. Do not
force-push, hard-reset, or discard manually applied patches. Keep the canonical
`/data/2x2-raw-event-display`, its `.venv`, and the existing one-collector launcher.

Read [clock/test documentation](playback_clock.md) and
[release/version/push instructions](v1_release.md). Push the development branch
without declaring stable v1. The stable release requires recorded operational
acceptance: resolve/classify the three rejected T timestamps; exercise real
Beam and Light routing and source controls; verify PPS-cycle identity/relevant
phase and clock label against an independent reference; verify normal
DAQ/PacMon health; rerun complete tests after the clock change. Operator logs
and acceptance must distinguish observed results from pending checks.

Use **`v1.0.0`** for the first approved 2D tag, updating both `pyproject.toml`
and `raw_display/__init__.py` first. Preserve the tag and start `feature/3d-events`
from it. The new docs contain no claim that this tag has already been made.

## Historical handoff (v0.4.2 baseline)

The history below is retained verbatim; the continuation snapshot above takes
precedence for implemented feature scope, post-repair evidence and release state.

**Purpose:** preserve the decisions, implemented behavior, operational discoveries and remaining work from the development conversation so another developer or chat can continue without rebuilding the context.

**Documentation prepared:** 2026-10-06. **Reviewed runtime baseline:** v0.4.2, GitHub `main` at `28a12e935eccda591373191373a90de5fd5ab818` before this documentation-only update. **Owner/operator:** Bruno Gelli. **Repository:** <https://github.com/BrunoGelli/2x2-raw-event-display>.

This is a handoff, not a claim that the deployment environment was inspected directly. Code behavior is grounded in the reviewed repository; on-detector results and the latest repair are user reports. Historical hypotheses are not silently promoted to established causes.

## 1. Read this first: current state and latest correction

The live 2D display works. Python batch ingestion, PacMon geometry, ASIC-time playback, the first-ten-ticks display veto, per-IOG receipt badges, optional trigger-window highlighting and a targeted timing audit have been implemented and exercised during the conversation.

**The IOG 6 timing problem is now resolved.** In the closing user update, Bruno reports that a real timing problem was found and fixed. This supersedes earlier messages describing IOG 6 as an open mystery or a blocker. The exact repair, configuration changes and post-fix metrics were not supplied; do not invent them.

The old numerical phase/delay analysis is historical diagnostic evidence. **Do not apply a 0.55156 s, 1.55156 s, one-second, or tile-8-specific correction based on it.** No such manual correction was found in the reviewed runtime. General 1.25 s playback buffering and preservation of late-hit ages remain implemented scheduling safeguards, not an IOG 6 workaround. This documentation does not change runtime delays or hardware settings.

The next major development is trigger-packet-based 3D reconstruction. It has **not** been implemented yet. The current trigger layer matches charge only within the trigger's own IO group. Common PPS epoch/frequency calibration, propagation across PACMANs, Beam/Light/Both source selection and a complete per-hit event builder remain planned.

### Compact start-of-next-chat briefing

> Continue `BrunoGelli/2x2-raw-event-display`, not the separate NERSC display. The working baseline is v0.4.2 / runtime commit `28a12e9`, feature build `trigger-audit-1`. Backend and the single PACMAN subscriber set run on acd-daq03; Canvas rendering runs in the ops01 browser; shifters use existing VNC/approved forwarding. HTTP/WebSocket must bind only to 127.0.0.1. Use `/data/2x2-raw-event-display` and its own `.venv`; do not resurrect competing bundles or `-next` as another deployment. Legacy 8+16-byte PACMAN framing and Packet_v2-compatible ASICs only. Batched decoding, geometry LUT, stateful PPS/S83 unrolling, bounded ASIC-time playback and raw timestamp <10 display veto are working. S83 RX and T-word trigger badges are implemented. Optional yellow/trigger-only selection is same-IOG only, default [t0,t0+190 µs). IOG 5 is the beam source and IOG 6 the light source according to Bruno's routing description; do not guess numerical subtype identities. The actual IOG 6 timing fault is now fixed: earlier offsets were diagnostic hypotheses, not a calibration to retain. Build the next 3D path from decoded/unrolled per-hit data, not the reduced phosphor state. Retain read-only/bounded/no-extra-subscription behavior and verify repository state before writing.

## 2. What this project is—and is not

The goal was a genuinely live, shifter-facing raw charge display without waiting for HDF5 rollover, packetization or Flow. Normal activity lights the physical pixel; a future trigger path should turn pixel position plus trigger-relative time into a 3D candidate event.

There are two separate projects:

| Project | Scope |
|---|---|
| `BrunoGelli/2x2-raw-event-display` | This project. Underground/DAQ-support host, live PACMAN ZMQ, raw phosphor and eventual triggered reconstruction. |
| `BrunoGelli/2x2-live-event-display` | Separate NERSC/nearline/Flow/JSON/Plotly deployment, with Spin-related planning. Do not import that architecture or command set into this handoff. |

The initial conversation considered Plotly for 3D and Canvas/WebGL for continuous activity. The implemented 2D frontend is plain HTML/CSS/JavaScript with Canvas. It does not need React, Node, a CDN, Plotly, or a database at runtime. No current 3D rotation feature should be claimed merely because rotation was discussed for a future view.

## 3. Machine responsibilities and immutable boundaries

**acd-daq03** is the support machine running PacMon, not the production recorder chosen for this deployment. The display runs a separate Python collector process plus a FastAPI process there. The collector subscribes to the configured PACMANs, decodes/maps data, maintains detector-time state, and publishes bounded snapshots/diagnostics.

**ops01**, in the same operations environment, runs the browser. The browser handles Canvas drawing, fading, controls, hover, pan/zoom and optional yellow layers. It requires no Python or ZMQ installation. Shifters access ops01 through their already established VNC workflow.

**HTTP/WebSocket bind address is always `127.0.0.1`.** The user explicitly rejected `0.0.0.0` or public/subnet-facing listening ports. Early chat examples using them are superseded and must not be copied. Use only the existing approved forwarding arrangement; no new firewall exposure is part of the project.

The observer never configures/powers/resets PACMANs or ASICs. It opens data SUB sockets on port 5556, no command socket on port 5555. One serving instance owns the subscriptions; browsers and audit clients do not create extra PACMAN connections.

Authoritative files, read at startup:

```text
/home/acd/acdaq/CRS_DAQ/daq0/crs_daq/io/pacman.json
/home/acd/acdaq/CRS_DAQ/daq0/crs_daq/RUN_CONFIG.json
```

The first provides `PACMAN_IO` / `io_group: [[integer, host], ...]`; the second supplies `io_group_asic_version_` for a once-at-startup compatibility check. Do not duplicate the IP list in the code. The deployed geometry comes from PacMon layout JSONs.

## 4. Implementation achievements and why they mattered

### 4.1 Python was retained by batching the actual expensive path

Initial traffic was estimated at about 330,000 hits/s. A later rate CSV averaged about 622,391 mapped hits/s. The browser never receives one event per raw packet: collector publication and web snapshots are about 10 Hz, while local rendering offers 15/30/60 FPS, default 30.

An early synthetic benchmark on daq03 showed the danger of benchmarking only large messages: eight-word messages decoded individually achieved only about 70,992 hits/s. Real probes showed around 7,400–8,000 messages/s on one source and commonly 5–10 words/message.

The correction was to drain up to 256 immediately available messages per source visit and join their bodies before one NumPy decode. Header validation remains per-message; fields/parity/geometry processing is vectorized over the accumulated words. The drain cap does not mean every batch fills, and there is no wait to collect exactly 256 frames.

The current defaults are receive HWM 4096 messages/source and a bounded drain of 256 messages/source, versus earlier 128 and 8. The decoder is specialized to the actual legacy Packet_v2 path. Rich per-packet Python objects, pandas and per-hit browser messages were deliberately excluded. Large-message/local benchmarks reached several million hits/s, but are not end-to-end production guarantees.

### 4.2 The apparent missing-data bug was a direction filter

The first image showed only about 1 Hz despite PacMon reporting many kilopackets/s. The subscriber was receiving structurally valid traffic. The bug was `accepted = parity & ~downstream`: almost all real valid data carried the downstream marker.

PacMon's charge/rate path does not reject data solely on that marker. Removing the rejection restored the mapped rate. The final policy accepts valid odd-parity DATA packets in either direction, and retains direction counters as diagnostics.

The later probe showed packet type 0 overwhelmingly dominant, negligible parity loss, mapped approximately equal to valid data, and zero malformed increments in the supplied sample. This established the working interpretation, not lossless transport.

### 4.3 PacMon geometry was reused exactly where possible

Four `geometry_mod*_v4.json` files provide the mapping. The optional downloader is pinned to PacMon commit `2cf0e2c7db056dd205efb7f41616c1795fa9ea67`; the live service does not download anything.

Mapping:

```python
module = (iog - 1) // 2
geometry_iog = 2 - (iog % 2)
local_tile = (io_channel - 1) // 4 + 1
geometry_tile = local_tile + 8 * (1 - (iog % 2))
```

All four Hydra IO channels belonging to a tile map to the same physical pixels. A dense array turns `(iog, io_channel, chip, channel)` into a pixel ID. Unknown addresses are counted, not plotted at zero. Geometry validation rejects missing/overlapping/off-grid/nonfinite input. Browser rasters retain tile gaps and orientation from the imported coordinates.

Do not freeze the geometry total from a screenshot. The conversation includes 39,200 for an IOG-1 subset, 337,600 for a full live startup, and a different synthetic demo count. These are configuration-dependent geometry counts, not enabled-chip measurements.

### 4.4 The display became a detector diagnostic almost immediately

The image showed approximately one-second coherent flashes on the v2a populations, with no comparable repeating response in module 2 / IOGs 5–6. The first implementation used host-consumption time, so software batching and delivery bunching had to be separated from real detector-time structure.

A one-off rate audit was added **inside the existing collector**, not as a second subscriber. It counted packets and distinct pixels at the same publication boundaries as the image, with rates calculated using actual interval durations.

The uploaded CSV had 38,080 rows: 64 tiles × 595 consecutive intervals over 59.926 s. Intervals ranged from about 100.005 to 102.467 ms, with a 100.667 ms median. The offline analysis found 59 common peaks at approximately one second. Distinct-pixel correlations among affected IOGs were 0.917–0.988; module 2 lacked the comparable recurring modulation. Timing precision is limited by the approximately 100 ms bins.

A telling example: IOG 1 tile 1 could produce roughly 3,700 packets from 10–25 distinct pixels in an interval, versus 3,857 from 161 distinct pixels at a peak. The phosphor view exposed the expanded population even when the packet-rate change was modest. IOG 4's quieter baseline made its packet-rate bursts especially clear.

The CSV analysis established a pre-rendering periodic population, not by itself its physical timing. ASIC-time playback was the next discriminator.

### 4.5 ASIC-time unrolling and buffered playback replaced arrival-time grouping

Legacy DATA words contain both the ASIC timestamp and a PACMAN receipt counter. S83 supplies the reset-period information; S72 is heartbeat. The decoder preserves original word ordering and accepted-hit indices so SYNC state can be applied before data are reduced to display arrays.

The streaming unroller follows the referenced `ndlar_flow` calculation while retaining state across batches. Defaults: 100 ns ticks, 10,000,000 ticks/reset, SYNC subtype 83. For each selected valid SYNC, round its counter to a positive integer number of periods; accumulate preceding increments per IOG. Charge time is computed as:

```python
crossed = receipt < raw_asic
hit_ticks = raw_asic % period + preceding_offset - crossed * preceding_sync_increment
receipt_ticks = preceding_offset + receipt
```

This handles the expected reset-crossing case and multi-period SYNC increments where the information exists. It is not a universal free-running-counter calibration. Pre-SYNC hits stay in warmup; invalid metadata and out-of-order data are visible. A raw decrease alone is not treated as an inferred reset.

Changing timestamps was not sufficient: a bounded playback buffer was needed to prevent delayed batches from flashing all at once. The canonical implementation waits for a 1.25 s detector-time reserve, advances a per-IOG cursor at 1×, freezes/re-buffers on underrun, and does not convert a long host pause into a catch-up jump. Future hits wait; late hits keep their old age. `np.maximum.at` prevents older hits overwriting newer pixel times.

The browser receives detector times and per-IOG clocks, interpolates within a short bound/frontier, and computes fading locally. Host monotonic time paces the image, not the assigned physical timestamp. The Unix seconds in the PACMAN envelope are ignored by this path, so NTP offset is not directly included in hit ages.

There were two competing v0.4.0 implementations during tool-access difficulties. The settled implementation is the remote detector-cursor/RDP2 path, **not** the earlier local 0.5 s host-anchored bundle. Do not combine their API names or timing semantics.

### 4.6 PPS-induced noise was identified and vetoed for display

The user reported that the flashes survived ASIC-time playback. Stephen Greenberg's messages, supplied by Bruno, explained that SYNC-induced noise is a known issue during self-triggering in v2a; data processing excludes the first ten clock ticks. His practical criterion was raw timestamps below 10.

The implemented cut is **raw ASIC timestamp <10**, not elapsed host time after a received SYNC and not modulo period. Tick 10 is retained. It applies to charge presentation across the selected groups. The unroller consumes unfiltered accepted data first; raw mapped counters and the host-time rate audit remain pre-cut. The user then reported a much cleaner live view with the PPS flashes gone.

The code did not eliminate electrical pickup or alter threshold tuning. It implements a display selection with visible counts and an explicit off switch: canonical `--min-raw-timestamp 0`. Earlier patch names such as `--sync-veto-ticks` are not the canonical interface.

Per-IOG `SYNC 83 RX` indicators were also added. They are driven by observed receipt metadata, not a fake 1 Hz animation, ignore heartbeat 72, and distinguish recent/stale/disconnected data. They are not NTP/hardware-lock or detector-playback markers.

### 4.7 Trigger badges and a separate yellow candidate layer work

`TRG RX` counts PACMAN T words, distinct from SYNC/heartbeat S words. Trigger counters/subtypes are available even without optional history matching. Numerical T subtypes have not been equated with beam/light by guesswork.

With `RAW_DISPLAY_TRIGGER_VIEW=1`, the browser offers all activity, yellow highlighted windows, and window-only activity. Default matching is `[t0,t0+190 µs)`, using unrolled detector times. A separate layer preserves the timestamp of a matched hit even if an unrelated hit later fires the same pixel. A late trigger can select previously retained charge from bounded history; future/late candidates follow the existing playback clock and retain their age.

Matching remains **same IOG only**. Bruno identifies IOG 5 as beam-trigger input and IOG 6 as light-trigger input. Only seeing IOG 6 candidates does not prove a missing IOG 5 feature: subtype counts, invalid T times and matching counters must be inspected. A Beam/Light/Both source selector and cross-IOG propagation are not implemented.

History defaults: 2 detector seconds, 500,000 hits, 4,096 chunks and 2,048 merged windows per IOG. Capacity/coverage failures are reported. This is candidate selection, not a saved event stream or 3D reconstruction.

## 5. IOG 6 investigation: important history, now closed operationally

The concern began with a large cumulative late-hit fraction concentrated in IOG 6 despite normal-looking reserves, no recorded underruns, and no timing-validation failures. The first-ten-ticks cut removed no IOG 6 hits in the supplied snapshots, separating this issue from the PPS-noise population. Subsequent interval fractions around 42–47% showed it was ongoing at that time.

The opt-in late audit was built to localize it without another subscription. It reuses existing unrolling results and keeps fixed tile/chip counters, histograms and only 12 recent diagnostic samples. It reports model-dependent inferred lag, not independent network delay.

A later 15.547 s report localized almost all late hits to tile 8: 2,060/4,601 selected hits, or 44.77%, versus 12/28,514 on the other active tiles. Tile 5 had no selected hits. Every selected IOG 6 hit had raw ASIC timestamp above one period, receipt below one period, and `receipt < raw`. The original uploaded report is historical and predates the reported repair.

The first late sample had raw ASIC counter 1,454,016,253, phase 4,016,253 and receipt 9,532,298 ticks. The phase difference was 0.5516045 s; the unroller's previous-period subtraction made the inferred lag 1.5516045 s. Twelve retained late samples clustered near a 0.55156 s circular phase difference.

Under a fixed phase-offset model with approximately uniform activity in PPS phase, the then-current formula predicted two inferred-age populations separated by one second and a late fraction around 44.84%, close to the observed 44.77%. That was a strong diagnostic hypothesis about the interaction between counter behavior and epoch assignment, **not a calibrated correction or proof of a physical 1.55 s transit delay**.

**Latest status:** Bruno confirms an actual timing fault existed and is fixed. Do not reopen this as an unresolved blocker merely because the old JSON remains attached. Do not claim the exact cause was NTP, free-running counters, FIFO mode, missing reset distribution or a particular hardware action: none was confirmed in the final update. Normal diagnostic instrumentation remains useful for future regressions.

## 6. Why “read-only ZMQ” was not treated as automatically harmless

The display is intentionally a lossy observer, not a participant in acquisition control. It must never command hardware. However, an added SUB connection still costs publisher/network/host resources.

Early operation disturbed PacMon. The efficient batched implementation appeared much better to the user. Several changes occurred together—batching, larger HWM/drains and a specialized decoder—so their individual causal contributions were not measured separately.

The development review also found public legacy PACMAN dataserver code that reused a zero-copy message buffer through a `msg_ready`/release callback and included an application-level wait, with a one-second timeout in one implementation. That means ordinary PUB socket semantics alone cannot prove that all deployed publisher applications are immune to a poorly draining observer. The deployed server build was not verified against that code in this conversation.

A release callback is not a receiver application acknowledgement. The risk is coupling introduced by surrounding publisher/buffer management, not proof that a slow browser directly blocks a raw PUB send. The earlier suggestion to deliberately stall a subscriber during production acquisition was withdrawn.

The settled operating approach is: one receiver set, drain promptly, vectorize/bound downstream work, keep web clients away from acquisition flow, and compare independent DAQ/PacMon health when changing workload. Never claim zero transport loss from received counts. Do not aggressively throttle the receiver, casually add a second probe/server, or reset a PACMAN to solve a web problem. A nonblocking mirror of already-received data is a future architectural option, not part of this deployment.

Bruno explicitly chose to continue with the working topology and revisit impact if symptoms recur. Do not restart that whole investigation as a prerequisite to every frontend improvement.

## 7. Current runbook and diagnostics

The settled checkout is `/data/2x2-raw-event-display`, with its own `.venv`. An example serving command for the implemented candidate layer is:

```bash
cd /data/2x2-raw-event-display
RAW_DISPLAY_TRIGGER_VIEW=1 \
/data/2x2-raw-event-display/.venv/bin/python -m raw_display serve \
    --time-basis asic \
    --geometry-dir /data/2x2-raw-event-display/layout \
    --host 127.0.0.1 --port 8765
```

No late audit is required by default now that IOG 6 is fixed. To investigate a new issue, add `RAW_DISPLAY_LATE_AUDIT_IOGS=6` (or another configured IOG) to that same instance at restart. `RAW_DISPLAY_RATE_AUDIT=1` enables the independent host-time tile-rate control capture. Do not start another collector just to obtain these diagnostics.

Runtime identification:

```bash
curl -fsS http://127.0.0.1:8765/api/geometry |
jq '{time_basis, feature_build, trigger_view_enabled}'
```

Expected: ASIC mode, `trigger-audit-1`, and an enable flag matching startup. `/api/status` supplies raw counters/playback state; `/api/observers` supplies T subtype/matching data; `/api/timing-audit` supplies the enabled timing audits. The audit route exists even when disabled.

Canonical counter vocabulary differs from an earlier local patch:

| Earlier local name | Canonical top-level name |
|---|---|
| `sync_vetoed_hits` | `post_sync_filtered_hits` |
| `display_eligible_hits` | `display_selected_hits` |

Nested timing counters additionally require valid initialized timing. `mapped_hits` remains pre-cut. `pre_cut_late_hits` distinguishes the population before the veto. Cumulative `late_hits` can grow harmlessly; recent fractions, lateness distribution and overflow/invalid/underrun counters matter. None certifies complete upstream transport.

General default settings: 100 ns ticks, 10^7 reset ticks, S83, 1.25 s reserve, 500,000 pending hits/IOG, HWM4096, drain256, snapshots10Hz, browser30FPS. The canonical cut option is `--min-raw-timestamp`; the full README lists the environment variables and API formats.

## 8. Git, environments and deployment: hard-earned lessons

Several working implementations existed at once because direct GitHub write access varied between turns. This led to local patches/bundles and independent remote commits solving similar problems. A matching `0.4.0` or `0.4.1` version string did not imply identical code.

Historical checkpoints:

| Commit | Role |
|---|---|
| `a03db3b` | Initial observer/geometry/Canvas implementation. |
| `250e633` and follow-ups | Direction-filter correction, expanded diagnostics and loopback enforcement. |
| `2f01991` | Batched, fixed-v2 baseline with Python 3.9 compatibility. |
| `d9eb675` | Alternative local ASIC-time bundle; retained as backup, not canonical. |
| `db6892a` | Canonical remote ASIC-time/RDP2 implementation. |
| `4db6fc9` | User's local SYNC-veto patch commit; historical sibling implementation. |
| `ebd7c91` | Canonical remote post-SYNC policy and S83 indicators. |
| `f40d843` | User successfully pushed trigger-window/audit extension. |
| `4fe3e37` | Audit-client error explanation and deployment troubleshooting. |
| `28a12e9` | Explicit v0.4.2 label for the working trigger/audit feature set. |

The temporary `feature/trigger-audit` branch/worktree at `/data/2x2-raw-event-display-next` worked and was pushed, but the original checkout still served old code. The decisive log showed the `-next/.venv/bin/python` executable importing `raw_display` from the old current directory.

**Absolute Python path is not sufficient with `-m`/`-c` when the current working directory contains an old package.** Always check current directory, interpreter, imported package and server file. The two environments can both display `(.venv)` in the prompt. A subshell `( ... )` discards its `cd`/activation on exit. `pip install -e .` points at the directory being installed; it does not fetch/update Git.

Symptoms of the old serving source were `feature_build: null`, no trigger badges, and HTTP404 on `/api/timing-audit`. Setting new environment variables could not add missing routes to that old application. Once checkout, venv and imports were unified, the route and feature metadata worked.

The `tools/inspect_timing_audit.py` “file not found” error from `~` was simply a relative-path/cwd problem; running it from the repo worked. The same principle applies to `layout` and other relative paths.

Use one canonical deployment and incremental Git changes. Keep backups rather than force-pushing or blindly merging incompatible sibling implementations. The earlier interactive askpass warning and pip-upgrade notice were not the reason for non-fast-forward rejections. Tool permissions are turn-specific; a future agent must inspect current capabilities and only claim a push after verifying it.

## 9. Evidence and validation limits

The user ran the software against the detector and reported successful live views, the disappearance of PPS flashes after the veto, API feature confirmation, and detailed rate/timing output. User installation logs recorded 29 passing tests early, 112 passed/1 skipped for one local SYNC-patch variant, and 137 passed/2 skipped for the trigger/audit installation. These are historical per-checkout results, not interchangeable expected test counts.

Synthetic tests developed during the session covered wire fields/parity, geometry aliases, SYNC boundaries, missing-period increments, late/out-of-order aging, display cuts, bounded queues, browser clocks/indicators, trigger windows, audits and localhost ZMQ/HTTP/WebSocket integration. Several graphical tests used offline fixtures, not real ops01/VNC rendering. Never relabel a fixture render as on-detector validation.

The large NERSC packet directory supplied by Bruno was not accessible from the earlier development environment. No successful real-file download/replay from it was established. An offline HDF5/range-reading validator exists, but formula agreement does not validate arbitrary hardware counter assumptions.

Important retained evidence files from the conversation:

- `tile_rates.csv`: host-time rate/unique-pixel audit, approximately 60 s; not individual ASIC timestamps.
- `tile_rate_burst_analysis.zip`: plots and analysis of the periodic population, derived from that CSV.
- `iog6-timing-audit.json`: pre-repair IOG 6 report and selected late samples; historical, not current system health.
- Uploaded installation logs: successful commits/tests, branch divergence and the old-checkout import path.

The handoff does not upload these raw diagnostic files to GitHub or expose their data as a new public artifact. Their numerical lessons are summarized here. The latest IOG 6 resolution is Bruno's operational update; no post-fix regression run was performed for this documentation task.

## 10. Historical proposal: separate event building and 3D

Superseded for the current iteration by the latest-hit/t0 design in the
2026-10-07 continuation above. This records the earlier discussion only.

Start from the working canonical architecture rather than replacing it. The natural input to a new event builder is the decoded, unrolled per-hit stream **before** it is reduced to one latest timestamp per pixel.

Planned sequence:

1. Confirm/configure beam source IOG5 and light source IOG6 and their observed T subtypes. Add a source-aware selector; do not infer source identity from subtype alone.
2. Establish a common PPS cycle reference across IOGs. Preserve unrounded SYNC intervals to estimate clock-rate differences; assess offsets and uncertainty rather than just matching RX flashes.
3. Express triggers and charge on that shared detector clock, respecting the actual ASIC/PACMAN counter convention now that the IOG6 fault has been repaired.
4. Retain bounded per-hit windows and allow late arrivals before declaring an event ready. Source coverage, drops and calibration quality should be visible.
5. First expose `(transverse pixel position, t_hit-t0)` as a trigger-relative candidate view. Then map drift distance with a configured velocity, anode coordinate and drift sign, respecting detector bounds and trigger offsets.

The discussed 190 µs window is an initial configurable choice, not a proven maximum drift time for every setting. Beam spill timing and light-trigger timing need not be interchangeable physical t0 definitions. Plotly.js was a proposed 3D renderer, not an already integrated dependency. Serve any future assets locally rather than through a CDN.

A shared PPS offers a useful clock-rate reference: compare observed interval ticks with 10^7, but also match physical cycles and validate fixed delays. This calibration was discussed and is not yet implemented. Do not import the old IOG6 phase-offset hypothesis as a shortcut.

## 11. Source map for the next developer

The [full README](../README.md) is the operational/technical manual. At the reviewed baseline:

| Area | Source |
|---|---|
| Decoder and retained word order | `raw_display/codec.py` |
| PacMon geometry and lookup | `raw_display/geometry.py` |
| Unrolling and playback | `raw_display/timing.py` |
| Live ASIC pipeline | `raw_display/timed_runtime.py`, `timing_cli.py` |
| ASIC web API/RDP2 | `raw_display/timed_server.py` |
| Host/demo path | `raw_display/runtime.py`, `server.py` |
| Post-SYNC display-only policy | `raw_display/post_sync.py` |
| Trigger candidates and optional state | `raw_display/trigger_windows.py`, `observer_runtime.py` |
| Rate and late audits | `raw_display/rate_audit.py`, `late_audit.py` |
| Canvas and trigger UI | `raw_display/static/display.js`, `trigger_view.js` |
| Offline/user-facing tools | `tools/tile_rate_audit.py`, `inspect_timing_audit.py`, `benchmark_timing.py`, `validate_packet_timing.py` |

Primary code snapshot: <https://github.com/BrunoGelli/2x2-raw-event-display/tree/28a12e935eccda591373191373a90de5fd5ab818>.

Upstream timing reference: <https://github.com/DUNE/ndlar_flow/blob/a0eb2f364e35340d67fd73dc09a8e8f847211a58/src/proto_nd_flow/reco/charge/raw_event_builder.py>.

Upstream mapping/wire reference: <https://github.com/BrunoGelli/2x2Pacmon/tree/2cf0e2c7db056dd205efb7f41616c1795fa9ea67>.

**Final instruction for continuation:** treat IOG6 as repaired, the 2D/raw observer as working, PPS filtering as display-only, common-epoch/3D work as unfinished, and acquisition safety/loopback-only serving as non-negotiable. Verify the next repository head before making incremental changes; do not reconstruct an obsolete bundle from this historical narrative.
