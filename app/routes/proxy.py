"""WebSocket proxy management endpoints."""

from fastapi import APIRouter, HTTPException

from app import database as db
from app.services.proxy_manager import proxy_manager

router = APIRouter()


@router.post("/api/proxy/{device_id}/start")
async def start_proxy(device_id: int):
    device = db.get_device(device_id)
    if not device:
        raise HTTPException(404, "Device not found")
    port = proxy_manager.start(device_id, device["host"], device["port"])
    if port is None:
        raise HTTPException(503, "No available proxy ports")
    return {"device_id": device_id, "ws_port": port, "status": "running"}


@router.post("/api/proxy/{device_id}/stop")
async def stop_proxy(device_id: int):
    proxy_manager.stop(device_id)
    return {"device_id": device_id, "status": "stopped"}


@router.post("/api/proxy/start-all")
async def start_all_proxies():
    devices = db.get_all_devices()
    started = 0
    for d in devices:
        if d.get("enabled"):
            port = proxy_manager.start(d["id"], d["host"], d["port"])
            if port is not None:
                started += 1
    return {"status": "started", "count": started}


@router.post("/api/proxy/stop-all")
async def stop_all_proxies():
    proxy_manager.stop_all()
    return {"status": "all_stopped"}


@router.post("/api/proxy/{device_id}/restart")
async def restart_proxy(device_id: int):
    device = db.get_device(device_id)
    if not device:
        raise HTTPException(404, "Device not found")
    port = proxy_manager.restart(device_id, device["host"], device["port"])
    if port is None:
        raise HTTPException(503, "No available proxy ports")
    return {"device_id": device_id, "ws_port": port, "status": "running"}


@router.get("/api/proxy/status")
async def proxy_status():
    return proxy_manager.get_all()
