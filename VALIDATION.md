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
