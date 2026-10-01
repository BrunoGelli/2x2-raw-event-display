# Installed code versus the process serving port 8765

A successful install/test/commit/push does not replace an already-running
collector or web server. Diagnose the process serving the port, not only the
version printed by a command in a different terminal.

## The subshell trap

An installation block wrapped in `( ... )` runs in a subshell. Its `cd` and
`source .venv/bin/activate` do not remain active in the parent shell after the
block exits. Both environments can print `(.venv)` in the prompt even though
one belongs to `2x2-raw-event-display` and the other belongs to
`2x2-raw-event-display-next`.

The original trigger/audit extension at f40d843 did not bump the package or CLI
version above 0.4.1. Version 0.4.1 alone therefore cannot distinguish it from the
preceding SYNC-only implementation. Use the commit, imported paths and the
running server's feature metadata instead.

## Confirm what is running (no detector connections)

From a terminal on daq03:

```bash
curl -fsS http://127.0.0.1:8765/api/geometry |
  python3 -m json.tool
```

The trigger/audit implementation in ASIC mode must report:

```json
{"time_basis": "asic", "feature_build": "trigger-audit-1"}
```

`trigger_view_enabled` is false unless `RAW_DISPLAY_TRIGGER_VIEW=1` was set on
the serving process. Trigger RX badges do not require that optional hit history.

The new ASIC server always registers `/api/timing-audit` and `/api/observers`.
Disabling the audit results in a successful JSON response with no enabled audit
IOGs, not HTTP 404. The host-time comparison app does not provide those routes.
A 404 means the origin/path is reaching a different app/build (or a proxy routing
problem); it is not fixed by refreshing browser assets or reinstalling a client.

## Launch this exact worktree and interpreter

Stop the existing **event-display** serving command with Ctrl+C in its own
terminal. Do not kill unrelated Python processes, PacMon, or DAQ services. If a
service supervisor restarts the old process, identify that display service and
stop/update it through the existing operations procedure. Do not start two
collectors, including on different ports, just to test the update.

The following commands use the existing next worktree. Run them in the serving
terminal, not only inside an earlier installation subshell:

```bash
cd /data/2x2-raw-event-display-next
/data/2x2-raw-event-display-next/.venv/bin/python -c \
  'import sys, raw_display, raw_display.timed_server as s; print(sys.executable); print(raw_display.__file__); print(s.__file__)'
git log -1 --oneline

RAW_DISPLAY_LATE_AUDIT_IOGS=6 \
/data/2x2-raw-event-display-next/.venv/bin/python -m raw_display serve \
  --time-basis asic \
  --geometry-dir /data/2x2-raw-event-display/layout \
  --host 127.0.0.1 --port 8765
```

The import paths must identify `2x2-raw-event-display-next`, not the original
checkout. These checks import code only; they do not open PACMAN connections.
The final serving command does connect to the usual configured data streams.
Retain the existing approved loopback forwarding path. No public/subnet bind is
needed or allowed.

For yellow trigger-window matching, set `RAW_DISPLAY_TRIGGER_VIEW=1` on that
same serving invocation after checking the audit-only run. Matching still has
the documented same-IOG limitation. See `trigger_audit.md`.

In a second terminal, confirm the new metadata, then capture the audit:

```bash
curl -fsS http://127.0.0.1:8765/api/timing-audit | python3 -m json.tool
/data/2x2-raw-event-display-next/.venv/bin/python \
  /data/2x2-raw-event-display-next/tools/inspect_timing_audit.py \
  --iog 6 --seconds 15 --json-out /tmp/iog6-timing-audit.json
```

Audit initialization may take a publication interval. The JSON should show IOG
6 in `enabled_iogs` and a recent `capture_age_s`. The output file is created
exclusively; choose a different name if it already exists. Finally hard-reload
the ops01 page so it loads the new JavaScript from the same server.

## Updating without divergent local implementations

Use the already-published feature branch/worktree as the deployment checkout:

```bash
cd /data/2x2-raw-event-display-next
git status --short
git pull --ff-only origin main
```

Stop on a dirty tree or rejected fast-forward and inspect rather than forcing a
reset, merge or push. Do not reapply the old patch: it is already committed.
Use one implementation on remote main, and one deliberate deployment restart.
