"""Pydantic models for request/response validation."""

from pydantic import BaseModel, Field
from typing import Optional, List


# ── Device Models ──────────────────────────────────────────────────────

class DeviceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    host: str = Field(..., min_length=1, max_length=255)
    port: int = Field(default=5900, ge=1, le=65535)
    password: str = Field(default="")
    group_name: str = Field(default="Ungrouped")
    group_color: str = Field(default="#4589ff")
    view_only: bool = Field(default=False)
    enabled: bool = Field(default=True)
    needs_password: bool = Field(default=False)


class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    password: Optional[str] = None
    group_name: Optional[str] = None
    group_color: Optional[str] = None
    view_only: Optional[bool] = None
    enabled: Optional[bool] = None
    needs_password: Optional[bool] = None
    sort_order: Optional[int] = None


class DeviceReorder(BaseModel):
    device_id: int
    sort_order: int
    group_name: Optional[str] = None


class DeviceReorderBatch(BaseModel):
    updates: List[DeviceReorder]


# ── Group Models ───────────────────────────────────────────────────────

class GroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    color: str = Field(default="#4589ff")


class GroupUpdate(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    sort_order: Optional[int] = None
    enabled: Optional[bool] = None


# ── Import / Export Models ─────────────────────────────────────────────

class ImportConfirm(BaseModel):
    devices: List[dict]
    overwrite_duplicates: bool = False
    preserve_view_only: bool = True


# ── Settings Model ─────────────────────────────────────────────────────

class SettingsUpdate(BaseModel):
    grid_columns: Optional[str] = None       # "auto", "2", "3", "4", "6"
    thumbnail_quality: Optional[str] = None   # "low", "medium", "high"
    dark_mode: Optional[bool] = None
    auto_reconnect: Optional[bool] = None
    health_check_interval: Optional[int] = None
    vnc_default_port: Optional[int] = None
    app_username: Optional[str] = None
    app_password: Optional[str] = None
