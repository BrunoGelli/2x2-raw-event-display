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
4. Click **Kiosk**. The selected plot fills the entire screen, with a **transparent
   detector-time readout at top-left** and the **official transparent 2×2 logo
   at top-right**. All dashboard panels and module headings disappear, with
   no bar across the plot. Move the pointer to reveal the temporary controls
   at **bottom-right** (rotation, theme and exit).
5. Press **Esc** (or **Exit kiosk**) to return to the previous dashboard view.
   Switching views is intentionally done outside kiosk so the selection cannot
   change unexpectedly during a presentation.

A successful Fullscreen API request hides the browser chrome. If browser/remote
desktop policy prevents it, the kiosk still fills the available tab; the browser
toolbar may remain visible. You can use browser F11 when applicable. If already
in fullscreen before kiosk, leaving fullscreen also exits kiosk.

The time overlay reuses the **existing PPS-qualified playback clock**, including
PAUSED/STALE/CLOCK WARNING labels. As elsewhere in the dashboard, it reports
the **oldest qualified playhead across all configured IO groups**, not the
browser wall clock or a precise unique interaction timestamp. When the clock
is unavailable it explicitly says so. The text switches between light ink on
dark and dark ink on light, and the logo is bundled locally as a compact
transparent WebP asset derived from the supplied official image. Both
overlays let pointer input pass through to the plot.

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
python -m pytest -q tests/test_kiosk_overlays.py tests/test_kiosk_frontend.py tests/test_beam_delay.py
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
python -m pytest -q tests/test_kiosk_overlays.py tests/test_kiosk_frontend.py
```

The Node test covers view preservation, light/dark switching, 3D rotation
controls, fullscreen/Escape and the fallback when fullscreen is denied.
The opt-in 2D browser and 3D WebGL fixtures also check the transparent
timestamp, bundled official logo and light/dark contrast. Run those with
`RAW_DISPLAY_BROWSER_TEST=1` or `RAW_DISPLAY_3D_BROWSER_TEST=1` if the
corresponding Chromium/Playwright dependencies are available.
Always check that camera rotation, plot scaling and hit contrast look right on
the actual OPS01 display.
