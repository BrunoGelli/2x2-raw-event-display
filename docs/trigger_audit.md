# Trigger-window view and timing audit (feature build `trigger-audit-1`)

Incremental extension for the canonical remote v0.4.1 commit
`ebd7c9146546830d4079e7377415586473cdc639`. It is **not** a replacement
implementation of v0.4.1 and it is not a 3D event builder.

## What changes and what does not

The existing ASIC-time collector receives the same PACMAN SUB streams on port
5556, with the same receive HWM, drain limit, scheduling priority and clock
unrolling. No additional subscriptions, detector commands, register writes,
configuration changes or resets are introduced. The original raw mapped counts,
post-SYNC cut and detector-time playback remain in use. HTTP serving is still
restricted to `127.0.0.1`.

The extension adds optional downstream work. It is not zero-cost, and unchanged
socket settings do not certify absence of acquisition impact. Enable the timing
audit first; enable buffered trigger matching separately after checking the
normal DAQ/PacMon load.

## Trigger reception badges

The default ASIC-time website now shows `TRG RX` beside `SYNC 83 RX` for each
IO group. Only a PACMAN **T word** refreshes the trigger marker. A SYNC S word,
including subtypes 83 and 72, never does. A T word's subtype is not automatically
identified as beam or light: inspect the observed subtype counts first.

Badges are driven by received counters and approximate collector-consumption
ages, not an independent one-second animation. Initial history does not invent
an arrival pulse. A disconnected/stale metadata stream becomes unknown. Lack of
a recent trigger is normal for a source without that trigger, not a hardware
fault. These badges precede buffered charge playback and are not event-time
alignment or NTP/PPS-lock indicators.

The normal host-time comparison mode is unchanged. The new matching and audit
features require ASIC mode. A host-mode page does not pretend to supply a
calibrated trigger association.

## Optional trigger-window layer

On the **one existing serving instance**, enable:

```bash
RAW_DISPLAY_TRIGGER_VIEW=1 raw-display serve \
  --geometry-dir /data/2x2-raw-event-display/layout \
  --host 127.0.0.1 --port 8765
```

Restart the old instance first; do not run two collectors concurrently. Reload
the browser after updating the assets. The website offers:

- **All hits**: existing phosphor view.
- **Highlight windows**: time-window candidates get a yellow layer.
- **Window hits only**: only that yellow layer is shown.

Changing these controls is browser-local and does not reconnect PACMANs. The
yellow layer keeps independent last-hit timestamps, so a later unrelated hit
on the same pixel cannot silently erase a previously matched hit from the
window-only view. Future hits wait for the canonical detector playhead; late
hits keep their old age. The playhead and rollover algorithm are not modified.

### Deliberately same-IOG matching only

A trigger is compared **only with charge in the IO group carrying that trigger**.
The default half-open window is `[t0, t0 + 300 microseconds)`. Thus, with triggers
present in only two PACMANs, trigger-only mode will not populate the other six
IO groups. The UI states this limitation explicitly. A blank unmatched plane
is not a detector-efficiency measurement.

The first observed PPS epoch can differ between source connections. This
extension does not infer a common epoch from host arrival times, badge flashes,
or similar-looking phases. Detector-wide association and calibrated 3D require
that separate timing step. These are **temporal candidates**, not causal proof
that a charge hit belongs to the triggering interaction, and not complete saved
events.

Trigger word timestamps are read from bytes 4..7 of the legacy PACMAN T word.
The data-word receipt field at bytes 2..5 is not substituted. Projection uses
preceding valid SYNCs in original wire order and the source's existing unroller
epoch. Pre-PPS triggers and T counters outside one reset interval are counted
as invalid for matching instead of guessing a rollover. Trigger-only batches
are supported; triggers do not themselves advance the canonical charge frontier.

### Explicit settings and bounds

