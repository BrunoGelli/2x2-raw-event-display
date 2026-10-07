# Optional 3D detector view

This is **online approximate drift visualization**, not precision calibrated
reconstruction. It extends the accepted `v1.0.0` commit
`cd4f5632fe42e05ab41cb9bd5202f1fde3c5228a` on `feature/3d-events`.
The stable tag is untouched. The development package is `1.1.0.dev0`.

## Operator behavior

Enable `RAW_DISPLAY_3D=1` on the existing ASIC-time display instance, then choose
**View → 3D detector**. The flag also enables ordinary trigger matching.
Without it, 3D arrays/queues, artifact loading and 3D transport are absent.
The default 2D/3D candidate window is now `[t0,t0+190 us)`; the existing
`RAW_DISPLAY_TRIGGER_POST_US` override still works. The endpoint is exclusive.

| Trigger view | 3D behavior |
|---|---|
| All hits | Latest ordinary hits glow on their physical anode planes. |
| Highlight windows | Green anode activity plus yellow reconstructed candidates. |
| Window hits only | Yellow reconstructed candidates only. |

The existing Beam / Light / Beam + Light selector applies independently.
IOG5 supplies Beam t0; IOG6 supplies Light t0; eligible triggers propagate over
the existing qualified PPS epochs. Beam points are **Beam-referenced drift
candidates**: spill timing can differ from ionization time. No extra Beam or
IOG6 offset is applied.

Drag to orbit, wheel to zoom, double-click or **Reset zoom** to reset the camera.
Persistence and the displayed PPS-labelled clock are shared with 2D. Pause
freezes normal hits, paired hits, their fading clocks and the displayed clock;
camera movement remains possible. Ingest continues. If 3D has never received a
snapshot before pausing in 2D, resume once in 3D to obtain one; live pairs are
never substituted into an older paused snapshot.

Switching views does not restart the collector or reconnect the WebSocket.
Geometry and Three.js are loaded once, when 3D is first selected. Returning to
2D stops 3D frame requests after the next normal acknowledgement. An in-flight
frame may still arrive. Corrupt optional frames, a failed geometry fetch or
WebGL failure return the user to working 2D. An incompatible geometry artifact
at server startup disables 3D with a warning; 2D still starts.

## Deliberately limited state

There is one latest ordinary hit per pixel, one latest Beam-associated hit,
and one latest Light-associated hit. For each source, choose the **largest t0
at or before that hit** satisfying `0 <= hit_tick-t0_tick < post_ticks`.
Overlaps therefore choose the smallest nonnegative drift time. Beam and Light
can retain different hit times and different t0s on the same pixel.

The optional layer retains an exact integer `dt_ticks` beside the existing
IOG-local hit timestamp. This is equivalent to retaining the associated t0.
Pairs update together: greatest hit timestamp wins; equal-hit ties choose the
latest qualifying t0. A late trigger can revise a retained hit's t0 without
changing its hit timestamp or increasing matched-hit counters again.

The existing bounded recent-hit history is shared by both sources. There is
no full event collection, arbitrary pixel hit history, event navigation or
track finding. Multiple events can coexist in afterglow and repeated hits on
one pixel can erase parts of a track. This is an intentional first-version
simplification. Existing independent IOG playheads remain independent.

With a nonzero pre-window override, 2D can still select pre-trigger hits, but
3D rejects a pair without a qualifying preceding trigger. Unqualified or stale
PPS timing revokes and clears all source pairs and pending associations.

## Authoritative geometry and numerical choices

The generator imports the actual Flow Geometry resource **only during offline
development**, without running an h5flow workflow. The live service imports
only NumPy and the generated binary/JSON product.

| Item | Pinned value/source |
|---|---|
| Flow repository | DUNE/ndlar_flow, `develop` at `5d63843212cd32d88de2c15d5ffa95b8d43b50a5` |
| Resource config | `yamls/proto_nd_flow/resources/GeometryData.yaml` |
| Detector input | `data/proto_nd_flow/2x2.yaml` |
| CRS inputs | Four module-specific `layouts_v5` files listed and hashed in the artifact |
| Raw index | Existing PacMon pixel IDs, with all four Hydra IO aliases |
| Coordinates | x = drift, y = vertical, z = beam; served in mm |
| Electric field | 500 V/cm |
| Temperature | 87.17 K |
| Nominal velocity | 1.596452482154287 mm/us, Flow mobility parameterization evaluated at E,T |
| Maximum drift | 304.31 mm, from actual `Geometry.max_drift_distance` |
| Corresponding full-drift time | 190.61638439081983 us |
| Normal association window | 190 us = 1,900 ticks at 100 ns |
| Tiny boundary tolerance | 0.5 mm; only excess within this tolerance may clamp |

