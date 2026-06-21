"""Manages websockify child processes — one per VNC device.

Includes a background watchdog that periodically reaps dead processes,
frees their ports, and auto-restarts proxies for reachable devices.

Proxies are only active while at least one web UI viewer is connected.
When the last viewer disconnects, all proxies are stopped after a short
grace period (to survive page refreshes).
"""

import os
import socket
import subprocess
import logging
import threading
import time
from typing import Optional

log = logging.getLogger("proxy_manager")

# ── Configurable port range ────────────────────────────────────────────
# Defaults to 16 ports (6100-6115). Override via environment variables.
PORT_START = int(os.environ.get("WS_PORT_START", "6100"))
PORT_COUNT = int(os.environ.get("WS_PORT_COUNT", "16"))
PORT_END = PORT_START + PORT_COUNT - 1

# Watchdog checks every this many seconds
WATCHDOG_INTERVAL = 15

# Grace period (seconds) before stopping proxies after last viewer leaves.
# Allows page refreshes without killing everything.
VIEWER_GRACE_SECONDS = int(os.environ.get("VIEWER_GRACE_SECONDS", "10"))




def _websockify_stdio() -> tuple:
    """Return (stdout, stderr) for websockify child; inherit stderr when debugging."""
    if os.environ.get("WEBSOCKIFY_DEBUG", "").strip().lower() in (
        "1", "true", "yes", "on",
    ):
        return subprocess.DEVNULL, None
    return subprocess.DEVNULL, subprocess.DEVNULL


def _tcp_reachable(host: str, port: int, timeout: float = 5.0) -> bool:
    """Quick TCP check to see if a VNC target is reachable."""
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


