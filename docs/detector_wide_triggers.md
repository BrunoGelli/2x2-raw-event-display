# Detector-wide 2D trigger candidate — verification before v1

**Status: unreleased. No v1 tag or deployment approval.**

Based on remote `main` at `6508a73d77f960a327874102be5091b585fb975f`
(runtime v0.4.2, `28a12e9`). The original 2D build used `detector-wide-2d-1`. The live receipt alignment build uses `receipt-pps-1`.
The package version stays at the baseline until a release is approved. This
candidate supersedes the same-IOG-only trigger descriptions in the historical
README, PROJECT_CONTEXT and trigger_audit documents; it does not add 3D.

## Behavior

PACMAN **T words on IOG 5 are Beam**, and **T words on IOG 6 are Light**, according
to Bruno's confirmed routing. Numeric subtype is retained and may still be
filtered by `RAW_DISPLAY_TRIGGER_TYPES`; it does not determine Beam/Light.
T words elsewhere are counted as unexpected sources, not broadcast as events.
SYNC/heartbeat words never become triggers.

Each eligible trigger is translated to every configured, qualified IOG's local
detector epoch. Its half-open charge window remains `[t0-pre, t0+post)`, default
`[t0, t0+190 microseconds)`. Missing/unqualified targets are counted and skipped.
A trigger before qualification is not retrospectively invented; target hit
history can recover eligible charge received before a later trigger.

The browser has independent **All hits / Highlight windows / Window hits only**
and **Beam + Light / Beam / Light** controls. Both source histories are available
without restarting reception or reconnecting the WebSocket. A newer light hit
cannot erase an older beam hit on the same pixel. Source selection does not
change the normal All-hits population.

`BEAM RX` on IOG5 and `LIGHT RX` on IOG6 identify the streams that physically
received T words. Other planes do not acquire fake receipt counts or badges.
The *hit selection* propagates detector-wide, not the physical-receipt telemetry.
A global qualification note names unavailable targets. An empty plane is not an
efficiency measurement: a trigger need not produce charge in every plane.

## Live PPS qualification

Each board counts valid SYNC-83 intervals in wire order. Its local tick origin
can differ from its neighbors. The UNIX header labels PACMAN message assembly.
The following observations associate a local interval with a whole UNIX second.

* Use complete DATA envelopes containing parity-valid type-0 receipt times.
  Their full observed receipt range must fall in [200, 300) or [700, 800) ms
  after a PPS. Ignore envelopes containing a reset. FIFO-delayed ASIC hit times,
  trigger words, geometry selection and the post-SYNC display veto supply no votes.
* Each band must cover two distinct local PPS intervals. Both must agree on
  `epoch_second = header_second - local_interval`. At least one reset must be
  labelled. Its header and subsequent reset labels can imply that origin or one
  second earlier. Evidence takes effect after its envelope, in recorded order.
* A contradiction, malformed reset, or stale PPS revokes the board. A fresh
  labelled reset and new evidence in both bands can restore the original pinned
  epoch. A collector restart begins a fresh timing session. Live routing waits
  for receipt evidence even when all reset headers agree. Quiet boards and
  historical free-running data with insufficient corroboration stay local.

For a cross-board trigger both source and target need fresh epochs. Translate
in integer ticks with `target_t0 = source_t0 + (source_epoch - target_epoch) * R`.
Then apply the configured beam-only delay and the existing drift window.
Source and target on the same PACMAN already share a local hardware clock.
Revocation clears pending windows, painted hits and drift pairs only for routes
that used that board's epoch. Unmatched local charge history remains available.

In `/api/status`, inspect `trigger_alignment.common_timing.sources` for each
board's `status`, `method`, `epoch_second` and `receipt_intervals` (early, late,
each capped at two). The kiosk clock uses the same qualified origin. Independent
playheads can still have different playback lags. Absolute UTC and subsecond
inter-board phase require a separate reference.

### Run 70065 replay

The 01:36:02 CST packet file from November 22, 2026 yielded a steady receipt
epoch on all eight IOGs. Of 3,278 IOG 6 LRS words, 3,246 had valid local
times. The replay compared 2,933 projected LRS triggers in the common board
coverage, trimming four seconds at either end and excluding ±5 ms around PPS.
Counts are mapped, display-selected hit–trigger pairs in half-open 190 µs windows.
Overlapping windows can count the same hit twice. From the checkout with the
validation dependencies installed, reproduce the table with:

