import asyncio
from fastapi import APIRouter, HTTPException, WebSocket

from app import database as db
from app.services.proxy_manager import proxy_manager

router = APIRouter()

@router.websocket("/api/ws/{device_id}")
async def vnc_websocket_proxy(websocket: WebSocket, device_id: int):
    """
    Direct WebSocket -> TCP proxy via FastAPI.
    This routes VNC traffic over the same port as the UI, solving all 
    firewall, reverse proxy, and HTTPS mixed-content issues.
    """
    device = db.get_device(device_id)
    if not device:
        await websocket.close()
        return

    # Accept the subprotocol requested by noVNC (usually 'binary')
    await websocket.accept(subprotocol="binary")
    
    try:
        reader, writer = await asyncio.open_connection(device["host"], device["port"])
    except Exception as e:
        import logging
        logging.error("FastAPI WS proxy: Failed to connect to VNC target %s:%s - %s", device["host"], device["port"], e)
        await websocket.close()
        return

    async def ws_to_tcp():
        try:
            while True:
                data = await websocket.receive_bytes()
                writer.write(data)
                await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()

    async def tcp_to_ws():
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                await websocket.send_bytes(data)
        except Exception:
            pass
        finally:
            try:
                await websocket.close()
            except:
                pass

    await asyncio.gather(ws_to_tcp(), tcp_to_ws())


# Keep old REST routes for backward compatibility temporarily, 
# though they are mostly no-ops now since the frontend will use the route above.
@router.post("/api/proxy/{device_id}/start")
async def start_proxy(device_id: int):
    return {"device_id": device_id, "ws_port": 0, "status": "running"}

@router.post("/api/proxy/{device_id}/stop")
async def stop_proxy(device_id: int):
    return {"device_id": device_id, "status": "stopped"}

@router.post("/api/proxy/start-all")
async def start_all_proxies():
    return {"status": "started", "count": 0}

@router.post("/api/proxy/stop-all")
async def stop_all_proxies():
    return {"status": "all_stopped"}

@router.post("/api/proxy/{device_id}/restart")
async def restart_proxy(device_id: int):
    return {"device_id": device_id, "ws_port": 0, "status": "running"}

@router.get("/api/proxy/status")
async def proxy_status():
    return {}