class ProxyManager:
    """Spawn / stop / restart websockify subprocesses for VNC devices.

    Proxies only run while at least one web UI viewer is connected.
    A background watchdog thread periodically reaps dead proxies and
    auto-restarts them if the target is reachable.
    """

    def __init__(self):
        self._lock = threading.Lock()
        # device_id -> {"process": Popen, "port": int, "host": str,
        #               "vnc_port": int, "started_at": float}
        self._proxies: dict[int, dict] = {}
        self._used_ports: set[int] = set()

        # Viewer tracking
        self._viewer_count = 0
        self._viewer_lock = threading.Lock()
        self._grace_timer: Optional[threading.Timer] = None

        # Pending restarts for unreachable targets
        self._pending_restarts: dict[int, dict] = {}

        # Watchdog
        self._watchdog_thread: Optional[threading.Thread] = None
        self._watchdog_stop = threading.Event()

    # ── helpers ────────────────────────────────────────────────────────

    def _next_port(self) -> Optional[int]:
        for p in range(PORT_START, PORT_END + 1):
            if p not in self._used_ports:
                return p
        return None

    def _kill_process(self, proc: subprocess.Popen) -> None:
        """Terminate a subprocess, escalating to kill if needed."""
        if proc.poll() is not None:
            try:
                proc.wait(timeout=0)
            except Exception:
                pass
            return
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log.warning("websockify PID %d did not terminate, killing", proc.pid)
            proc.kill()
            try:
                proc.wait(timeout=3)
            except Exception:
                pass
        except Exception:
            pass

    def _spawn_websockify(self, host: str, vnc_port: int, ws_port: int) -> Optional[subprocess.Popen]:
        """Spawn a websockify subprocess with proper timeout flags."""
        target = f"{host}:{vnc_port}"
        cmd = [
            "websockify",
            "--web", "/app/static/novnc",
            f"0.0.0.0:{ws_port}",
            target,
        ]
        log.info("Starting websockify: %s", " ".join(cmd))
        out, err = _websockify_stdio()
        try:
            proc = subprocess.Popen(cmd, stdout=out, stderr=err)
            return proc
        except Exception as exc:
            log.error("Failed to start websockify for %s: %s", target, exc)
            return None

    # ── viewer tracking ────────────────────────────────────────────────

    def has_viewers(self) -> bool:
        """Return True if at least one web UI viewer is connected."""
        with self._viewer_lock:
            return self._viewer_count > 0

    def viewer_connected(self) -> int:
        """Called when a web UI viewer connects. Returns new viewer count.

        If this is the first viewer, starts all proxies for enabled devices.
        The caller must provide the device list via start_all_for_devices().
        """
        with self._viewer_lock:
            # Cancel any pending grace-period shutdown
            if self._grace_timer:
                self._grace_timer.cancel()
                self._grace_timer = None
            self._viewer_count += 1
            count = self._viewer_count
        log.info("Viewer connected (total: %d)", count)
        return count

    def viewer_disconnected(self) -> int:
        """Called when a web UI viewer disconnects. Returns new viewer count.

        If this was the last viewer, schedules proxy shutdown after grace period.
        """
        with self._viewer_lock:
            self._viewer_count = max(0, self._viewer_count - 1)
            count = self._viewer_count
            if count == 0:
                # Schedule shutdown after grace period
                if self._grace_timer:
                    self._grace_timer.cancel()
                self._grace_timer = threading.Timer(
                    VIEWER_GRACE_SECONDS, self._grace_shutdown
                )
                self._grace_timer.daemon = True
                self._grace_timer.start()
                log.info(
                    "Last viewer disconnected — proxies will stop in %ds if no reconnect",
                    VIEWER_GRACE_SECONDS,
                )
            else:
                log.info("Viewer disconnected (remaining: %d)", count)
        return count

    def _grace_shutdown(self):
        """Called after grace period expires with no viewers."""
        with self._viewer_lock:
            if self._viewer_count > 0:
                # A viewer reconnected during the grace period
                return
        log.info("Grace period expired, stopping all proxies (no viewers)")
        self.stop_all()

    def get_viewer_count(self) -> int:
        with self._viewer_lock:
            return self._viewer_count

    # ── watchdog ───────────────────────────────────────────────────────

    def start_watchdog(self):
        """Start the background watchdog thread."""
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            return
        self._watchdog_stop.clear()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop, daemon=True, name="proxy-watchdog"
        )
        self._watchdog_thread.start()
        log.info("Proxy watchdog started (interval=%ds)", WATCHDOG_INTERVAL)

    def stop_watchdog(self):
        """Signal the watchdog to stop."""
        self._watchdog_stop.set()
        if self._watchdog_thread:
            self._watchdog_thread.join(timeout=WATCHDOG_INTERVAL + 5)
            self._watchdog_thread = None

    def _watchdog_loop(self):
        """Background loop: reap dead proxies and auto-restart them."""
        while not self._watchdog_stop.wait(timeout=WATCHDOG_INTERVAL):
            try:
                self._watchdog_tick()
            except Exception:
                log.exception("Watchdog tick failed")

    def _watchdog_tick(self):
        """Single watchdog iteration: find dead proxies, restart reachable ones."""
        # Only do work if viewers are connected
        if not self.has_viewers():
            return

        dead_entries = []

        with self._lock:
            for device_id, info in list(self._proxies.items()):
                if info["process"].poll() is not None:
                    dead_entries.append((device_id, info))
                    self._proxies.pop(device_id)
                    self._used_ports.discard(info["port"])
                    try:
                        info["process"].wait(timeout=0)
                    except Exception:
                        pass

        if dead_entries:
            log.info(
                "Watchdog: found %d dead proxy(ies): %s",
                len(dead_entries),
                [f"device={did} port={info['port']}" for did, info in dead_entries],
            )

            for device_id, info in dead_entries:
                host = info["host"]
                vnc_port = info["vnc_port"]

                if _tcp_reachable(host, vnc_port):
                    new_port = self.start(device_id, host, vnc_port)
                    if new_port:
                        log.info(
                            "Watchdog: auto-restarted proxy for device %d → ws port %d",
                            device_id, new_port,
                        )
                    else:
                        log.warning(
                            "Watchdog: failed to restart proxy for device %d", device_id
                        )
                else:
                    log.info(
                        "Watchdog: device %d target %s:%d unreachable, will retry next tick",
                        device_id, host, vnc_port,
                    )
                    with self._lock:
                        if device_id not in self._proxies:
                            self._pending_restarts[device_id] = {
                                "host": host,
                                "vnc_port": vnc_port,
                            }

        # Retry pending restarts
        if self._pending_restarts:
            resolved = []
            for device_id, meta in list(self._pending_restarts.items()):
                with self._lock:
                    if device_id in self._proxies:
                        resolved.append(device_id)
                        continue

                if _tcp_reachable(meta["host"], meta["vnc_port"]):
                    new_port = self.start(device_id, meta["host"], meta["vnc_port"])
                    if new_port:
                        log.info(
                            "Watchdog: pending restart succeeded for device %d → ws port %d",
                            device_id, new_port,
                        )
                    resolved.append(device_id)

            for did in resolved:
                self._pending_restarts.pop(did, None)

    # ── public API ─────────────────────────────────────────────────────

    def start(self, device_id: int, host: str, vnc_port: int) -> Optional[int]:
        """Start a websockify proxy for the given device.

        Returns the WebSocket port number, or None if no port available.
        """
        with self._lock:
            # Already running?
            if device_id in self._proxies:
                existing = self._proxies[device_id]
                if existing["process"].poll() is None:
                    return existing["port"]
                # Process died — clean up
                self._proxies.pop(device_id)
                self._used_ports.discard(existing["port"])
                self._kill_process(existing["process"])

            port = self._next_port()
            if port is None:
                log.error("No available websockify ports (%d-%d exhausted)", PORT_START, PORT_END)
                return None

            proc = self._spawn_websockify(host, vnc_port, port)
            if proc is None:
                return None

            self._used_ports.add(port)
            self._proxies[device_id] = {
                "process": proc,
                "port": port,
                "host": host,
                "vnc_port": vnc_port,
                "started_at": time.monotonic(),
            }

            self._pending_restarts.pop(device_id, None)
            return port

    def stop(self, device_id: int) -> bool:
        """Stop the websockify proxy for a device."""
        with self._lock:
            info = self._proxies.pop(device_id, None)
            if info is None:
                self._pending_restarts.pop(device_id, None)
                return False
            self._used_ports.discard(info["port"])

        self._kill_process(info["process"])
        self._pending_restarts.pop(device_id, None)
        return True

    def restart(self, device_id: int, host: str, vnc_port: int) -> Optional[int]:
        """Stop then start a proxy."""
        self.stop(device_id)
        return self.start(device_id, host, vnc_port)

    def get_port(self, device_id: int) -> Optional[int]:
        """Return the WebSocket port for a device, or None."""
        with self._lock:
            info = self._proxies.get(device_id)
            if not info:
                return None
            if info["process"].poll() is None:
                return info["port"]
            self._proxies.pop(device_id)
            self._used_ports.discard(info["port"])
            self._kill_process(info["process"])
            return None

    def get_status(self, device_id: int) -> str:
        """Return 'running', 'stopped', or 'restarting'."""
        with self._lock:
            info = self._proxies.get(device_id)
            if not info:
                if device_id in self._pending_restarts:
                    return "restarting"
                return "stopped"
            if info["process"].poll() is None:
                return "running"
            self._proxies.pop(device_id)
            self._used_ports.discard(info["port"])
            self._kill_process(info["process"])
            return "stopped"

    def get_all(self) -> dict:
        """Return mapping of device_id -> {port, status}."""
        with self._lock:
            result = {}
            dead = []
            for did, info in list(self._proxies.items()):
                alive = info["process"].poll() is None
                if alive:
                    result[did] = {
                        "port": info["port"],
                        "status": "running",
                    }
                else:
                    result[did] = {
                        "port": info["port"],
                        "status": "stopped",
                    }
                    dead.append((did, info))

            for did, info in dead:
                self._proxies.pop(did, None)
                self._used_ports.discard(info["port"])

            for did in self._pending_restarts:
                if did not in result:
                    result[did] = {"port": None, "status": "restarting"}

            return result

    def reap_dead(self) -> list[int]:
        """Sweep all proxies, clean dead ones. Returns list of reaped device IDs."""
        reaped = []
        with self._lock:
            for did, info in list(self._proxies.items()):
                if info["process"].poll() is not None:
                    self._proxies.pop(did)
                    self._used_ports.discard(info["port"])
                    try:
                        info["process"].wait(timeout=0)
                    except Exception:
                        pass
                    reaped.append(did)
        if reaped:
            log.info("Reaped %d dead proxy(ies): %s", len(reaped), reaped)
        return reaped

    def stop_all(self):
        """Terminate every websockify subprocess."""
        with self._lock:
            proxies_copy = list(self._proxies.items())
            self._proxies.clear()
            self._used_ports.clear()
            self._pending_restarts.clear()

        for did, info in proxies_copy:
            self._kill_process(info["process"])

        log.info("All proxies stopped (%d terminated)", len(proxies_copy))

    def get_stats(self) -> dict:
        """Return diagnostic stats for debugging."""
        with self._lock:
            alive = sum(1 for info in self._proxies.values()
                        if info["process"].poll() is None)
            dead = sum(1 for info in self._proxies.values()
                       if info["process"].poll() is not None)
            return {
                "total_tracked": len(self._proxies),
                "alive": alive,
                "dead_not_yet_reaped": dead,
                "pending_restarts": len(self._pending_restarts),
                "ports_used": len(self._used_ports),
                "ports_available": (PORT_END - PORT_START + 1) - len(self._used_ports),
                "port_range": f"{PORT_START}-{PORT_END}",
                "port_count": PORT_COUNT,
                "viewer_count": self.get_viewer_count(),
            }


# Module-level singleton
proxy_manager = ProxyManager()
