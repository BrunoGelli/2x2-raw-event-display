# Kiosk presentation, automatic 3D orbit and light theme

This browser-only feature builds on `fix/beam-trigger-delay-20261110`. It does **not**
change collector subscriptions, PACMAN connections, trigger/beam timing, drift
reconstruction, or the HTTP/WebSocket protocol.

## Using it

1. In **View**, choose **All modules**, one **Module**, or **3D detector**.
2. Optionally choose **Light mode**. The default is the original dark phosphor
   appearance. The light theme adjusts the UI, 2D pixels, trigger candidates,
   and WebGL scene colors. The choice persists in browser-local storage.
3. In the 3D view, choose **Auto-rotate: On** to orbit continuously around the
   detector; normal mouse orbit/zoom still works. This control is not shown for 2D.
4. Click **Kiosk**. Only the selected plot fills the screen. Status panels,
   module headings and controls disappear. Move the pointer to reveal a small
   temporary control bar for rotation, theme and exit.
5. Press **Esc** (or **Exit kiosk**) to return to the previous dashboard view.
   Switching views is intentionally done outside kiosk so the selection cannot
   change unexpectedly during a presentation.

A successful Fullscreen API request hides the browser chrome. If browser/remote
desktop policy prevents it, the kiosk still fills the available tab; the browser
toolbar may remain visible. You can use browser F11 when applicable. If already
in fullscreen before kiosk, leaving fullscreen also exits kiosk.

Automatic rotation is a display-only OrbitControls setting (about one full
revolution per minute). It starts/stops without reloading geometry or opening
a new WebSocket. Theme changes likewise do not reconnect to the DAQ.

## Install on the deployed DAQ03 checkout

Stop only the existing **raw-display** process before switching code; do not
stop DAQ or PacMon processes. Make sure any locally patched files are safely
saved first:

```bash
cd /data/2x2-raw-event-display
git status --short
git fetch origin
git switch --track origin/feature/kiosk-auto-rotate-light-theme-20261112
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest -q tests/test_kiosk_frontend.py tests/test_beam_delay.py
```

If the local feature branch already exists, use
`git switch feature/kiosk-auto-rotate-light-theme-20261112` instead of
`git switch --track`.

Start the usual **single** observer process on loopback, keeping the
already-validated trigger flags and `RAW_DISPLAY_BEAM_DELAY_US` setting:

```bash
RAW_DISPLAY_3D=1 RAW_DISPLAY_TRIGGER_VIEW=1 RAW_DISPLAY_TRIGGER_TYPES="" \
raw-display serve --time-basis asic \
  --geometry-dir /data/2x2-raw-event-display/layout \
  --host 127.0.0.1 --port 8765
```

If you normally set `RAW_DISPLAY_BEAM_DELAY_US`, preserve that same value
before `raw-display serve`; kiosk mode makes no change to it. Refresh the
OPS01 browser tab (hard refresh if the old UI is cached).

## Verification

Without detector hardware:

```bash
node tests/test_kiosk.js
python -m pytest -q tests/test_kiosk_frontend.py
```

The Node test covers view preservation, light/dark switching, 3D rotation
controls, fullscreen/Escape and the fallback when fullscreen is denied.
The existing opt-in browser/WebGL fixture can be used for graphical validation.
Always check that camera rotation, plot scaling and hit contrast look right on
the actual OPS01 display.