Two differences from approximate planning values are explicit:

* The detector YAML contains `drift_length: 30.27225` cm, but these CRS layout
  files omit `drift_length`. The referenced Geometry resource instead derives
  30.431 cm from half the anode spacing; its cathode thickness is zero. The
  artifact follows the actual resource, rather than silently mixing inputs.
* The data LArData YAML also has `vdrift: [1.583]`. This is a configured data
  value, distinct from the field-based nominal parameterization chosen for
  this approximate display. Both values and the choice are recorded. Do not
  tune offsets to force agreement with a differently configured Flow output.

For each raw pixel, the generated record gives `x_anode_mm, y_mm, z_mm,
drift_dir`. Drift direction comes from Flow tile orientations, never IOG
parity. Module 0 uses IOG1/2, module 1 IOG3/4, module 2 IOG5/6, module 3 IOG7/8.
The formula is:

```python
dt_ticks = hit_tick - trigger_tick
drift_mm = dt_ticks * tick_seconds * 1e6 * v_drift_mm_per_us
x_mm = x_anode_mm + drift_dir * drift_mm
# y_mm, z_mm remain the physical pixel coordinates
```

At zero drift the point is exactly on the anode. Increasing positive drift
moves toward the cathode. Negative/large out-of-range values are hidden and
counted, not silently projected onto a boundary. The usual 190 us interval is
inside this geometry's full drift, so its normal candidates need no clamp.
No field distortion, space-charge, lifetime, recombination or charge corrections
are included. Host arrival time and Unix-scale ticks never determine drift.

## Generating and checking the artifact

This runs on a development machine; h5flow, SciPy and YAML are **not** DAQ
runtime dependencies. Use a clean checkout at the pinned Flow commit and
install its development dependencies, including h5flow, into that environment.

```bash
raw-display fetch-geometry --geometry-dir layout
python tools/generate_geometry3d.py --flow-root /path/to/ndlar_flow \
  --geometry-dir layout --out raw_display/geometry3d
```

The generator validates all 337,600 pixels and 1,350,400 electronics aliases.
It independently checks positions using the YAML address mapping, pixel pitch,
tile rotations and module translations; compares module bounds; checks common
anodes, +/-1 signs, and full drift to each cathode; then writes:

* `pixels.bin`: 16 bytes per raw pixel: little-endian float32 x/y/z, int8 sign,
  three reserved zero bytes. Binary SHA256:
  `7c1f7aabf0cf75fd4cd9d062b3f2762a45e0482cc23f764c38fdaad4b942ae86`.
* `metadata.json`: Flow ref/commit and input hashes, raw layout hashes and
  per-IOG pixel identity hashes, units, velocity assumptions, bounds, samples
  and generation validation. Subset IOG serving remaps contiguous slices in
  the existing raw ID order and exposes a separate served-byte hash.

The runtime refuses a changed raw layout rather than guessing an alignment.
Regenerate/revalidate after an authoritative mapping change. An explicit custom
artifact directory can be supplied through `RAW_DISPLAY_3D_GEOMETRY`.

No representative Run-3 Flow HDF5 file was supplied in this implementation
session. The following **read-only offline** comparator is provided for that
remaining independent check:

```bash
python tools/compare_flow_geometry3d.py /path/to/current-flow.h5 \
  --flow-root /path/to/pinned/ndlar_flow --geometry-dir layout \
  --max-hits 100000 --offset 0 --dt-us 100
```

It follows raw-hit → packet references, compares `(x_pix,y_pix,z_pix)` in cm
against the artifact in mm, then compares the same chosen drift distance with
Flow's basic `get_drift_coordinate` using the file's stored anode/sign lookup.
It reports the file's drift velocity separately. Its chosen synthetic dt is
not a measured interaction t0. Repeat at several offsets to cover the file;
understand every geometry mismatch before live 3D commissioning.

## Transport and failure isolation

`GET /api/geometry3d.bin` serves the static geometry once. `/api/geometry`
includes `view3d_enabled`, `view3d_error`, `view3d_build: 3d-candidates-1`, and
`geometry3d` provenance. Disabled 3D returns HTTP 404 for the binary endpoint.

RDP2, RDT1, RDB1 and RDL1 are unchanged. R3D1 is optional:

