"""Manages websockify child processes — one per VNC device."""

import os
import subprocess
import logging
import threading
from typing import Optional

log = logging.getLogger("proxy_manager")

PORT_START = 6100
PORT_END = 6199


def _websockify_stdio() -> tuple:
    """Return (stdout, stderr) for websockify child; inherit stderr when debugging."""
    if os.environ.get("WEBSOCKIFY_DEBUG", "").strip().lower() in (
        "1", "true", "yes", "on",
    ):
        return subprocess.DEVNULL, None
    return subprocess.DEVNULL, subprocess.DEVNULL


class ProxyManager:
    """Spawn / stop / restart websockify subprocesses for VNC devices."""

    def __init__(self):
        self._lock = threading.Lock()
        # device_id -> {"process": Popen, "port": int, "host": str, "vnc_port": int}
        self._proxies: dict[int, dict] = {}
        self._used_ports: set[int] = set()

    # ── helpers ────────────────────────────────────────────────────────

    def _next_port(self) -> Optional[int]:
        for p in range(PORT_START, PORT_END + 1):
            if p not in self._used_ports:
                return p
        return None

    def _reap_proxy_unlocked(self, device_id: int, info: dict) -> None:
        """Remove a dead proxy entry, free its port, and reap the child. Lock held."""
        self._proxies.pop(device_id, None)
        self._used_ports.discard(info["port"])
        proc = info["process"]
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        else:
            try:
                proc.wait(timeout=0)
            except Exception:
                pass

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
                self._reap_proxy_unlocked(device_id, existing)

            port = self._next_port()
            if port is None:
                log.error("No available websockify ports (6100-6199 exhausted)")
                return None

            target = f"{host}:{vnc_port}"
            cmd = ["websockify", "--web", "/app/static/novnc",
                   f"0.0.0.0:{port}", target]
            log.info("Starting websockify: %s", " ".join(cmd))
            out, err = _websockify_stdio()
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=out,
                    stderr=err,
                )
            except Exception as exc:
                log.error("Failed to start websockify for device %s: %s",
                          device_id, exc)
                return None

            self._used_ports.add(port)
            self._proxies[device_id] = {
                "process": proc,
                "port": port,
                "host": host,
                "vnc_port": vnc_port,
            }
            return port

    def stop(self, device_id: int) -> bool:
        """Stop the websockify proxy for a device."""
        with self._lock:
            info = self._proxies.pop(device_id, None)
            if info is None:
                return False
            proc = info["process"]
            self._used_ports.discard(info["port"])
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
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
            self._reap_proxy_unlocked(device_id, info)
            return None

    def get_status(self, device_id: int) -> str:
        """Return 'running' or 'stopped'."""
        with self._lock:
            info = self._proxies.get(device_id)
            if not info:
                return "stopped"
            if info["process"].poll() is None:
                return "running"
            self._reap_proxy_unlocked(device_id, info)
            return "stopped"

    def get_all(self) -> dict:
        """Return mapping of device_id -> {port, status}."""
        with self._lock:
            result = {}
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
                    self._reap_proxy_unlocked(did, info)
            return result

    def stop_all(self):
        """Terminate every websockify subprocess."""
        with self._lock:
            for did in list(self._proxies.keys()):
                info = self._proxies.pop(did)
                proc = info["process"]
                self._used_ports.discard(info["port"])
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            self._proxies.clear()
            self._used_ports.clear()


# Module-level singleton
proxy_manager = ProxyManager()
