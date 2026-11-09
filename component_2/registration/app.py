"""Component 2 vehicle/task registry API backed by the shared project database."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[1]
DB_PATH = Path(os.getenv("PROJECT_DB_PATH", PROJECT_ROOT / "shared" / "database" / "project.db"))
TOKENS_PATH = HERE / "data" / "vehicle_tokens.json"
ADMIN_TOKEN = os.getenv("COMPONENT2_ADMIN_TOKEN", "")

sys.path.insert(0, str(PROJECT_ROOT / "shared" / "database"))
from db_utils import initialize_database  # noqa: E402

initialize_database(DB_PATH)


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def token_map() -> dict:
    if not TOKENS_PATH.exists():
        return {}
    return json.loads(TOKENS_PATH.read_text(encoding="utf-8"))


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def require_admin(token: str | None):
    if not ADMIN_TOKEN:
        raise HTTPException(503, "Set COMPONENT2_ADMIN_TOKEN before using admin endpoints")
    if not token or not secrets.compare_digest(token, ADMIN_TOKEN):
        raise HTTPException(401, "Valid X-Admin-Token required")


def require_vehicle(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required")
    vehicle_id = token_map().get(hash_token(authorization[7:]))
    if not vehicle_id:
        raise HTTPException(403, "Invalid vehicle credential")
    return vehicle_id


def ensure_device(conn: sqlite3.Connection, name: str, description: str | None = None):
    name = name.strip().lower()
    if not name:
        raise ValueError("Device name cannot be empty")
    row = conn.execute(
        "SELECT device_bit_position,device_bit_integer FROM device_types WHERE device_name=?",
        (name,)
    ).fetchone()
    if row:
        return int(row["device_bit_position"]), int(row["device_bit_integer"])
    position = int(conn.execute(
        "SELECT COALESCE(MAX(device_bit_position),-1)+1 FROM device_types"
    ).fetchone()[0])
    bit_value = 1 << position
    conn.execute(
        """INSERT INTO device_types(device_bit_position,device_bit_integer,device_name,description)
           VALUES (?,?,?,?)""",
        (position, bit_value, name, description)
    )
    return position, bit_value


def ensure_task_bit(conn: sqlite3.Connection, task_id: str):
    row = conn.execute(
        "SELECT task_bit_position,task_bit_integer FROM tasks WHERE task_id=?",
        (task_id,)
    ).fetchone()
    if row:
        return int(row["task_bit_position"]), int(row["task_bit_integer"])
    position = int(conn.execute(
        "SELECT COALESCE(MAX(task_bit_position),-1)+1 FROM tasks"
    ).fetchone()[0])
    return position, 1 << position


class DeviceReport(BaseModel):
    type: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    description: str | None = None
    source: str = "configured"
    hardware_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class VehicleReport(BaseModel):
    vehicle_id: str = Field(min_length=1, max_length=128)
    agent_version: str = "1.0.0"
    reported_at: str
    client: dict[str, Any] = Field(default_factory=dict)
    devices: list[DeviceReport]


class DeviceTypeReport(BaseModel):
    device_name: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    description: str | None = None


class TaskReport(BaseModel):
    task_id: str = Field(min_length=1, max_length=128)
    required_devices: list[str]
    description: str | None = None


app = FastAPI(title="Component 2 — Shared Vehicle Capability Registry", version="2.0.0")


@app.get("/health")
def health():
    try:
        with db() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok", "service": "component2-registry", "database": str(DB_PATH)}
    except sqlite3.Error as exc:
        raise HTTPException(503, str(exc))


@app.post("/api/v1/vehicles/register")
def register_vehicle(report: VehicleReport, authorization: str | None = Header(default=None)):
    authorized_vehicle = require_vehicle(authorization)
    if report.vehicle_id != authorized_vehicle:
        raise HTTPException(403, "Credential is not assigned to this vehicle_id")
    with db() as conn:
        mask = 0
        normalized = {}
        for device in report.devices:
            name = device.type.strip().lower()
            if name in normalized:
                continue
            position, bit_value = ensure_device(conn, name, device.description)
            normalized[name] = (position, device)
            mask |= bit_value
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO vehicles(vehicle_id,devices_bit_integer,updated_at)
               VALUES (?,?,?) ON CONFLICT(vehicle_id) DO UPDATE SET
               devices_bit_integer=excluded.devices_bit_integer,
               updated_at=excluded.updated_at""",
            (report.vehicle_id, mask, now)
        )
        conn.execute("DELETE FROM vehicle_devices WHERE vehicle_id=?", (report.vehicle_id,))
        for position, device in normalized.values():
            conn.execute(
                """INSERT INTO vehicle_devices
                   (vehicle_id,device_bit_position,source,hardware_id,metadata_json)
                   VALUES (?,?,?,?,?)""",
                (report.vehicle_id, position, device.source, device.hardware_id,
                 json.dumps(device.metadata))
            )
    # Keep the response compatible with the included vehicle-side agent.
    return {
        "status": "registered",
        "vehicle_id": report.vehicle_id,
        "device_count": len(normalized),
        "capability_mask": mask,
        "capability_mask_binary": format(mask, "b"),
        "devices_bit_integer": mask,
        "devices_bit_binary": format(mask, "b")
    }