| Offset | Type | Meaning |
|---|---|---|
| Header 0 | 4 bytes | ASCII `R3D1` |
| Header 4 | uint32 LE | Same snapshot sequence as following RDP2 |
| Header 8 | uint32 LE | Number of changed pairs |
| Record 0 | uint32 LE | Existing raw pixel ID |
| Record 4 | uint32 LE | Source 0 = Beam; 1 = Light |
| Record 8 | float64 LE | Latest hit, IOG-local detector seconds |
| Record 16 | int64 LE | Exact integer `hit_tick - t0_tick`; -1 = no valid preceding t0 |

Header size is 12 bytes; record size is 24. Unseen hit seconds use -1.
An update is emitted when either member of a pair changes, including t0-only
reassociation and explicit clearing. JavaScript decodes the integer with
`getBigInt64`, checks the safe range, then converts the small difference to
Number. The browser never subtracts global Unix-scale floating-point ticks.

R3D1 precedes the canonical RDP2 and uses its existing single acknowledgement.
Old clients continue sending the sequence as a string. New clients may send
`{"ack": sequence, "view3d": true/false}`; this changes the next snapshot's
optional stream without reconnecting. Initial `?view3d=1` also works. Re-enabling
starts with a full pair snapshot. Slow clients still skip intermediate states
under the existing timeout and bounded one-in-flight policy.

The collector publishes hit and dt arrays under the same existing short lock;
the server copies coherent snapshots and does all encoding outside that lock.
No browser network wait is introduced into PACMAN draining.

## Performance and diagnostics

The renderer keeps fixed-capacity buffers, compacts only active latest pixels
for GPU drawing, and fades points using per-IOG displayed time. Small 64-second
time origins keep GPU float32 fading precise across long sessions. Drift x is
computed from integer delta ticks before conversion to GPU coordinates.
The existing 15/30/60 FPS setting can reduce render work independently of
collection. Hidden 3D does not render or receive R3D1 data.

`rawDisplayDiagnostics().view3d` reports requested state, R3D1 bytes/frames,
current rejected/clamped retained-pair counts, point counts and draw count.
Counts of rejected/clamped pairs describe the current retained state, not a
cumulative unique-hit counter. `/api/observers` includes
`association_trigger_limit_drops` and existing pending/history drop counters.
`/api/status.view3d` reports 3D clients, sent bytes and optional encoding errors;
`websocket_binary_bytes_sent` includes all binary protocols but excludes JSON,
WebSocket/TCP framing and geometry HTTP transfers.

At the full geometry size, the additional dt arrays are 5.15 MiB in the
collector and 5.15 MiB in shared memory. The optional server snapshot adds the
same amount. Pending pair queues add 8 bytes per retained pending hit over the
ordinary trigger layer; their existing capacity limits remain enforced. Bounded
unmerged trigger lists are capped separately because merged windows cannot
identify a unique t0. There is no per-event or unbounded work queue.

See [validation and commissioning status](3d_validation.md). Live commissioning
must still measure collector CPU/RSS, browser FPS and bandwidth with the gate
off, gate on in 2D, and 3D selected. Browser selection does not change collector
state allocation or subscription topology.

## Commissioning on the existing instance

Keep the existing approved loopback/VNC arrangement. Stop only the display
instance before updating its code; do not start a probe or second collector.
Preserve local changes, fetch `feature/3d-events`, install from the intended
checkout and run tests. First verify normal 2D with the feature gate off.

Then restart that same instance with:

```bash
cd /data/2x2-raw-event-display
RAW_DISPLAY_TRIGGER_VIEW=1 RAW_DISPLAY_3D=1 \
  .venv/bin/python -m raw_display serve --time-basis asic \
  --geometry-dir /data/2x2-raw-event-display/layout \
  --host 127.0.0.1 --port 8765
```

Check `/api/geometry`, all 8 qualified PPS epochs, normal IOG timing (including
IOG6), unchanged subscriber count and DAQ/PacMon health. Select 3D and inspect
a Light-triggered cosmic muon: correct anodes, inward drift on both sides,
coherent track, no mirrors/axis swaps and no points past cathodes. Exercise
Beam/Light/Both, all/highlight/window-only, persistence, pause, fullscreen,
camera controls and repeated 2D/3D switching. Record resource and health
comparisons. Revert to gate-off 2D if necessary; do not reset detector hardware.

For an explicitly synthetic, static development preview without a collector:

```bash
python tools/synthetic_3d.py --geometry-dir layout --port 8766
```

This binds only to 127.0.0.1 and labels itself SYNTHETIC. Its static snapshot
does not certify live PPS labels, acquisition health or real event geometry.