| Environment variable | Default | Meaning |
|---|---|---|
| `RAW_DISPLAY_TRIGGER_VIEW` | `0` | Set exactly `1` to enable hit history/matching. Badges do not require it. |
| `RAW_DISPLAY_TRIGGER_PRE_US` | `0` | Lower bound is `t0 - pre`; supports 0..100000 microseconds. |
| `RAW_DISPLAY_TRIGGER_POST_US` | `300` | Exclusive upper bound is `t0 + post`; supports >0..100000 microseconds. |
| `RAW_DISPLAY_TRIGGER_TYPES` | empty | All T-word subtypes; otherwise comma-separated integer byte values. No beam/light labels are guessed. |
| `RAW_DISPLAY_TRIGGER_HISTORY_S` | `2` | Detector-time retention for matching late triggers; 0.1..10 seconds. |
| `RAW_DISPLAY_TRIGGER_MAX_HITS` | `500000` | Retained-hit limit **per IOG**; 1..2000000. |

Durations are quantized to the configured detector tick. Retained hit chunks are
also limited to 4096 per IOG; merged windows are limited to 2048. The matched
presentation queue is bounded independently. Overflow discards display-only
work and increments counters; it never waits for a browser. Normal playback
limits remain unchanged.

Hits arriving before the trigger can be recovered from this bounded history.
A trigger older than retained history cannot recover discarded data, and a
large readout delay can still make association incomplete. Inspect
`/api/observers`: history-capacity drops, outside-history triggers, window-limit
drops, invalid trigger times and matched-buffer drops are all reported. Zero
such counters is not a losslessness certificate for upstream transport.

Metadata and a supplementary binary `RDT1` layer ride the same acknowledged
WebSocket. The usual RDP2 acknowledgement covers the preceding optional layer;
older clients that do not request it retain their old protocol. No unbounded
per-browser hit queue is added. Per-pixel state and clocks are copied coherently
under the existing snapshot lock.

## IOG 6 timing audit

Start with this inexpensive, opt-in diagnostic on the current single collector:

```bash
RAW_DISPLAY_LATE_AUDIT_IOGS=6 raw-display serve \
  --geometry-dir /data/2x2-raw-event-display/layout \
  --host 127.0.0.1 --port 8765
```

`RAW_DISPLAY_LATE_AUDIT_IOGS` accepts a comma-separated subset of configured IO
groups. It adds no PACMAN subscription. To combine it with the trigger view,
prefix the same command with both environment variables.

In another terminal, from this checkout:

```bash
python tools/inspect_timing_audit.py --iog 6 --seconds 15 \
  --json-out /tmp/iog6-timing-audit.json
```

The tool makes two loopback HTTP requests and uses only the Python standard
library. It starts no listener and opens no ZMQ connection. The optional output
is created exclusively; choose another filename if it already exists. A
collector restart, stale data, or disabled audit makes the command fail rather
than report misleading differences.

The report shows **interval**, not merely cumulative, late fractions per tile.
It also reports cumulative top late chips, raw-ASIC/receipt counters at or above
the configured rollover interval, crossing corrections, histograms and a bounded
recent late-packet sample. Counts describe mapped, timing-valid hits passing the
raw timestamp display cut; they do not have the same warmup denominator as raw
`mapped_hits`. Samples are not an unbiased packet distribution.

Useful interpretations, not diagnoses:

- Concentration on a few tile/chip addresses identifies where to look first.
- Raw ASIC counters >= one reset period, with continuing PACMAN S83 words,
  warrant checking ASIC reset delivery and packet/epoch interpretation. Receipt
  of an S83 word is not proof that every ASIC reset.
- Large inferred ASIC-to-receipt delays can reflect readout buffering **or** an
  epoch-model problem. They are not independently measured network latency.
- A lateness distribution near an integer reset period makes cycle association
  worth checking before changing the global playback reserve.

The audit does not repair or alter timestamps, remove crossing corrections, or
increase the playback delay. It reuses the original ingest results rather than
unrolling charge twice. It uses fixed counters and a small bounded sample buffer;
1-Hz JSON snapshots are size-limited and published with a **nonblocking writer**.
Diagnostic-reader contention causes a skipped diagnostic publication, not a wait
in the collector. No diagnostic files are written by the live collector.

## Validation scope

The distribution's `VALIDATION.md` records the tests actually run, including
36 feature regressions, a localhost-only publisher/collector integration test,
HTTP/WebSocket tests, Node logic tests and an offline graphical browser fixture.
The full upstream test suite still must be run in the complete installation
checkout. No production PACMAN, real packet file, daq03, ops01, or VNC validation
is claimed for this extension.
