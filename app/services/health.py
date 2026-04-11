"""Background health checker — TCP-pings every VNC device periodically."""

import socket
import logging
import threading
from apscheduler.schedulers.background import BackgroundScheduler

from app import database as db

log = logging.getLogger("health_checker")


class HealthChecker:
    """Periodically TCP-connect to every VNC device to check reachability."""

    def __init__(self):
        self._statuses: dict[int, str] = {}  # device_id -> "online"|"offline"|"error"
        self._scheduler: BackgroundScheduler | None = None
        self._lock = threading.Lock()

    # ── internal ───────────────────────────────────────────────────────

    @staticmethod
    def _tcp_check(host: str, port: int, timeout: float = 3.0) -> str:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return "online"
        except (socket.timeout, ConnectionRefusedError, OSError):
            return "offline"
        except Exception:
            return "error"

    def _run_check(self):
        """Called by the scheduler in a background thread."""
        try:
            devices = db.get_all_devices()
        except Exception as exc:
            log.error("Health check DB error: %s", exc)
            return

        for device in devices:
            status = self._tcp_check(device["host"], device["port"])
            with self._lock:
                self._statuses[device["id"]] = status

    # ── public API ─────────────────────────────────────────────────────

    def start(self, interval: int = 30):
        """Start the periodic health check scheduler."""
        if self._scheduler and self._scheduler.running:
            return
        self._scheduler = BackgroundScheduler(daemon=True)
        self._scheduler.add_job(
            self._run_check, "interval", seconds=interval,
            id="health_check", replace_existing=True,
        )
        self._scheduler.start()
        log.info("Health checker started (interval=%ds)", interval)
        # Run once immediately
        threading.Thread(target=self._run_check, daemon=True).start()

    def stop(self):
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None

    def check_device(self, host: str, port: int) -> str:
        return self._tcp_check(host, port)

    def get_status(self, device_id: int) -> str:
        with self._lock:
            return self._statuses.get(device_id, "unknown")

    def get_all_statuses(self) -> dict[int, str]:
        with self._lock:
            return dict(self._statuses)

    def set_status(self, device_id: int, status: str):
        with self._lock:
            self._statuses[device_id] = status

    def remove_device(self, device_id: int):
        with self._lock:
            self._statuses.pop(device_id, None)


# Module-level singleton
health_checker = HealthChecker()
