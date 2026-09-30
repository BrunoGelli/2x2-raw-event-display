"""Website + snapshot WebSockets. The browser never connects to a PACMAN."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import struct
import time
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from .runtime import snapshot, FIELDS, COL

FRAME_HEADER = struct.Struct("<4sIId")  # magic, sequence, count, server monotonic seconds
UPDATE = np.dtype([("id", "<u4"), ("age", "<f4")])


def encode_frame(previous, current, now, sequence):
    ids = np.flatnonzero(current != previous)
    records = np.empty(len(ids), dtype=UPDATE)
    records["id"] = ids
    records["age"] = np.where(current[ids] > 0, np.maximum(0, now - current[ids]), 1e9)
    return FRAME_HEADER.pack(b"RDP1", sequence, len(ids), now) + records.tobytes()


def create_app(geometry, shared, proc, demo=False, frame_hz=10.0, max_clients=4):
    static = Path(__file__).with_name("static")
    hub = {"seen": np.zeros(len(geometry.pixels)), "stats": np.zeros((9, len(FIELDS))),
           "heartbeat": 0.0, "sequence": 0, "clients": 0}

    async def pump():
        while True:
            # Copy under a short lock in a helper thread, never hold it during network I/O.
            seen, stats, heartbeat = await asyncio.to_thread(snapshot, shared)
            hub.update(seen=seen, stats=stats, heartbeat=heartbeat,
                       sequence=(hub["sequence"] + 1) & 0xffffffff)
            await asyncio.sleep(1 / frame_hz)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(pump())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="2x2 raw display", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    @app.get("/api/geometry")
    def geometry_meta():
        return {**geometry.metadata, "mode": "DEMO" if demo else "LIVE",
                "wire_format": "legacy16 / Packet_v2 / batched", "frame_hz": frame_hz}

    @app.get("/api/geometry.bin")
    def geometry_binary():
        return Response(geometry.pixels.tobytes(), media_type="application/octet-stream")

    @app.get("/api/status")
    def status():
        now = time.monotonic()
        sources = []
        for iog in geometry.metadata["iogs"]:
            row = hub["stats"][iog]
            data = {key: int(row[COL[key]]) for key in FIELDS if not key.startswith("last_")}
            data["iog"] = iog
            for name in ("rx", "trigger"):
                value = row[COL["last_" + name]]
                data[name + "_age_s"] = max(0, now - value) if value else None
            sources.append(data)
        age = max(0, now - hub["heartbeat"]) if hub["heartbeat"] else None
        healthy = bool(proc.is_alive() and age is not None and age < 3)
        return dict(mode="DEMO" if demo else "LIVE", collector_alive=proc.is_alive(),
                    collector_healthy=healthy, collector_age_s=age, sources=sources,
                    clients=hub["clients"], transport_loss="not measurable from this stream",
                    time_basis="host arrival; not synchronized detector time")

    @app.get("/healthz")
    def health():
        value = status()
        return JSONResponse(value, status_code=200 if value["collector_healthy"] else 503)

    @app.websocket("/ws")
    async def ws_stream(ws: WebSocket):
        # No cross-origin browser use by default. This is not authentication.
        origin = ws.headers.get("origin")
        allowed = {"http://" + ws.headers.get("host", ""), "https://" + ws.headers.get("host", "")}
        if (origin and origin not in allowed) or hub["clients"] >= max_clients:
            await ws.close(code=1008)
            return
        await ws.accept()
        if ws.query_params.get("geometry", geometry.metadata["geometry_id"]) != geometry.metadata["geometry_id"]:
            await ws.close(code=4009)  # Reload geometry after a server/configuration change.
            return
        hub["clients"] += 1
        previous = np.zeros(len(geometry.pixels))
        last_sequence = -1
        try:
            while True:
                if hub["sequence"] == last_sequence:
                    await asyncio.sleep(1 / frame_hz)
                    continue
                current, sequence = hub["seen"], hub["sequence"]
                frame = encode_frame(previous, current, time.monotonic(), sequence)
                await asyncio.wait_for(ws.send_bytes(frame), timeout=3)
                # One frame in flight. Slow/tab-hidden clients skip intermediate snapshots.
                # Diff against their own last delivered state, not global deltas.
                ack = await asyncio.wait_for(ws.receive_text(), timeout=3)
                if ack != str(sequence):
                    await ws.close(code=1008)
                    return
                previous, last_sequence = current, sequence
        except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError):
            pass
        finally:
            hub["clients"] -= 1
            try:
                await ws.close()
            except RuntimeError:
                pass

    return app
