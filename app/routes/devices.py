"""Device CRUD, import/export, groups, settings, and token endpoints."""

import json
import logging
from fastapi import APIRouter, HTTPException, UploadFile, File, Query
from fastapi.responses import JSONResponse, StreamingResponse

from app import database as db
from app.models import (
    DeviceCreate, DeviceUpdate, DeviceReorderBatch,
    GroupCreate, GroupUpdate,
    ImportConfirm, SettingsUpdate,
)
from app.services.crypto import encrypt_password, decrypt_password
from app.services.proxy_manager import proxy_manager
from app.services.health import health_checker

log = logging.getLogger("routes.devices")
router = APIRouter()


# ═══════════════════════════════════════════════════════════════════════
#  DEVICES
# ═══════════════════════════════════════════════════════════════════════

@router.get("/api/devices")
async def list_devices():
    devices = db.get_all_devices()
    statuses = health_checker.get_all_statuses()
    proxy_info = proxy_manager.get_all()
    result = []
    for d in devices:
        d["health_status"] = statuses.get(d["id"], "unknown")
        pi = proxy_info.get(d["id"])
        d["ws_port"] = pi["port"] if pi else None
        d["proxy_status"] = pi["status"] if pi else "stopped"
        # Never send raw encrypted password to frontend
        d["has_password"] = bool(d.get("password"))
        d.pop("password", None)
        result.append(d)
    return result


@router.post("/api/devices")
async def create_device(device: DeviceCreate):
    data = device.model_dump()
    if data.get("password"):
        data["password"] = encrypt_password(data["password"])
    device_id = db.create_device(data)
    # Start websockify if enabled
    ws_port = None
    if data.get("enabled", True):
        ws_port = proxy_manager.start(device_id, data["host"], data["port"])
    created = db.get_device(device_id)
    created["ws_port"] = ws_port
    created["has_password"] = bool(created.get("password"))
    created.pop("password", None)
    return created


@router.put("/api/devices/{device_id}")
async def update_device(device_id: int, updates: DeviceUpdate):
    existing = db.get_device(device_id)
    if not existing:
        raise HTTPException(404, "Device not found")

    data = {k: v for k, v in updates.model_dump().items() if v is not None}
    if "password" in data and data["password"]:
        data["password"] = encrypt_password(data["password"])

    db.update_device(device_id, data)

    # Restart websockify if connection details changed
    host_changed = "host" in data or "port" in data
    enabled_changed = "enabled" in data
    updated = db.get_device(device_id)

    if enabled_changed and not updated["enabled"]:
        proxy_manager.stop(device_id)
    elif host_changed or enabled_changed:
        if updated["enabled"]:
            proxy_manager.restart(
                device_id, updated["host"], updated["port"]
            )

    updated["ws_port"] = proxy_manager.get_port(device_id)
    updated["has_password"] = bool(updated.get("password"))
    updated.pop("password", None)
    return updated


@router.delete("/api/devices/{device_id}")
async def delete_device(device_id: int):
    if not db.delete_device(device_id):
        raise HTTPException(404, "Device not found")
    proxy_manager.stop(device_id)
    health_checker.remove_device(device_id)
    return {"ok": True}


@router.post("/api/devices/{device_id}/test")
async def test_device(device_id: int):
    device = db.get_device(device_id)
    if not device:
        raise HTTPException(404, "Device not found")
    status = health_checker.check_device(device["host"], device["port"])
    health_checker.set_status(device_id, status)
    return {"device_id": device_id, "status": status}


@router.get("/api/devices/{device_id}/status")
async def device_status(device_id: int):
    device = db.get_device(device_id)
    if not device:
        raise HTTPException(404, "Device not found")
    status = health_checker.get_status(device_id)
    return {"device_id": device_id, "status": status}


@router.get("/api/devices/{device_id}/token")
async def device_token(device_id: int):
    """Return the plaintext VNC password for one-time use by the frontend."""
    device = db.get_device(device_id)
    if not device:
        raise HTTPException(404, "Device not found")
    password = decrypt_password(device.get("password", ""))
    return {"password": password}


@router.put("/api/devices/reorder")
async def reorder_devices(batch: DeviceReorderBatch):
    for u in batch.updates:
        db.update_device_order(u.device_id, u.sort_order, u.group_name)
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════
#  GROUPS
# ═══════════════════════════════════════════════════════════════════════

@router.get("/api/groups")
async def list_groups():
    groups = db.get_all_groups()
    counts = db.get_group_device_counts()
    for g in groups:
        g["device_count"] = counts.get(g["name"], 0)
    return groups


@router.post("/api/groups")
async def create_group_endpoint(group: GroupCreate):
    gid = db.create_group(group.name, group.color)
    return {"id": gid, "name": group.name, "color": group.color}


@router.put("/api/groups/{group_id}")
async def update_group_endpoint(group_id: int, updates: GroupUpdate):
    data = {k: v for k, v in updates.model_dump().items() if v is not None}
    if not db.update_group(group_id, data):
        raise HTTPException(404, "Group not found")
    return {"ok": True}


@router.delete("/api/groups/{group_id}")
async def delete_group_endpoint(group_id: int):
    if not db.delete_group(group_id):
        raise HTTPException(404, "Group not found")
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════
#  IMPORT / EXPORT
# ═══════════════════════════════════════════════════════════════════════

