# Initial validation — 2026-09-30

## Executed in the development container

- 22 pytest cases passed, including a real localhost ZMQ PUB -> spawned SUB collector -> shared-memory test and HTTP/WebSocket tests.
- Python bytecode compilation and JavaScript syntax checks passed.
- Uvicorn + the separate synthetic collector served a 409,600-pixel demo; HTTP status reported a healthy collector.
- Offline headless Chromium renderer smoke test passed at the default 30 FPS target with 330k synthetic updates/s. It exercised all eight canvases, module selection, pause/resume and zoom, with no JavaScript page errors. The browser test used fixture HTTP/WebSocket objects; direct browser access to localhost was restricted in this environment. It is not a VNC or ops01 performance test.

Synthetic decode + odd-parity validation + geometry lookup + state update, using 64 distinct replay messages:

| Words/message | Messages | Hits | Elapsed | Throughput |
|---|---:|---:|---:|---:|
| 1024 | 10,000 | 10,240,000 | 2.760 s | 3.71 million hits/s |
| 64 | 10,000 | 640,000 | 1.273 s | 0.503 million hits/s |

Environment: Python 3.13.5, NumPy 2.3.5, Linux x86_64. These are local measurements, not guarantees for daq03. The timing excludes network reception, IPC, browser/VNC and geometry loading; smaller wire messages have materially less headroom. Re-run the benchmark on daq03 using representative batch sizes.

## Still requires deployment validation

The complete production geometry JSONs were not available as local test fixtures. Geometry tests used explicit synthetic PacMon-format fixtures; the production loader must be run with the real files using `raw-display check`. The downloader is pinned to upstream Git blob hashes but was not exercised end-to-end in this network-restricted container.

No connection was made to a real PACMAN, daq03, ops01, or the production DAQ. Confirm the legacy16/v2 wire format, imported tile positions, CPU/network headroom and normal DAQ/PacMon behavior with the one-source probe before starting all sources. No claim of lossless raw reception is made.


## Production-debugging correction — 2026-09-30

The first daq03 probe exposed a decoder bug: the initial display rejected valid LArPix data when the downstream-marker bit was set, while PacMon's ADC/rate path records direction but does not reject on it. v0.2.0 removes that cut and adds per-second raw receive/packet-type/parity/direction diagnostics.

v0.2.0 also adds strict support for both known PACMAN envelopes (legacy 8+16-byte and new v1.0 24+24-byte framing), reads the ASIC packet family from the active CRS `RUN_CONFIG.json`, and refuses every web bind address except `127.0.0.1`.

Local post-fix regression: 29 pytest cases passed. On the same development container, the deliberately small-message synthetic benchmark gave ~95.7 khit/s at 8 words/message and ~706.5 khit/s at 64 words/message. The 8-word result is a reminder that per-message Python overhead matters: correctness should be established on daq03 first, then representative daq03 message batching/rates should be measured before claiming 330 khit/s headroom. Current collector batching can be optimized without changing the browser/API architecture.


## Batched live collector — 2026-09-30

After the real IOG-1 probe showed ~7.3–8.0 k ZMQ messages/s but only ~5–10 words/message, the live path was simplified to the actual Run-3 requirement: legacy16 PACMAN framing plus Packet_v2-compatible ASIC payloads. Packet_v3/new24 support was removed from the hot path.

The collector now drains up to 256 messages per PACMAN, concatenates their word bodies, and performs one NumPy decode/parity pass per batch. The receive HWM default was increased from 128 to 4096 messages/source. The active RUN_CONFIG is checked once at startup and v3-family IO groups are rejected.

The original one-message-at-a-time 8-word benchmark on daq03 was ~70.99 khit/s. Re-run the same benchmark after pulling v0.3.0 with `--batch-messages 256`; that number is the relevant offline comparison before the next all-IOG live probe.
