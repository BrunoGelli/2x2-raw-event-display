# First stable 2D release: preserve local work, push, then tag

Target tag: **v1.0.0**, not an unversioned `v1` alias. This document does not
create a release. Do not run the stable-tag section while acceptance is pending.
The last supplied live verifier had three invalid trigger timestamps and no
Beam observation; the new clock does not resolve that evidence gap.

## 1. Checkpoint the already installed detector-wide candidate

Work only in `/data/2x2-raw-event-display`. Stop the **existing display instance**
through its usual launcher before editing/restarting its runtime; leave the DAQ
and PacMon alone. Do not start a second display collector. Review the paths below
before staging: do not commit local datasets, configuration secrets or logs.

```bash
cd /data/2x2-raw-event-display
git branch --show-current                 # expected: feature/detector-wide-2d
git status --short
git diff --stat
```

When the changes are the intended detector-wide candidate, stage and review them:

```bash
git add README.md raw_display docs tests tools
git diff --cached --check
git diff --cached --stat
git diff --cached                         # review, including newly added files
```

Commit only if staged changes exist (the candidate may already be committed):

```bash
if ! git diff --cached --quiet; then
    git commit -m "Add detector-wide Beam and Light 2D trigger candidates"
fi
```

Do not reset/clean a dirty tree to make this work. Resolve unexpected changes
before proceeding. A backup branch alone would not save uncommitted patches;
the reviewed checkpoint above does.

## 2. Apply the incremental clock patch

Place/extract the delivered clock bundle as `/tmp/2x2-playback-clock/`. This patch
is **on top of** the installed detector-wide candidate, not bare GitHub `main`.
It preserves trailing README/context history and changes no collector code.

```bash
cd /data/2x2-raw-event-display &&
git diff --quiet && git diff --cached --quiet &&
git apply --check /tmp/2x2-playback-clock/playback-clock.patch &&
git apply /tmp/2x2-playback-clock/playback-clock.patch &&
git diff --check &&
.venv/bin/python -m pytest -q
```

Stop on a failed guard/test. Do not use `--reject`, force application, or a hard
reset. New optional browser/Node tests may skip on daq03; record why. The clock
itself requires neither Node nor a new Python dependency.

Review/commit this addition separately:

```bash
git add README.md raw_display docs tests
git diff --cached --check && git diff --cached --stat
git diff --cached
git commit -m "Add displayed PPS-time clock and document 2D release verification"
```

Restart the one display through its normal launcher, preserving its options.
Equivalent foreground example, **only with the previous display stopped**:

```bash
RAW_DISPLAY_TRIGGER_VIEW=1 RAW_DISPLAY_TRIGGER_TYPES="" \
.venv/bin/python -m raw_display serve \
    --time-basis asic \
    --geometry-dir /data/2x2-raw-event-display/layout \
    --host 127.0.0.1 --port 8765
```

Refresh the ops01 browser. From another terminal on daq03:

```bash
curl -fsS http://127.0.0.1:8765/api/geometry | \
.venv/bin/python -c 'import json,sys; d=json.load(sys.stdin); print(d.get("feature_build"), d.get("playback_clock")); assert d.get("playback_clock")=="pps-playhead-1"'

.venv/bin/python tools/verify_detector_triggers.py \
    --seconds 120 --output /tmp/detector-wide-with-clock.json
```

Expected markers: `detector-wide-2d-1 pps-playhead-1`. `--version` still says
`0.4.2` until release preparation below. The clock should show labelled
playhead time, plausible lag and pane spread, freeze on Pause, and clearly
identify stale/unavailable time instead of continuing as a fake live clock.

## 3. Push the development branch now (no stable tag)

After reviewing the working tree and tests:

```bash
git status --short
git push -u origin feature/detector-wide-2d
```

This preserves the work on GitHub without claiming a stable release. It does
not deploy the DAQ service. No `--force`, `--all`, or `--tags` is needed.

## 4. Record operational acceptance and prepare version 1.0.0

Complete `docs/v1_acceptance.md`: rejection reasons/disposition, both trigger
paths, independent timing evidence, clock/browser checks, full deployed tests,
and independent DAQ/PacMon health. Only then set its exact decision line to
`Release decision: APPROVED`. A software pass alone cannot fill this record.