def _parse_mightyviewer_json(raw: dict) -> dict:
    """Parse MightyViewer JSON and return a preview dict."""
    groups_arr = raw.get("Groups", [])
    stations_arr = raw.get("Stations", [])

    group_map = {g["Id"]: g["Name"] for g in groups_arr}
    groups_found = list(set(group_map.values()))

    new_devices = []
    duplicates = []
    invalid = []

    for station in stations_arr:
        try:
            conn = station.get("ConnectionDetails", {})
            host = conn.get("HostAddress", "")
            if not host:
                invalid.append({"raw": station, "reason": "Empty host address"})
                continue

            port = conn.get("Port") or 5900
            password = conn.get("Password", "") or ""
            name = station.get("DisplayName", station.get("Alias", "Unknown"))
            group_id = station.get("GroupId")
            group_name = group_map.get(group_id, "Ungrouped") if group_id else "Ungrouped"
            enabled = station.get("Enabled", True)
            settings = station.get("Settings", {})
            view_only = settings.get("IgnoreInput", True)

            device_data = {
                "name": name,
                "host": host,
                "port": port,
                "password": password,
                "group_name": group_name,
                "view_only": view_only,
                "enabled": enabled,
            }

            existing = db.find_device_by_host_port(host, port)
            if existing:
                device_data["conflict_with"] = existing["name"]
                duplicates.append(device_data)
            else:
                new_devices.append(device_data)

        except Exception as exc:
            invalid.append({"raw": station, "reason": str(exc)})

    if "Ungrouped" not in groups_found and any(
        d.get("group_name") == "Ungrouped" for d in new_devices + duplicates
    ):
        groups_found.append("Ungrouped")

    return {
        "total_found": len(stations_arr),
        "new": new_devices,
        "duplicates": duplicates,
        "invalid": invalid,
        "groups_found": sorted(groups_found),
    }


@router.post("/api/import/json")
async def import_preview(file: UploadFile = File(...)):
    """Parse uploaded MightyViewer JSON and return a dry-run preview."""
    content = await file.read()
    try:
        raw = json.loads(content)
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON file")

    if "Stations" not in raw:
        raise HTTPException(400, "Missing 'Stations' key — not a MightyViewer file")

    preview = _parse_mightyviewer_json(raw)
    return preview


@router.post("/api/import/json/confirm")
async def import_confirm(body: ImportConfirm):
    """Commit parsed devices to the database and start proxies."""
    imported = 0
    skipped = 0

    for device_data in body.devices:
        host = device_data.get("host", "")
        port = device_data.get("port", 5900)
        existing = db.find_device_by_host_port(host, port)

        if existing and not body.overwrite_duplicates:
            skipped += 1
            continue

        if not body.preserve_view_only:
            device_data["view_only"] = False

        pw = device_data.get("password", "")
        if pw:
            device_data["password"] = encrypt_password(pw)

        if existing and body.overwrite_duplicates:
            db.update_device(existing["id"], device_data)
            if device_data.get("enabled", True):
                proxy_manager.restart(existing["id"], host, port)
            imported += 1
        else:
            did = db.create_device(device_data)
            if device_data.get("enabled", True):
                proxy_manager.start(did, host, port)
            imported += 1

    return {"imported": imported, "skipped": skipped}


@router.get("/api/export/json")
async def export_mightyviewer():
    """Export in MightyViewer-compatible JSON format."""
    devices = db.get_all_devices()
    # Build group list and ID mapping
    groups_set = sorted(set(d["group_name"] for d in devices))
    group_id_map = {}
    groups_out = []
    for idx, gname in enumerate(groups_set, start=1):
        if gname == "Ungrouped":
            continue
        group_id_map[gname] = idx
        groups_out.append({"Id": idx, "Name": gname, "Enabled": True})

    stations_out = []
    for d in devices:
        gid = group_id_map.get(d["group_name"])  # None for Ungrouped
        stations_out.append({
            "Id": d["id"],
            "GroupId": gid,
            "Alias": d["name"],
            "Enabled": bool(d["enabled"]),
            "ConnectionDetails": {
                "HostAddress": d["host"],
                "Password": "",
                "Port": d["port"] if d["port"] != 5900 else None,
            },
            "DisplayName": d["name"],
            "Settings": {
                "FileTransferRemotePath": "/",
                "FileTransferLocalPath": "/",
                "IsAutoScale": True,
                "IsOriginalScale": False,
                "LowQuality": False,
                "Scale": 100,
                "IgnoreInput": bool(d["view_only"]),
            },
        })

    output = json.dumps(
        {"Stations": stations_out, "Groups": groups_out},
        indent=2,
    )
    return StreamingResponse(
        iter([output]),
        media_type="application/json",
        headers={
            "Content-Disposition": "attachment; filename=MightyViewerStations.json"
        },
    )


@router.get("/api/export/devices")
async def export_devices():
    """Export plain device list (passwords excluded)."""
    devices = db.get_all_devices()
    for d in devices:
        d.pop("password", None)
    output = json.dumps(devices, indent=2, default=str)
    return StreamingResponse(
        iter([output]),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=vnc_devices.json"},
    )


# ═══════════════════════════════════════════════════════════════════════
#  SETTINGS
# ═══════════════════════════════════════════════════════════════════════

@router.get("/api/settings")
async def get_settings():
    return db.get_all_settings()


@router.put("/api/settings")
async def update_settings(body: SettingsUpdate):
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    # Convert booleans to strings for storage
    for k in ("dark_mode", "auto_reconnect"):
        if k in data:
            data[k] = str(data[k]).lower()
    db.update_settings(data)

    # Restart health checker if interval changed
    if "health_check_interval" in data:
        health_checker.stop()
        health_checker.start(int(data["health_check_interval"]))

    return db.get_all_settings()
