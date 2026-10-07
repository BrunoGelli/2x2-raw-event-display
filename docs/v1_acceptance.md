# v1.0.0 — 2D display acceptance record

Release decision: PENDING

Candidate commit: TO BE RECORDED
Operator and acceptance time: TO BE RECORDED

This is a checklist, not a claim that pending checks passed. Change the release
decision only after completing the record, including explicit disposition of
any known limitations. Keep local hardware/configuration secrets out of Git.

| Check | Evidence / disposition |
|---|---|
| Full deployed tests before clock | Bruno reported 222 passed, 5 skipped, 9.75 s, 2026-10-06. |
| Full deployed tests after clock | PENDING; record Python environment, counts and skipped reasons. |
| Real Light routing/controls | Bruno reported 2,587 routed triggers to every IOG and visible yellow muon candidates; record representative post-clock check and control acceptance. |
| Real Beam routing/controls | PENDING; no IOG5 triggers in the supplied 120-second interval. No-trigger intervals are inconclusive. |
| Three invalid Light timestamps | PENDING; recover reason-level evidence and classify/fix. Do not silently waive the verifier flag. |
| Common PPS cycle identity / relative phase | PENDING; independent check, not merely stable header labels or selected-window occupancy. |
| Absolute displayed-time sanity | PENDING; compare clock to independent timestamped observation/log and review approximate lag. |
| Clock Pause, UTC/Chicago, stale/connection behavior | PENDING; ops01/VNC acceptance. Use disconnect only on the viewer's approved forwarding path; do not disturb acquisition or PACMANs. |
| IOG6 post-repair timing | Supplied 119.988 s interval: 162,148 mapped hits, zero pre-cut late hits. Record representative later stability. |
| DAQ/PacMon health under representative load | PENDING; independent CPU/memory/rates/recorder/PacMon checks. |
| Single collector, loopback-only service | PENDING; verify existing deployment/launcher. |
| Final source review / version | PENDING; no IOG6-specific correction; versions in pyproject and package both 1.0.0 at release. |

The pasted verifier summary remained `software_checks_passed=false` and
`v1_release_approved=false`. No raw diagnostic file or rejection samples were
attached. The receipt/routing count difference is not itself measured loss,
because the counters come from different publication snapshots; sampling skew
has not been proved to explain the entire difference.

A through-going track is a useful qualitative check, but seeing selected hits
inside the programmed 300 us window is not independent proof of phase: the
selection imposes that condition. Use an independent physical/common timing
reference or unselected trigger-relative distributions for calibration evidence.

Final findings and any explicitly accepted limitations:

TO BE RECORDED
