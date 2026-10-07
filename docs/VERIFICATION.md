# Clock addition verification

Incremental base: the previously delivered and user-installed detector-wide
candidate, trigger build `detector-wide-2d-1`, itself based on GitHub `6508a73`.
New `/api/geometry` marker: `playback_clock: pps-playhead-1`.
Package version is intentionally unchanged pending accepted v1.0.0 preparation.
No remote GitHub mutation, push, merge, deployment, version bump, or tag occurred.

## Executed

- 30 unique Node clock regression cases passed.
- Four focused pytest tests passed, including the Node wrapper, two actual
  endpoint-body unit tests using synthetic state, and one offline Chromium
  test of the actual clock and Canvas wiring with in-memory fake transport.
- The focused run passed three consecutive times after fixing a test predicate
  to tolerate the intended null clock model during reconnect. That predicate
  race was in the test, not a change to production behavior.
- A fresh baseline-subset application of the delivered patch passed the same
  four tests. Patched source bytes matched the tested workspace.
- Changed Python files compiled and parsed with Python 3.9 grammar. This was
  not a Python 3.9 runtime/dependency test. Changed JavaScript syntax checks
  passed under Node 22.16.0.
- The three modified source-file baselines matched the exact SHA256 values in
  the prior candidate manifest. They had been reconstructed from GitHub source
  and checked against Git blob hashes before applying the prior candidate.
- README and PROJECT_CONTEXT changes are intro hunks against fetched canonical
  context. Full trailing files were not fetched here. Patch application with
  extra trailing-content sentinels preserved those bytes exactly; no fabricated
  whole-document index hashes are included for these prefix edits.

## Scope and limits

This workspace is a materialized source subset, NOT a complete remote checkout.
The full repository test suite was not rerun here. Bruno's 222 passed / 5 skipped
result was on daq03 before the clock addition. Rerun the complete checkout suite
there after this patch, recording optional Node/browser skips.

The browser fixture ran real display.js, playback_clock.js, clock CSS and the
actual updated index markup, with minimal layout CSS and synthetic geometry/
transport. The unchanged trigger renderer was not loaded in this clock-specific
test. No real HTTP server, PACMAN, DAQ/PacMon service, or ops01 VNC was contacted.
The endpoint-body tests are unit tests, not an HTTP/collector integration claim.

The patch changes no collector, unroller, trigger router, geometry or per-hit
selection. Existing binary layouts/ACK behavior are unchanged. Approximate lag
uses server wall time, not browser wall time; it inherits header/PPS ambiguity
and does not certify NTP lock. Only operator acceptance can authorize v1.

The last supplied live check remains unresolved: three invalid Light trigger
timestamps, no observed Beam triggers in that interval, and pending independent
cycle/phase validation and operational stability acceptance. These are preserved
in the README, context and the new pending acceptance record.
