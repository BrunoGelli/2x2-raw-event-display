# 2×2 raw event display

A read-only PACMAN observer and a browser-based **2D phosphor display**. The collector and website run on **acd-daq03**, separately from PacMon and the production DAQ. A browser on **ops01** draws the display; shifters can use their existing VNC connection.

This first version is deliberately **not** an event builder. It shows physical pixels fading after their most recent **host-arrival** hit. It does not infer drift coordinates, align PACMAN clocks, calibrate charge, or associate charge with light/beam triggers. Trigger and sync words are counted, not reconstructed.

## Install on daq03

Use a separate environment, not the running CRS/PacMon environment. Python 3.9+ is required; Python 3.11+ is preferable when already available.

```bash
git clone https://github.com/BrunoGelli/2x2-raw-event-display.git
cd 2x2-raw-event-display
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest -q
```

Only NumPy, PyZMQ, FastAPI, Uvicorn, and the WebSocket transport are runtime dependencies. There is no Go, Node, npm, Plotly, LArPix-control, database, or external CDN dependency.

**ops01 needs only a modern browser.** HTML, CSS, JavaScript, geometry, and display updates are served by daq03. Nothing is installed on ops01 by this project.

## First: a no-hardware demo

```bash
raw-display demo --host 0.0.0.0 --port 8765
```

Open `http://acd-daq03:8765/` on ops01. The page is prominently marked **DEMO** and uses synthetic geometry and approximately 330,000 synthetic hits/s. No PACMAN sockets or CRS files are accessed in demo mode. Ctrl+C stops our server and collector.

The web server defaults to loopback; `--host 0.0.0.0` intentionally exposes it on the machine's interfaces. Use only the trusted operations subnet with existing firewall/access restrictions. This MVP has no authentication and must not be exposed to the Internet. Binding to daq03's specific operations-subnet IP is preferable on a multihomed host.

The browser offers persistence control, module selection, zoom/pan, pixel hover, local pause/resume, fullscreen, and a 15/30/60 FPS target. The default is 30 FPS to reduce VNC/CPU load; this is a target, not a performance guarantee. Backend snapshots default to 10 Hz. Pause affects the view, not ingestion.

## Use PacMon geometry, not a new detector map

The loader reads these existing PacMon JSON files directly:

```
geometry_mod0_v4.json
geometry_mod1_v4.json
geometry_mod2_v4.json
geometry_mod3_v4.json
```

Point `--geometry-dir` at an existing PacMon `layout` directory. Alternatively, fetch a verified snapshot once:

```bash
raw-display fetch-geometry --geometry-dir layout
```

The downloader pins `BrunoGelli/2x2Pacmon` main at commit `2cf0e2c7db056dd205efb7f41616c1795fa9ea67` and verifies the Git blob hash of every downloaded JSON. Downloads are explicit: **the live application never fetches anything from GitHub**. Existing nonmatching files are not overwritten. Custom local PacMon files remain supported through `--geometry-dir`; their SHA256 fingerprints are reported by `check`.

The mapping reproduces PacMon's `cmd/pacmon/plot.go`:

```python
module = (io_group - 1) // 2
geometry_io_group = 2 - (io_group % 2)
local_tile = (io_channel - 1) // 4 + 1
geometry_tile = local_tile + 8 * (1 - (io_group % 2))
```

For example, IOG 6 / local tile 5 uses module 2's geometry IO group 2 / tile 13. All four IO channels belonging to a tile map to the same physical pixels, so rerouting a Hydra within that tile does not require rebuilding a route-specific geometry map. The imported coordinates retain the tile swaps and orientations in PacMon's files. Positive geometry Y is displayed upward. Canvas rasters are built per tile, so physical gaps between tiles are retained.

Unknown addresses are counted and excluded, never plotted at a guessed origin. Invalid grids, missing tiles and overlapping geometry entries fail startup. The pixel total is the number in the geometry, **not** a count of enabled/live channels.

## The PACMAN address file remains authoritative

Every live/probe startup reads:

```
/home/acd/acdaq/CRS_DAQ/daq0/crs_daq/io/pacman.json
```

No duplicate PACMAN address list is stored in this repository. The expected structure is the existing `PACMAN_IO` JSON with `io_group: [[integer, hostname_or_IP], ...]`. The file is read only; address changes take effect on restart. Use `--pacman-config` to explicitly select another file. This 2×2 implementation accepts IO groups 1–8 and IPv4 addresses/hostnames without ports.

## Validate before live use

Check geometry/configuration without opening **any** sockets:

```bash
raw-display check --geometry-dir layout
```

Then, in an operations-approved test, subscribe to just one PACMAN and print diagnostics without running a web server:

```bash
raw-display probe --geometry-dir layout --iog 1 --seconds 30
```