@app.post("/api/v1/devices")
def upsert_device(device: DeviceTypeReport, x_admin_token: str | None = Header(default=None)):
    require_admin(x_admin_token)
    with db() as conn:
        position, bit_value = ensure_device(conn, device.device_name, device.description)
        row = conn.execute(
            "SELECT * FROM device_types WHERE device_name=?",
            (device.device_name.strip().lower(),)
        ).fetchone()
    return {**dict(row), "device_bit_position": position, "device_bit_integer": bit_value}


@app.post("/api/v1/tasks")
def upsert_task(task: TaskReport, x_admin_token: str | None = Header(default=None)):
    require_admin(x_admin_token)
    names = sorted({name.strip().lower() for name in task.required_devices if name.strip()})
    if not names:
        raise HTTPException(422, "Tasks must require at least one device")
    with db() as conn:
        required_mask, positions = 0, []
        for name in names:
            position, bit_value = ensure_device(conn, name)
            required_mask |= bit_value
            positions.append(position)
        task_position, task_bit = ensure_task_bit(conn, task.task_id)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO tasks
               (task_bit_position,task_bit_integer,task_id,devices_need_bit_integer,description,updated_at)
               VALUES (?,?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET
               devices_need_bit_integer=excluded.devices_need_bit_integer,
               description=excluded.description,updated_at=excluded.updated_at""",
            (task_position, task_bit, task.task_id, required_mask, task.description, now)
        )
        conn.execute("DELETE FROM task_device_requirements WHERE task_id=?", (task.task_id,))
        for position in positions:
            conn.execute(
                "INSERT INTO task_device_requirements(task_id,device_bit_position) VALUES (?,?)",
                (task.task_id, position)
            )
    return {
        "task_id": task.task_id,
        "task_bit_position": task_position,
        "task_bit_integer": task_bit,
        "required_devices": names,
        "devices_need_bit_integer": required_mask,
        "task_capable_bit_integer": task_bit
    }


@app.get("/api/v1/devices")
def list_devices(x_admin_token: str | None = Header(default=None)):
    require_admin(x_admin_token)
    with db() as conn:
        rows = conn.execute("SELECT * FROM device_types ORDER BY device_bit_position").fetchall()
    return [dict(row) for row in rows]


@app.get("/api/v1/vehicles")
def list_vehicles(x_admin_token: str | None = Header(default=None)):
    require_admin(x_admin_token)
    with db() as conn:
        rows = conn.execute("SELECT * FROM vehicles ORDER BY vehicle_id").fetchall()
    return [dict(row) for row in rows]


@app.get("/api/v1/tasks")
def list_tasks(x_admin_token: str | None = Header(default=None)):
    require_admin(x_admin_token)
    with db() as conn:
        rows = conn.execute("SELECT * FROM tasks ORDER BY task_bit_position").fetchall()
    return [dict(row) for row in rows]


@app.get("/api/v1/compatibility")
def all_compatibility(x_admin_token: str | None = Header(default=None)):
    require_admin(x_admin_token)
    result = []
    with db() as conn:
        vehicles = conn.execute(
            "SELECT vehicle_id,devices_bit_integer FROM vehicles ORDER BY vehicle_id"
        ).fetchall()
        tasks = conn.execute(
            "SELECT task_id,task_bit_integer,devices_need_bit_integer FROM tasks "
            "WHERE active=1 ORDER BY task_bit_position"
        ).fetchall()
        for vehicle in vehicles:
            mask, task_mask, matches = int(vehicle["devices_bit_integer"]), 0, []
            for task in tasks:
                required = int(task["devices_need_bit_integer"])
                if (mask & required) == required:
                    matches.append(task["task_id"])
                    task_mask |= int(task["task_bit_integer"])
            result.append({
                "vehicle_id": vehicle["vehicle_id"],
                "devices_bit_integer": mask,
                "compatible_task_ids": matches,
                "task_capable_bit_integer": task_mask
            })
    return result


@app.get("/api/v1/vehicles/{vehicle_id}/compatible-tasks")
def vehicle_compatibility(vehicle_id: str, x_admin_token: str | None = Header(default=None)):
    require_admin(x_admin_token)
    with db() as conn:
        vehicle = conn.execute(
            "SELECT devices_bit_integer FROM vehicles WHERE vehicle_id=?",
            (vehicle_id,)
        ).fetchone()
        if vehicle is None:
            raise HTTPException(404, "Vehicle not registered")
        mask = int(vehicle["devices_bit_integer"])
        tasks = conn.execute(
            "SELECT task_id,task_bit_integer,devices_need_bit_integer FROM tasks "
            "WHERE active=1 ORDER BY task_bit_position"
        ).fetchall()
    matches, task_mask = [], 0
    for task in tasks:
        required = int(task["devices_need_bit_integer"])
        if (mask & required) == required:
            matches.append(task["task_id"])
            task_mask |= int(task["task_bit_integer"])
    return {
        "vehicle_id": vehicle_id,
        "compatible_task_ids": matches,
        "task_capable_bit_integer": task_mask
    }
