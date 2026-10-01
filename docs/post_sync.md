# Post-SYNC display cut and subtype-83 receive indicator (v0.4.1)

## Exact selection

The requested policy comes from the Stephen Greenberg discussion supplied by
Bruno: reject **raw ASIC timestamps < 10 ticks** during self-triggered display.
This implements that explicit selection, not a measured noise-identification
algorithm or a change to detector acquisition.

The default applies to mapped, valid-parity Packet_v2 charge hits from every
selected IO group in both ASIC-time and explicit host-time modes:

- Raw timestamps 0 through 9 are excluded from painting/buffering.
- Timestamp 10 is retained.
- The comparison is against the raw ASIC field, **not** receipt time, wall time,
  unrolled time or timestamp modulo the rollover interval. Thus raw R+5 is not
  rejected merely because its remainder would be 5.
- SYNC, trigger and heartbeat words are not charge hits and are not removed.
- At the configured 100 ns tick, ten ticks span 1 microsecond; the selection
  itself is in ticks and does not assume a host-clock duration.

Disable the cut for comparison by restarting the existing instance with:

```bash
raw-display serve --geometry-dir layout --host 127.0.0.1 --port 8765 \
  --min-raw-timestamp 0
```

The normal command defaults to `--min-raw-timestamp 10`. Do not run a second
collector concurrently just to compare cuts. No new dependency is needed.

## Timing and counters are not hidden by the display selection

The decoder, parity policy, downstream-marker acceptance and raw word order
are unchanged. The unroller consumes all accepted hits and SYNC words **before**
the display selection. A filtered-only batch can still advance its receipt-time
frontier and SYNC state. Original raw arrays are not rewritten.

Root per-source `/api/status` counters:

- `mapped_hits`: all mapped accepted charge hits, before the cut (unchanged).
- `post_sync_filtered_hits`: mapped hits with raw timestamp below the cut.
- `display_selected_hits`: mapped hits passing the raw-timestamp selection;
  not a count of rendered points, and may include ASIC timing warmup.

Nested `sources[].timing` counters in ASIC mode:

- `timing_eligible_hits`: mapped hits accepted by timing validation before cut.
- `pre_cut_late_hits`: timing-eligible hits already behind the playhead at ingest,
  including hits subsequently excluded by the cut. Use this to continue the IOG 6
  investigation without making the symptom disappear by changing a display cut.
- `late_hits`: late hits retained after the cut, with their original detector age.
- `post_sync_filtered_hits` and `display_selected_hits`: corresponding counts
  restricted to timing-eligible hits; unlike root counters, these exclude warmup
  and invalid timing.

All these counters are cumulative since collector startup. For a recent fraction,
compare differences between two status snapshots from the same collector run.
Existing `invalid_times`, `invalid_syncs`, `boundary_corrected` and buffer-drop
counters retain their meanings. Selected does not imply delivered or rendered.

The optional tile-rate audit is deliberately **unfiltered** and remains on the
host-consumption clock. Its population and the top-level mapped-rate display are
not silently changed just because the phosphor cut is enabled.

## SYNC 83 RX badge

Each IO-group plane has a small receive badge. Only a PACMAN `S` word whose subtype
is 83 advances its count and refreshes its age. Subtype 72 does neither, even when
heartbeats are plentiful. A trigger word with numeric subtype 83 is not a SYNC.

The badge briefly brightens on a newly observed counter increment. There is no
synthetic 1 Hz pulse generator. `WAIT` means no subtype-83 arrival has been seen;
`STALE` means its collector age exceeds 2.5 s; a closed/stalled WebSocket shows
unknown status. Initial historical counts and reconnects do not invent a pulse.
Hover for the cumulative received count and age.

**RX is an arrival indicator, not a hardware phase-lock or NTP-lock indication.**
It refers to collector consumption of the drained batch, sampled by the existing
approximately 10 Hz publication. It is not a microsecond timing instrument. An
invalid-timestamp subtype-83 word still counts as received; `invalid_syncs` is the
separate timing-validation diagnostic. The charge view is buffered by about 1.25
seconds, so this arrival badge is deliberately not phase-aligned to its playback.
It continues reporting reception while the local phosphor view is paused.

The original RDP1/RDP2 binary layouts and acknowledgements are unchanged. New
browser clients opt into small `sync83` JSON messages on their existing WebSocket;
old binary-only clients continue to work. No extra PACMAN subscriber, HTTP polling
loop or unbounded event queue is introduced.

## NTP and the IOG 6 investigation

The present ASIC unroller uses ASIC timestamps, PACMAN receipt timestamps, ordered
SYNC words and per-IOG state. It does not use the PACMAN message header's Unix time.
The host monotonic clock paces presentation but is not assigned as a hit time.
A PACMAN OS wall-clock/NTP offset therefore does not directly enter this late-hit
calculation. This does not prove that hardware PPS/reset distribution, clock
configuration or packet delivery is correct. Check those independently rather
than treating successful NTP synchronization as proof of hardware timestamp lock.

This update does not claim to resolve IOG 6's large late fraction. The pre-cut
counter is preserved specifically so that investigation can continue.

## Validation and scope

Executed locally on Python 3.13 / NumPy 2.3.5:

- 28 added pytest cases passed, including exact cut boundaries, disable mode,
  raw-versus-modulo semantics, invalid parameters, unroller state preservation,
  pre-cut lateness and a PACMAN-header Unix-time invariance check.
- Actual localhost XPUB/SUB collector tests in both host and ASIC modes verified
  the cut, subtype-83 count/age, heartbeat isolation and unchanged subscription
  count. HTTP/WebSocket tests cover opt-in JSON and binary-only clients.
- Node ran the actual frontend clock/indicator logic for per-source activity,
  stale/reconnect behavior, repeated metadata and retained late-hit ages.
- Chromium rendered an offline fixture with an active and a stale source, with
  no JavaScript page errors or network requests. This was not an ops01/VNC or
  graphical browser-to-live-server test.

No real PACMAN or production service was contacted. No claim about real-file
validation, production throughput, NTP health or the physical noise coupling is
made. Live overhead is a few vectorized comparisons/counters per batch and small
per-client metadata; it is not asserted to be zero.

PACMAN endpoints, socket type/port, HWM, drain limit, priorities, playback delay,
geometry and clock-unrolling equations are unchanged. The web CLI remains
restricted to 127.0.0.1. This selection cannot reduce detector FIFO occupancy or
raw acquisition load: it only omits the selected noise candidates from the view.
