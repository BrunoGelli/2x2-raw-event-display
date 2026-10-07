# Detector-wide 2D trigger candidate — verification before v1

**Status: unreleased. No v1 tag or deployment approval.**

Based on remote `main` at `6508a73d77f960a327874102be5091b585fb975f`
(runtime v0.4.2, `28a12e9`). Feature identifier: **`detector-wide-2d-1`**.
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

## PPS labels: an important limit on the original proposal

The legacy envelope contains integer Unix seconds associated with **message
assembly**. This field is not an independently measured timestamp of the S83
edge. Even two consistent successive labels cannot exclude a stable one-second
packaging/NTP bias on a board. Millisecond-scale OS clock errors near a second
boundary can change the integer label. Therefore this implementation calls the
result **provisional PPS epoch qualification**, not verified hardware alignment.
The live release gate requires independent validation of that assumption.

For valid S83 words, with one reset period equal to one second:

```python
R = 10_000_000  # at the configured 100 ns tick
source_epoch_offset = header_unix_second * R - local_sync_tick
trigger_in_target_ticks = trigger_in_source_ticks + source_offset - target_offset
```

These operations use integers. Unix-scale ticks never pass through floating
point or browser arithmetic. The browser still gets IOG-local detector seconds.
This labels whole cycles; it does not measure/correct sub-second phase or clock
frequency. Independent presentation playheads remain unchanged, so correlated
hits are not guaranteed to flash at the same wall-clock instant on every plane.

Qualification rules are identical for every IOG:

* Two distinct, increasing, consistent S83 observations are required. Each S83
  must have a valid canonical timestamp and an unambiguous nonzero header label.
* The original qualified offset is pinned for the collector session. An
  inconsistent label revokes eligibility; it never silently retimes charge or
  adopts a replacement offset. Two observations of the original offset restore
  eligibility. A persistent new offset requires investigation and a collector
  restart, not an automatic correction.
* No fresh valid PPS for 2.5 seconds revokes eligibility. Detector progress
  beyond 2.5 reset periods without valid PPS also revokes it. Invalid selected
  SYNCs and multiple selected PPS words in one envelope are rejected. Heartbeats
  do not refresh qualification. Within-batch wire order is respected: a later
  S83 cannot qualify an earlier T word.

Revoking a previously qualified mapping clears pending/tagged associations and
matching histories **globally**, once per transition, as a conservative measure.
Normal charge playback is not cleared or retimed. Cumulative reset/discard
counters remain visible. A constantly flapping board can consequently degrade
trigger-view coverage; it is not hidden as successful alignment.

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
