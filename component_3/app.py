"""Component 3 HTTP receiver for Component 2's six-field vehicle state."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data" / "received"
DB_PATH = DATA_DIR / "component3_receiver.sqlite3"
CSV_PATH = DATA_DIR / "vehicle_states.csv"
FIELDS = [
    "vehicle_id",
    "window_start_timestamp",
    "window_duration_s",
    "velocity_mps",
    "direction_degrees",
    "task_capable_bit_integer",
]
LOCK = RLock()


class VehicleState(BaseModel):
    vehicle_id: str = Field(min_length=1, max_length=128)
    window_start_timestamp: str = Field(min_length=1, max_length=80)
    window_duration_s: float = Field(ge=0)
    velocity_mps: Optional[float] = Field(default=None, ge=0)
    direction_degrees: Optional[float] = Field(default=None, ge=0, lt=360)
    task_capable_bit_integer: int = Field(ge=0)

    class Config:
        extra = "forbid"


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_store() -> None:
    with connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS received_vehicle_states (
                record_key TEXT PRIMARY KEY,
                vehicle_id TEXT NOT NULL,
                window_start_timestamp TEXT NOT NULL,
                window_duration_s REAL NOT NULL,
                velocity_mps REAL,
                direction_degrees REAL,
                task_capable_bit_integer INTEGER NOT NULL,
                received_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_component3_vehicle_window "
            "ON received_vehicle_states(vehicle_id, window_start_timestamp)"
        )
    export_csv()


def export_csv() -> None:
    """Rebuild the local CSV from the durable receiver store."""
    with LOCK:
        with connect() as conn:
            rows = conn.execute(
                """SELECT vehicle_id,window_start_timestamp,window_duration_s,
                          velocity_mps,direction_degrees,task_capable_bit_integer
                   FROM received_vehicle_states ORDER BY rowid"""
            ).fetchall()
        temporary = CSV_PATH.with_suffix(".csv.tmp")
        with temporary.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow({
                    field: ("null" if row[field] is None else row[field])
                    for field in FIELDS
                })
        temporary.replace(CSV_PATH)


def normalize_and_validate(record: dict) -> dict:
    if set(record) != set(FIELDS):
        raise ValueError(f"Expected exactly these fields: {FIELDS}")
    if not isinstance(record["vehicle_id"], str) or not record["vehicle_id"].strip():
        raise ValueError("vehicle_id must be a non-empty string")
    try:
        datetime.fromisoformat(record["window_start_timestamp"].replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError("window_start_timestamp must be an ISO-8601 timestamp") from exc

    for field in ("window_duration_s", "velocity_mps", "direction_degrees"):
        value = record[field]
        if value is not None and (isinstance(value, bool) or not math.isfinite(float(value))):
            raise ValueError(f"{field} must be a finite number or null")
    if float(record["window_duration_s"]) < 0:
        raise ValueError("window_duration_s cannot be negative")
    velocity = record["velocity_mps"]
    if velocity is not None and float(velocity) < 0:
        raise ValueError("velocity_mps cannot be negative")
    direction = record["direction_degrees"]
    if direction is not None and not 0 <= float(direction) < 360:
        raise ValueError("direction_degrees must be in [0, 360)")
    mask = record["task_capable_bit_integer"]
    if isinstance(mask, bool) or not isinstance(mask, int) or mask < 0:
        raise ValueError("task_capable_bit_integer must be a non-negative integer")
    return {
        "vehicle_id": record["vehicle_id"],
        "window_start_timestamp": record["window_start_timestamp"],
        "window_duration_s": float(record["window_duration_s"]),
        "velocity_mps": None if velocity is None else float(velocity),
        "direction_degrees": None if direction is None else float(direction),
        "task_capable_bit_integer": mask,
    }


def record_key(record: dict, supplied_key: str | None = None) -> str:
    if supplied_key:
        return supplied_key
    stable = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


app = FastAPI(title="Component 3 — Vehicle State Receiver", version="1.0.0")
init_store()


@app.get("/health")
def health():
    with connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM received_vehicle_states"
        ).fetchone()[0]
    return {
        "status": "ok",
        "service": "component3-vehicle-state-receiver",
        "received_records": int(count),
        "csv_path": str(CSV_PATH),
    }


@app.post("/api/v1/vehicle-states")
def receive_vehicle_state(
    state: VehicleState,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        record = normalize_and_validate(
            state.model_dump() if hasattr(state, "model_dump") else state.dict()
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    key = record_key(record, idempotency_key)
    with LOCK:
        with connect() as conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO received_vehicle_states
                   (record_key,vehicle_id,window_start_timestamp,window_duration_s,
                    velocity_mps,direction_degrees,task_capable_bit_integer)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    key, record["vehicle_id"], record["window_start_timestamp"],
                    record["window_duration_s"], record["velocity_mps"],
                    record["direction_degrees"], record["task_capable_bit_integer"]
                )
            )
            inserted = cursor.rowcount == 1
        if inserted:
            export_csv()
    return {
        "status": "accepted" if inserted else "duplicate",
        "vehicle_id": record["vehicle_id"],
        "idempotency_key": key,
    }


@app.get("/api/v1/vehicle-states")
def list_vehicle_states(limit: int = 100):
    if not 1 <= limit <= 1000:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 1000")
    with connect() as conn:
        rows = conn.execute(
            """SELECT vehicle_id,window_start_timestamp,window_duration_s,
                      velocity_mps,direction_degrees,task_capable_bit_integer
               FROM received_vehicle_states ORDER BY rowid DESC LIMIT ?""",
            (limit,)
        ).fetchall()
    return [dict(row) for row in rows]
