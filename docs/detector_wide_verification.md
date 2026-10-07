# Detector-wide 2D candidate: development verification record

## Identity and disposition

Official repository: `BrunoGelli/2x2-raw-event-display`.
Base `main`: `6508a73d77f960a327874102be5091b585fb975f` (documentation commit over
runtime `28a12e935eccda591373191373a90de5fd5ab818`).
Candidate feature build: **`detector-wide-2d-1`**. **Not a v1 release.**

Implementation and tests were performed locally. GitHub exposed read operations
in this session; nothing was pushed, merged, deployed or tagged. No real PACMAN,
production recorder, PacMon service or ops01 browser was contacted.

## Executed checks

The locally available relevant Python suites completed with **114 passed,
1 skipped**, in 9.19 seconds, on Python 3.13.5 / NumPy 2.3.5, Linux x86_64.
The skipped test is the opt-in real browser-to-loopback-server test. The offline
Chromium Canvas fixture was explicitly enabled and passed. Both Node regression
scripts passed; JavaScript syntax checks passed on four files (Node v22.16.0).
Compilation succeeded and 21 Python files parsed under the Python 3.9 grammar.
**Python 3.9 runtime/dependency execution was not performed.**

Coverage included:

* Wire-level epochs starting in different PPS cycles; integer arithmetic above
  2^53 Unix ticks; Beam from IOG5 and Light from IOG6 on all eight target IOGs;
  subtype-independent routing; unexpected sources; exact half-open endpoints;
  reset-crossing, out-of-order and late hits; preservation of old hit age;
  independent Beam/Light ages on the same pixel; ten randomized oracle cases.
* Header-to-word boundaries, empty/non-D/malformed envelopes, SYNC-only and
  trigger-only batches, PPS qualification order, missing/ambiguous/invalid PPS,
  stale epochs, revocation/recovery without retiming, subtype filters, bounded
  histories/window/pending queues and trigger-storm routing caps.
* An actual eight-publisher localhost XPUB/SUB test through the collector
  subprocess: distinct local epochs, both source paths, eight-plane selection,
  one SUB handshake per publisher and no additional subscriptions.
* Real HTTP/API and WebSocket tests using the application test client: coherent
  source layers, RDB1/RDL1 plus one RDP2 ACK, legacy RDT1 union, binary-only RDP2,
  bad acknowledgements, cross-origin rejection, clear records, and nonblocking
  observer diagnostics.
* Actual frontend JavaScript in Node and actual Canvas in offline Chromium:
  source selection, highlight/only/all, pause/resume, separate source ages,
  future-hit suppression, receipt provenance, qualification freshness and no
  transport reconnection when changing controls. The offline fixture made no
  network requests; geometry and transport were synthetic, not on-detector data.
* Nine checks on the new read-only interval verifier, including rejection of
  stale/missing/restarted/retimed/unhealthy diagnostics and HTTP redirects.

The old `test_trigger_audit.py` suite was included. Two expectations were updated
intentionally: a T word on IOG1 no longer tags same-IOG hits because IOG1 is not a
configured Beam/Light source; its raw counts and generic late audit still work.
The unchanged old frontend test also passed. Other baseline test files and
parts of the complete remote checkout were not materialized in this environment.
**This is not a claim that the full repository test suite ran.** Run it in the
complete canonical checkout before deployment acceptance.

## Browser integration limit

An explicit attempt to load the real local HTTP service in Chromium failed at
navigation with `net::ERR_BLOCKED_BY_ADMINISTRATOR`. This was a browser
administrator policy restriction; it was not bypassed or relabelled as a pass.
Actual local collector/API/WebSocket integration passed separately. A separate
in-memory transport fixture verified the actual Canvas code. Neither replaces
a live ops01/VNC browser acceptance test. The screenshot is labelled FIXTURE and
uses synthetic 4,096-pixel geometry, not the installed detector layout.

## Synthetic performance

Each run processed 2,490,368 hits representing four seconds at 622,592 hits/s,
eight IOGs, eight-word messages, artificial 20 Hz Light and 2 Hz Beam. Three
matching-disabled/enabled pairs were run at each drain size. Normal playback
outputs were bit-identical in each pair. No playback-buffer or history-capacity
drops occurred. Enabled runs retained 1,261,552 history hits across all IOGs at
completion, below the configured per-IOG caps.

| Messages per decode batch | CPU seconds, matching off | CPU seconds, both sources on | Added percentage points of one core at the simulated rate |
|---|---:|---:|---:|
| 256 | 0.655–0.761 | 0.792–0.898 | 3.4–3.5 |
| 32 | 2.193–2.345 | 2.616–3.059 | 10.6–17.9 |

These are CPU-only, synthetic results. They include decoding, normal detector
playback and epoch/router/history work; they exclude fixture construction,
geometry lookup, live network reception, IPC snapshots and browser rendering.
The disabled comparison already uses the candidate decoder and PPS observer,
not an isolated benchmark of the old release. They are not a DAQ throughput or
losslessness guarantee. The much larger cost with partial drains is important:
**independent acquisition/PacMon health and actual drain/load conditions remain
a release gate.** Do not deliberately stall production subscribers to test this.

Chunk-bound pruning was added after an initial benchmark showed avoidable full
history scans for each trigger. Regression tests were rerun after that change.
Raw benchmark JSON and the reproducible script are supplied with the patch.

## Timing and IOG6 scope

No IOG6-only time offset was found in the reviewed canonical runtime; none was
added, and no nonexistent correction is claimed as removed. `timing.py`,
`post_sync.py`, generic late-audit computation, unroller arithmetic, normal
1.25-second playback reserve, raw `<10` display veto, endpoint parsing,
geometry and subscriber topology retain their baseline implementation.

Header assembly time is only a provisional whole-cycle label. Consistency
cannot rule out a stable one-second packaging/NTP bias or validate hardware
phase/frequency. The implementation exposes this limitation and revokes
uncertain associations rather than modifying charge timestamps. Common-cycle
identity and relevant relative phase must be checked independently before v1.

## Patch verification and remaining gate

Baseline bytes of 18 materialized source/test files were checked against the
GitHub blob SHAs. Changed/new source files and complete new documents are
provided as one incremental unified patch, not an alternative deployment.
README and PROJECT_CONTEXT edits are small intro notices; their patch context
was checked against the fetched intros, preserving trailing content.
The patch is intended exclusively for the official base SHA above. Always run
`git apply --check` on the real clean checkout. The verification build uses the
available file subset, not a fabricated copy of the full remote tree.

The candidate has no release/tag command. Remaining requirements are the full
checkout test suite and deployed Python environment, live PPS/phase checks,
real Beam and Light routing, ops01/VNC controls, a representative stable run,
post-repair IOG6 interval metrics, independent DAQ/PacMon health, and Bruno's
operational acceptance. See `detector_wide_triggers.md` for the exact runbook.
