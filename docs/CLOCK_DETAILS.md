# Displayed detector-time clock — `pps-playhead-1`

This is an incremental presentation addition on top of `detector-wide-2d-1`.
It does not change acquisition, decoding, clock unrolling, Beam/Light matching,
noise cuts, geometry, pending buffers, hit timestamps or binary protocols.
The package remains `0.4.2` until an accepted release explicitly changes it.

## What the clock means

The date/time is the oldest **qualified, currently displayed per-IOG playhead**.
All configured planes count, including planes hidden by module focus. Hover
shows each labelled IOG's cursor. The adjacent line reports coverage and the
oldest-to-newest spread; unqualified/missing panes are explicitly excluded/named.
A 2D phosphor frame can contain many old fading hits and several triggers.
This clock is not a unique event timestamp, latest incoming hit, input frontier,
trigger t0, or the time of every visible pixel.

The default timezone is `America/Chicago`, with date, milliseconds and CDT/CST;
UTC is selectable. Timezone selection is browser-local. It does not reconnect,
reconfigure, or change any detector time. The clock appears above the controls.

## Conversion and examples

Given a fresh qualified PPS anchor for IOG i:

```text
R = 10,000,000 ticks at 100 ns = one second
E_i = last_header_second_i - last_local_sync_tick_i / R
shown_unix_ms_i = 1000 * E_i + actual_local_playhead_ms_i
```

`last_local_sync_tick_i` is an integral reset-period multiple. The frontend checks
safe integer values and the one-second period. It does NOT use JSON
`offset_ticks`, since Unix-scale 100 ns integers can exceed JavaScript's exact
integer range. Matching remains in the pre-existing Python integer tick path;
this conversion only formats the display to milliseconds.

Example: a PPS labelled U is local second 42 in IOG1 and local second 39 in IOG6.
Their respective playheads 40.75 and 37.75 both label **U - 1.25 seconds**. If
IOG6 instead displays 37.80, the header shows U-1.25 (oldest) and a 0.050 s pane
spread. A newly received packet with a much later timestamp does not jump the
clock forward: the date follows the actual Canvas playback cursor.

The browser uses the same `displayTime()` function and the same frame's render
`now` value as Canvas, including its existing <=250 ms interpolation/frontier
cap. Pause takes the same frozen per-IOG playheads as the image. Resuming uses
the current playheads; it does not rewind or catch up the collector.

## Lag and trust limits

The existing `/api/status` response now also supplies `server_unix_s` (daq03
wall-clock time) and `trigger_alignment.session_id`. The clock compares each
labelled playhead to the recent server time, extrapolated only with browser
monotonic elapsed time. Browser `Date.now()` is not used for data time or lag.
Return-network latency is not subtracted, so the lag is approximate, not a
precise transport-latency measurement. A long request is rejected for freshness.
Negative lag remains visible as a clock-comparison warning instead of being
clamped to zero. Check server/PACMAN clocks and epoch assumptions in that case.

PPS labels originate from PACMAN message-assembly Unix seconds, NOT an
electrically latched PPS epoch. The clock inherits the common-timing model's
stable whole-second bias limitation. It does not query NTP, certify any host's
NTP lock, or validate absolute/subsecond hardware phase. Millisecond formatting
is display resolution, not a claim of absolute accuracy. Cross-check against
an independent time reference during operational acceptance.

## Failure and pause behavior

Live time requires a fresh display frame (<=1 s), recent status (<=3.5 s), a
healthy collector, supported one-second PPS, and a fresh aligned anchor for
each included source. Anchor freshness includes its reported age, diagnostic
publication age, browser elapsed time and a conservative full status-request
round trip. This can temporarily mark a pane unavailable during a delayed poll;
it never substitutes host arrival time for a missing detector label.

No-frame, disconnected, unhealthy or stale-status states show time unavailable.
Buffering panes retain their fixed time and are named. Partial coverage is
visible. Host-time comparison mode and the demo do not fabricate absolute
acquisition timestamps. Paused time and **lag at pause** remain fixed, even
through a later reconnect; the UI explicitly says PAUSED. A frozen timestamp
remains a historical label, not evidence of current alignment health. A pause
before qualification stays unlabelled, not retrospectively assigned an epoch.

Responses initiated before a reconnect are ignored by the clock. A changed
collector session requires another real frame. There is no added HTTP poll,
WebSocket, PACMAN subscriber, per-hit computation, or production dependency.
The clock labels refresh at most 10 Hz within the existing render loop. Status
and frames have independent update cadences; this is an operator display,
not a transactional timing-calibration record.

## Executed local tests for this addition

Executed in the development container: **30 Node test cases passed**. Coverage:
independent PPS epochs; safe conversion without Unix tick rounding; subsecond
position; rollover; pane spread; changing consistent anchors; invalid,
suspect, duplicate and stale metadata; fresh-frame/status/collector checks;
pause/reconnect/session changes; host/demo/non-second mode; negative/missing
lag; no browser-wall-clock dependency; explicit timezone/midnight/DST labels;
and no mutation of supplied playheads.

The focused Python run completed with **4 passed**, with the optional offline
Chromium fixture explicitly enabled. Those four tests comprise the Node suite
wrapper, two endpoint-body unit tests using synthetic state, and one actual
Canvas/clock browser wiring test with mocked in-memory transport. The browser
test exercised eight planes, timezone selection, pause while metadata changes,
resume, stale frames and reconnect; it observed no page errors or network
requests, and no added connection/poll endpoints. It uses minimal fixture layout
CSS plus production clock CSS, not a production-screen acceptance test. The
trigger renderer is unchanged and not loaded by this clock-specific fixture.

Python compilation and Python 3.9 grammar checks passed for the changed server
and three new Python test files. JavaScript syntax checks passed. This was not
a Python 3.9 runtime test, a full-checkout pytest run, a real HTTP/collector test,
or a visit to daq03/ops01. The prior **222 passed, 5 skipped** result is Bruno's
reported full-checkout run before this addition. Rerun that full suite after
applying the clock patch.

Reproduce focused tests on a development machine:

```bash
node --test tests/test_playback_clock.js
RAW_DISPLAY_CLOCK_BROWSER_TEST=1 python -m pytest -q \
    tests/test_playback_clock.py tests/test_playback_clock_metadata.py \
    tests/test_playback_clock_browser.py
```

Node >=18 / Playwright / Chromium remain optional development tools; do not install them
on production simply to display this clock. On daq03 run the normal full pytest
suite, record skips, restart only the existing display, refresh ops01, and
inspect `/api/geometry`: `playback_clock` must be `pps-playhead-1`.

## Remaining release gate

The last supplied live verifier was false: three invalid trigger timestamps
were reported, and the interval did not include Beam triggers. This addition
does not fix, relabel or waive those results. Record the findings and operator
acceptance in [v1 acceptance](v1_acceptance.md) before following the
[push/tag runbook](v1_release.md). The clock is not an HV interlock or a substitute
for operational safety/DAQ monitoring.