```sh
python tools/replay_pps_coincidence.py packet-0070065-2026_11_22_01_36_02_CST.h5 --geometry-dir layout
```

| Target IOG | −1 s | Routed window | +1 s |
|---|---:|---:|---:|
| 1 | 1,095 | 14,136 | 1,269 |
| 2 | 1,198 | 11,525 | 1,286 |
| 3 | 716 | 14,737 | 904 |
| 4 | 1,371 | 11,092 | 1,342 |
| 5 | 12,318 | 31,374 | 12,656 |
| 6 | 2,086 | 22,456 | 1,908 |
| 7 | 5,939 | 21,493 | 5,463 |
| 8 | 2,350 | 12,014 | 2,254 |

## IOG6 repair and unchanged protections

No IOG6-only timing constant, tile8 offset, 0.55156/1.55156-second adjustment, or
one-second IOG6 correction was found in the reviewed canonical runtime. There
was therefore no such runtime correction to remove. None is introduced here.
IOG6 appears specially only in the explicitly requested **Light source routing**
(and old diagnostic fixtures/documentation).

`timing.py`, `post_sync.py`, the canonical raw unroller, raw timestamp `<10` veto,
late-hit age preservation, and the general 1.25-second playback reserve are
unchanged. The optional late audit is retained as generic instrumentation and
is disabled by default. Its availability is not an IOG6 timing workaround.
The repair is accepted as Bruno's operational report; its exact physical cause
and intervention are not asserted by this candidate.

## Bounds, performance and safety

One charge history is shared by Beam and Light per target IOG: default 2 detector
seconds, 500,000 retained hits, and 4,096 chunks. Two source bits per retained hit
prevent double counting within each source. Conservative cached chunk bounds
avoid scanning unrelated history for each new trigger window.

Window lists and presentation queues are bounded separately **per source**:
2,048 merged windows and the existing DueLayer pending-hit/chunk limits. At most
2,048 eligible T words per source batch are routed; excess work is dropped and
counted. There are two source-specific last-hit arrays plus a union array for
legacy clients. This adds fixed memory, copies and CPU; it is not zero overhead.
Large readout delays, overflow and absent PPS can make candidate selection
incomplete. No counter certifies lossless upstream transport.

The same collector owns the same SUB sockets: data port 5556, HWM4096,
drain256, original priority and publication cadence. No command sockets,
configuration writes, hardware resets or detector-side changes are added.
HTTP/WebSocket bind remains `127.0.0.1`. Browser controls do not add subscriptions.
The verification helper uses only the existing loopback HTTP API, rejects
redirects, ignores proxy settings, and never opens a PACMAN socket.

## API and protocol

`/api/geometry` adds `feature_build: detector-wide-2d-1`,
`trigger_source_protocol: 1`, and `trigger_sources: {beam: 5, light: 6}`.
`trigger_scope` and `timing_alignment` describe the qualified/provisional scope.

`/api/observers` version 2 reports `session_id`, `common_timing` (per-IOG status,
integer offsets, last labels, freshness and failures), `trigger_routing`
(source/target skips and per-target Beam/Light routed counts), and `sources`
(per-target history plus per-source matched/drop/window counts).
`/api/status.trigger_alignment` carries the same latest qualification snapshot
and its capture age; the UI reuses its existing status polling.
`hardware_phase_verified` is deliberately false: this algorithm cannot measure it.

Existing RDP2 normal frames and their acknowledgements are unchanged. A client
requesting `trigger_windows=1&trigger_sources=1` receives RDB1 (Beam), RDL1
(Light), then RDP2, with a single acknowledgement of the RDP2 sequence. New layer
records use the RDT1 layout: 12-byte header, then `(uint32 id, float64 seconds)`;
`-1` clears a tag. Old trigger clients receive the union RDT1 layer. Binary-only
clients continue to receive RDP2 alone. The normal state, both source layers and
clock records are captured coherently under the existing snapshot lock.

## Install only into the canonical checkout

Stop the **existing display service/process** through its usual mechanism before
changing the serving package. Do not stop acquisition or PacMon, start a second
collector, or create another deployment directory/virtual environment.
Copy the provided incremental patch to `/tmp/detector-wide-2d.patch`.