The probe reports mapped hits/s, mean words/message, malformed messages and unmapped hits. Compare the normal DAQ/PacMon rates, host load and PACMAN/network load before, during and after the probe. Unexpected mapping or framing errors are a reason to stop and investigate, not to disable validation.

After that check, start one source in the browser:

```bash
raw-display serve --geometry-dir layout --iog 1 --host 0.0.0.0 --port 8765
```

Remove `--iog 1` to use all IO groups from the authoritative file:

```bash
raw-display serve --geometry-dir layout --host 0.0.0.0 --port 8765
```

Use `--frame-hz 5` for fewer browser snapshots. A `deploy/raw-display.service.example` is included for later supervised deployment; it is not installed automatically. Keep one application instance: multiple independent instances create additional PACMAN subscribers.

## Wire format: explicit and limited

This release supports **legacy PACMAN DATA messages: an 8-byte header plus N 16-byte words, containing 8-byte LArPix-v2 packets**. This matches the inspected PacMon `pkg/message.go` and `pkg/packet.go`. NumPy performs bulk field extraction and parity checking. Only upstream, valid-odd-parity v2 data packets become pixels. Configuration, trigger, sync, bad-parity and downstream packets are not painted.

**Rev5/24-byte envelopes and LArPix-v3 data decoding are not implemented in this release.** Do not point it at those formats expecting autodetection. Wrong message lengths/header types are rejected and counted. ASIC families cannot in general be inferred safely from an envelope: verify that the selected sources are v2. A large non-data count with no mapped hits warrants investigation.

## Architecture and overload behavior

```
PACMAN data PUB sockets (:5556)
        -> Python collector process on daq03
           -> vectorized decode + geometry LUT + last-arrival state
           -> fixed-size shared-memory snapshots
              -> FastAPI on daq03 (HTTP + WebSocket)
                 -> Canvas in ops01's browser
                    -> existing VNC to shifters
```

The collector only creates SUB sockets on data port 5556. It never creates a command/control socket, writes registers, resets a PACMAN, modifies configs, or changes PacMon/DAQ services. It requests lower CPU priority where supported.

**Read-only is not zero-cost:** another subscriber adds PACMAN/network work, even on the same receiving machine as PacMon. The actual dataserver/topology and available headroom still need commissioning. A SUB client alone does not prove that the production publisher is isolated from every possible overload.

Receive HWM defaults to 128 **messages per source**, not 128 hits. Messages are length-limited; application state uses fixed-size arrays. There is no unbounded event queue or browser queue feeding back into collection. HWM bounds are not an exact total-memory or latency guarantee; kernel/publisher queues also exist.

Each browser acknowledges one snapshot before the next is sent. A slow client receives a diff against its **own last delivered state**, so skipping intermediate snapshots does not permanently lose a pixel's latest activity. Unresponsive clients time out; geometry changes trigger a reload. The default is at most four browser connections. This is intentionally a lossy visualization: it coalesces repeated hits per pixel and does not preserve individual event history.

The UI distinguishes receiving/silent sources and stale/failed collector state. Its counters describe **received** data. Ordinary received message counts cannot prove that upstream/ZMQ dropped nothing; transport loss is explicitly shown as unmeasurable. Do not interpret a healthy display or matching average rates as a losslessness certification.

## Benchmark and tests

```bash
raw-display benchmark --words 1024 --messages 10000
raw-display benchmark --words 64 --messages 10000
# With the actual geometry files:
raw-display benchmark --geometry-dir layout --words 1024 --messages 10000
python -m pytest -q
```

The benchmark replays synthetic wire messages through parity checking, decoding, geometry lookup and state updates. It does **not** include ZMQ transport, shared-memory publication, actual hardware bursts, browser rendering or VNC. Words/message matters: matching the real batch size is more informative than quoting only hits/s.

Tests cover independent byte-layout examples, field boundaries, parity/direction filtering, malformed frames, special words, Hydra route aliases, IO-group remapping, configuration rereading, snapshot catch-up, HTTP/WebSocket exchange, and an actual local ZMQ publisher/collector pair. See `VALIDATION.md` for the initial local checks and their limits.

## Sources

- PacMon geometry/mapping: https://github.com/BrunoGelli/2x2Pacmon/tree/2cf0e2c7db056dd205efb7f41616c1795fa9ea67/layout
- PacMon wire definitions: https://github.com/BrunoGelli/2x2Pacmon/tree/2cf0e2c7db056dd205efb7f41616c1795fa9ea67/pkg
- PacMon plot conventions: https://github.com/BrunoGelli/2x2Pacmon/blob/2cf0e2c7db056dd205efb7f41616c1795fa9ea67/cmd/pacmon/plot.go

Upstream PacMon is Apache-2.0 licensed; see `NOTICE.md` for attribution. Its geometry files are downloaded separately, not silently regenerated here.