The following guard and version step should be run on the feature branch.
It validates all expected old strings before modifying any file:

```bash
cd /data/2x2-raw-event-display &&
test "$(git branch --show-current)" = feature/detector-wide-2d &&
grep -qx 'Release decision: APPROVED' docs/v1_acceptance.md &&
.venv/bin/python - <<'PY'
from pathlib import Path
changes = [
    (Path('pyproject.toml'), 'version = "0.4.2"', 'version = "1.0.0"'),
    (Path('raw_display/__init__.py'), '__version__ = "0.4.2"', '__version__ = "1.0.0"'),
    (Path('README.md'),
     '**Release status: unreleased 2D candidate; v1.0.0 requires operator acceptance.**',
     '**Release status: v1.0.0 — accepted 2D event display; see the acceptance record.**'),
]
pending = []
for path, old, new in changes:
    text = path.read_text()
    if text.count(old) != 1:
        raise SystemExit('Unexpected version/release state in %s; inspect before proceeding' % path)
    pending.append((path, text.replace(old, new)))
for path, text in pending:
    path.write_text(text)
PY
```

Refresh local package metadata without fetching/upgrading dependencies, run tests,
and confirm the CLI version. Stop if any command fails:

```bash
.venv/bin/python -m pip install --no-deps --no-build-isolation -e . &&
.venv/bin/python -m pytest -q &&
.venv/bin/python -m raw_display --version
```

Expected: `raw-display 1.0.0`. Review, then commit/push the accepted candidate:

```bash
git add pyproject.toml raw_display/__init__.py README.md docs/v1_acceptance.md
git diff --cached --check && git diff --cached
git commit -m "Prepare v1.0.0: accepted 2D raw event display"
git push origin feature/detector-wide-2d
```

If build dependencies are absent, stop and review the environment rather than
blindly upgrading production packages. No dependency changes are part of this
release. The acceptance record must honestly describe any explicitly accepted
limits; do not mark missing data as a measurement.

## 5. Fast-forward main and publish exactly this tag

Run as one fail-fast shell block after the version and acceptance steps:

```bash
(
set -euo pipefail
cd /data/2x2-raw-event-display
test -z "$(git status --porcelain)"
grep -qx 'Release decision: APPROVED' docs/v1_acceptance.md
.venv/bin/python -c 'from raw_display import __version__; assert __version__ == "1.0.0"'
git fetch origin --tags
git switch main
git merge --ff-only origin/main
git merge --ff-only feature/detector-wide-2d
.venv/bin/python -m pytest -q
git tag -a v1.0.0 -m "v1.0.0 — 2D raw event display: detector-wide Beam/Light windows and PPS-labelled display clock"
git push --atomic origin main refs/tags/v1.0.0
git show --no-patch v1.0.0
git ls-remote origin refs/heads/main refs/tags/v1.0.0 'refs/tags/v1.0.0^{}'
)
```

If main has diverged, an existing tag conflicts, or a branch rule rejects the
push, stop and reconcile/use the repository's review flow. Do not force-push,
move an existing release tag, or discard commits. `--atomic` means the branch
and tag update together or neither does; it fails if unsupported by the remote.
A failed push can leave the **local** tag in place, so inspect before retrying.
After success, the peeled `v1.0.0^{}` hash and remote main hash should match.

A pushed annotated tag is sufficient for a tagged source release. A separate
GitHub Releases page is optional: select existing `v1.0.0` in the repository's
Releases UI and copy the accepted scope, evidence and known limitations. Do not
create another tag or falsely describe pending checks as passed.

Reference: official Git documentation, https://git-scm.com/docs/git-push and
https://git-scm.com/docs/git-tag.

## 6. Preserve v1 and start 3D separately

```bash
git switch -c feature/3d-events v1.0.0
git push -u origin feature/3d-events
```

Build 3D from per-hit decoded/unrolled data plus trigger-relative time, not the
reduced 2D phosphor arrays. Keep the 2D release tag immutable and retain the
single-subscriber/read-only architecture.
