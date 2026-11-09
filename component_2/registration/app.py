"""Component 2 vehicle registration and task-capability API."""
from __future__ import annotations
import hashlib, json, os, secrets
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
REGISTRY_PATH = DATA / "vehicle_registry.json"
TOKENS_PATH = DATA / "vehicle_tokens.json"
LOCK = RLock()
ADMIN_TOKEN = os.getenv("COMPONENT2_ADMIN_TOKEN", "CHANGE_THIS_ADMIN_TOKEN")

def _read(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))

DB = _read(REGISTRY_PATH, {"iot_device_types": {}, "vehicles": {}, "tasks": {}})
TOKENS = _read(TOKENS_PATH, {})

def save():
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(DB, indent=2) + "\n", encoding="utf-8")
    tmp.replace(REGISTRY_PATH)

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def vehicle_for_token(token: str) -> str | None:
    return TOKENS.get(hash_token(token))

def require_vehicle(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required")
    vehicle_id = vehicle_for_token(authorization[7:])
    if not vehicle_id:
        raise HTTPException(403, "Invalid vehicle credential")
    return vehicle_id

def require_admin(token: str | None):
    if not token or not secrets.compare_digest(token, ADMIN_TOKEN):
        raise HTTPException(401, "Valid X-Admin-Token required")

def ensure_device_type(name: str) -> int:
    key = name.strip().lower()
    if not key:
        raise ValueError("Device type cannot be empty")
    if key not in DB["iot_device_types"]:
        used = {int(v["bit"]) for v in DB["iot_device_types"].values()}
        position = 0
        while (1 << position) in used:
            position += 1
        DB["iot_device_types"][key] = {"bit": 1 << position, "active": True}
    return int(DB["iot_device_types"][key]["bit"])

class DeviceReport(BaseModel):
    type: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    source: str = "configured"
    hardware_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

class VehicleReport(BaseModel):
    vehicle_id: str = Field(min_length=1, max_length=128)
    agent_version: str = "1.0.0"
    reported_at: str
    client: dict[str, Any] = Field(default_factory=dict)
    devices: list[DeviceReport]

class TaskReport(BaseModel):
    task_id: str = Field(min_length=1, max_length=128)
    required_devices: list[str]

app = FastAPI(title="Component 2 — Vehicle Capability Registry", version="1.0.0")

@app.get("/health")
def health():
    return {"status": "ok", "service": "component2-registration"}

@app.post("/api/v1/vehicles/register")
def register_vehicle(report: VehicleReport, authorization: str | None = Header(default=None)):
    authorized_vehicle = require_vehicle(authorization)
    if report.vehicle_id != authorized_vehicle:
        raise HTTPException(403, "Credential is not assigned to this vehicle_id")
    with LOCK:
        inventory, mask = [], 0
        # Deduplicate by device type; a type contributes its bit only once.
        seen = set()
        for device in report.devices:
            name = device.type.strip().lower()
            if name in seen:
                continue
            seen.add(name)
            bit = ensure_device_type(name)
            mask |= bit
            inventory.append({
                "type": name, "bit": bit, "source": device.source,
                "hardware_id": device.hardware_id, "metadata": device.metadata
            })
        now = datetime.now(timezone.utc).isoformat()
        DB["vehicles"][report.vehicle_id] = {
            "vehicle_id": report.vehicle_id,
            "devices": inventory,
            "capability_mask": mask,
            "capability_mask_binary": format(mask, "b"),
            "agent_version": report.agent_version,
            "client": report.client,
            "reported_at": report.reported_at,
            "server_updated_at": now,
            "last_seen_at": now,
            "status": "online"
        }
        save()
    return {
        "status": "registered", "vehicle_id": report.vehicle_id,
        "device_count": len(inventory), "capability_mask": mask,
        "capability_mask_binary": format(mask, "b")
    }

@app.post("/api/v1/tasks")
def upsert_task(task: TaskReport, x_admin_token: str | None = Header(default=None)):
    require_admin(x_admin_token)
    with LOCK:
        names = sorted({n.strip().lower() for n in task.required_devices})
        mask = 0
        for name in names:
            mask |= ensure_device_type(name)
        DB["tasks"][task.task_id] = {
            "task_id": task.task_id, "required_devices": names,
            "required_mask": mask, "required_mask_binary": format(mask, "b")
        }
        save()
        return DB["tasks"][task.task_id]

@app.get("/api/v1/vehicles")
def list_vehicles(x_admin_token: str | None = Header(default=None)):
    require_admin(x_admin_token)
    return list(DB["vehicles"].values())

@app.get("/api/v1/tasks")
def list_tasks(x_admin_token: str | None = Header(default=None)):
    require_admin(x_admin_token)
    return list(DB["tasks"].values())

@app.get("/api/v1/compatibility")
def all_compatibility(x_admin_token: str | None = Header(default=None)):
    require_admin(x_admin_token)
    return [
        {"vehicle_id": vid, "capability_mask": int(v["capability_mask"]),
         "compatible_tasks": sorted(
             tid for tid, task in DB["tasks"].items()
             if (int(v["capability_mask"]) & int(task["required_mask"])) == int(task["required_mask"])
         )}
        for vid, v in sorted(DB["vehicles"].items())
    ]

@app.get("/api/v1/vehicles/{vehicle_id}/compatible-tasks")
def vehicle_compatibility(vehicle_id: str, x_admin_token: str | None = Header(default=None)):
    require_admin(x_admin_token)
    if vehicle_id not in DB["vehicles"]:
        raise HTTPException(404, "Vehicle not registered")
    vmask = int(DB["vehicles"][vehicle_id]["capability_mask"])
    return {"vehicle_id": vehicle_id, "compatible_tasks": sorted(
        tid for tid, task in DB["tasks"].items()
        if (vmask & int(task["required_mask"])) == int(task["required_mask"])
    )}