```bash
cd /data/2x2-raw-event-display
# These guards must pass. Preserve any local patches; do not reset/stash blindly.
test -z "$(git status --porcelain)" || { echo "Working tree is not clean"; return 1 2>/dev/null || exit 1; }
test "$(git rev-parse HEAD)" = 6508a73d77f960a327874102be5091b585fb975f || { echo "Review baseline before applying"; return 1 2>/dev/null || exit 1; }
git switch -c feature/detector-wide-2d &&
git apply --check /tmp/detector-wide-2d.patch &&
git apply /tmp/detector-wide-2d.patch &&
git diff --check &&
.venv/bin/python -m pytest -q
```

Do not proceed after a failing guard/check/test. The full checkout may include
additional baseline tests that were not available in the patch-building runtime;
those must pass here. A test count different from the development report is not
by itself an error. The two optional browser tests normally skip on the DAQ host.

Review the diff and commit the candidate on that branch before restarting the
single instance. Do **not** create a v1 tag:

```bash
git diff --stat
git add raw_display tests tools docs README.md
git commit -m "Add guarded detector-wide Beam/Light 2D trigger candidates"
RAW_DISPLAY_TRIGGER_VIEW=1 \
/data/2x2-raw-event-display/.venv/bin/python -m raw_display serve \
    --time-basis asic \
    --geometry-dir /data/2x2-raw-event-display/layout \
    --host 127.0.0.1 --port 8765
```

Use the normal service manager instead when that is how the existing instance
runs. Refresh the ops01 browser after updating assets. No special IOG6 audit or
buffer setting is needed. Rollback is stopping the candidate, returning to the
preserved baseline branch/commit, and restarting the same one service.

## Live verification and v1 gate

After initial warmup, use a second terminal on the server host (HTTP client only):

```bash
cd /data/2x2-raw-event-display
curl -fsS http://127.0.0.1:8765/api/geometry | \
    jq '{feature_build, trigger_view_enabled, trigger_source_protocol, trigger_sources}'
.venv/bin/python tools/verify_detector_triggers.py \
    --seconds 120 --output /tmp/detector-wide-live-check.json
```

The helper preserves samples, checks one collector session, fresh qualified IOG
sets, pinned offsets, health and diagnostic increments, and reports interval
IOG6 lateness and per-target Beam/Light routing. It never approves a release.
Exit 0 means its software checks passed; no beam/light arrivals during the
interval remain inconclusive, even with exit 0. Output files are not overwritten.

Before tagging **v1 / 2D event display working**, record all of the following:

1. The complete deployed checkout tests pass. The inspected running interpreter,
   package directory, build ID and served assets are the candidate just tested.
2. Existing recorded data or approved timing diagnostics independently establish
   common PPS cycle identity and relevant phase. Consistent envelope labels or
   NTP alone do not establish this. Do not change hardware or inject production
   triggers simply to test the display.
3. Both real source paths are observed: receipt only on IOG5/6 as wired, and
   routed counts on every expected qualified IOG. Inspect windows on planes with
   actual corresponding activity; not every trigger must light every plane.
4. Beam/Light/Both, highlight/only/all, pause/resume and reconnect behave correctly
   in ops01/VNC. No browser errors, stale assets, false receipt pulses or extra
   collector subscriptions appear. Review post-repair IOG6 recent lateness rather
   than historical cumulative totals; no arbitrary offsets are applied.
5. Representative running shows stable independent acquisition/PacMon behavior,
   acceptable collector CPU/memory, no new invalid timing, alignment flapping,
   display drops or unexpected rate regressions. Archive the evidence and obtain
   Bruno's operational acceptance. A brief software check is not a substitute for
   a representative soak.

Only then choose the exact accepted commit, update version/release notes, and
create the v1 tag. No tag is created by this patch or either tool.

## Development verification

See [the development verification record](detector_wide_verification.md) and the machine-readable benchmark logs for
what actually ran. The benchmark command is entirely synthetic and safe off-DAQ:

```bash
python tools/benchmark_detector_triggers.py --seconds 4 --repeats 3
python tools/benchmark_detector_triggers.py --seconds 4 --repeats 3 --drain-messages 32
```

Optional browser tests require Playwright and Chromium on a development machine,
not new runtime dependencies on daq03. `RAW_DISPLAY_OFFLINE_BROWSER_TEST=1`
selects the in-memory transport/real-Canvas fixture.
`RAW_DISPLAY_BROWSER_TEST=1` selects the real loopback HTTP/WebSocket browser
integration. The latter was blocked by the development browser's administrator
policy and is not claimed as passed. Actual localhost collector/API/protocol
integration passed independently without a graphical browser.
