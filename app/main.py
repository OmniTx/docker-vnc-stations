"""FastAPI application — entry point for VNC Monitor Dashboard."""

import asyncio
import base64
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import database as db
from app.routes import devices as devices_router
from app.routes import proxy as proxy_router
from app.services.health import health_checker
from app.services.proxy_manager import proxy_manager

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
log = logging.getLogger("main")


# ── Lifespan ───────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown hooks."""
    # ── startup ──
    db.init_db()
    log.info("Database initialized")

    # Start health checker
    interval = int(os.environ.get("HEALTH_CHECK_INTERVAL", "30"))
    health_checker.start(interval)

    # Start the proxy watchdog for auto-recovery (runs only when viewers active)
    proxy_manager.start_watchdog()

    # NOTE: We no longer start proxies at boot.
    # Proxies start on-demand when a web UI viewer connects via SSE.
    log.info("Ready — proxies will start when a viewer connects")

    yield

    # ── shutdown ──
    proxy_manager.stop_watchdog()
    health_checker.stop()
    proxy_manager.stop_all()
    log.info("All proxies stopped")


# ── App ────────────────────────────────────────────────────────────────

app = FastAPI(title="VNC Monitor Dashboard", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Basic Auth Middleware ──────────────────────────────────────────────

@app.middleware("http")
async def basic_auth_middleware(request: Request, call_next):
    """Enforce HTTP Basic Auth if app_password is configured."""
    # Skip auth for liveliness
    if request.url.path == "/health":
        return await call_next(request)

    try:
        settings = db.get_all_settings()
    except Exception:
        log.exception("Settings load failed; denying request (fail closed)")
        return Response(status_code=503, content="Service temporarily unavailable")

    app_password = settings.get("app_password", "")
    app_username = settings.get("app_username", "admin")

    if not app_password:
        return await call_next(request)

    auth = request.headers.get("Authorization", "")
    if auth.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8")
            username, _, password = decoded.partition(":")
            if (
                secrets.compare_digest(username, app_username)
                and secrets.compare_digest(password, app_password)
            ):
                return await call_next(request)
        except Exception:
            pass

    return Response(
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="VNC Monitor Dashboard"'},
        content="Unauthorized",
    )


# ── Routers ────────────────────────────────────────────────────────────

app.include_router(devices_router.router)
app.include_router(proxy_router.router)


# ── Viewer connect / disconnect helpers ────────────────────────────────

def _start_all_proxies_for_viewer():
    """Start proxies for all enabled devices (called when first viewer arrives)."""
    devices = db.get_all_devices()
    started = 0
    for device in devices:
        if device["enabled"]:
            port = proxy_manager.start(device["id"], device["host"], device["port"])
            if port:
                started += 1
                time.sleep(0.05)  # Small stagger
    log.info("Viewer arrived — started %d/%d proxies", started, len(devices))


# ── SSE endpoint ───────────────────────────────────────────────────────

@app.get("/api/events")
async def sse_events():
    """Server-Sent Events stream for real-time status updates.

    Each SSE connection represents one active viewer. When the first viewer
    connects, all proxies are started. When the last viewer disconnects,
    proxies stop after a grace period.
    """
    # Viewer connects
    viewer_count = proxy_manager.viewer_connected()
    if viewer_count == 1:
        # First viewer — start all proxies in a background thread
        import threading
        threading.Thread(
            target=_start_all_proxies_for_viewer, daemon=True
        ).start()

    async def generate():
        last_statuses = {}
        last_latencies = {}
        last_proxies = {}
        try:
            while True:
                try:
                    current_statuses = health_checker.get_all_statuses()
                    current_latencies = health_checker.get_all_latencies()
                    current_proxies = proxy_manager.get_all()

                    if (current_statuses != last_statuses or 
                        current_latencies != last_latencies or
                        current_proxies != last_proxies):
                        payload = {
                            "type": "status_update",
                            "health": {str(k): v for k, v in current_statuses.items()},
                            "latencies": {str(k): v for k, v in current_latencies.items()},
                            "proxies": {str(k): v for k, v in current_proxies.items()},
                        }
                        yield f"data: {json.dumps(payload)}\n\n"
                        last_statuses = dict(current_statuses)
                        last_latencies = dict(current_latencies)
                        last_proxies = dict(current_proxies)
                    else:
                        yield ": keepalive\n\n"

                    await asyncio.sleep(2)
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    log.error("SSE error: %s", exc)
                    await asyncio.sleep(5)
        finally:
            # Viewer disconnects (page closed, tab closed, navigate away, etc.)
            proxy_manager.viewer_disconnected()

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "Connection": "keep-alive",
                                      "X-Accel-Buffering": "no"})


# ── Health endpoint ────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/proxy/diagnostics")
async def proxy_diagnostics():
    """Return proxy manager diagnostic stats for debugging."""
    return proxy_manager.get_stats()


# ── Explicit disconnect endpoint (called by frontend on beforeunload) ──

@app.post("/api/viewer/disconnect")
async def viewer_disconnect():
    """Explicit signal from frontend that the page is being closed.

    This provides a faster signal than waiting for SSE to timeout,
    though SSE disconnect is the authoritative mechanism.
    """
    # Note: SSE will also fire its finally block, but viewer_disconnected()
    # is safe to call even if count is already 0 (it clamps to 0).
    return {"ok": True}


# ── Serve frontend ────────────────────────────────────────────────────

@app.get("/")
async def root():
    return FileResponse("/app/static/index.html")


# Mount static AFTER route definitions so explicit routes take priority
app.mount("/static", StaticFiles(directory="/app/static"), name="static")
