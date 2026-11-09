"""Per-vehicle sliding-window GPS features and task capability bitmask."""
from __future__ import annotations

import json
import math
import sqlite3
import threading
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EARTH_RADIUS_M = 6_371_008.8


def parse_timestamp(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def initial_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


class SharedCapabilityStore:
    """Reads vehicle and task bitmasks and persists the six-field output record."""
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def get_task_capable_bit_integer(self, vehicle_id: str) -> int:
        if not self.db_path.exists():
            return 0
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            row = conn.execute(
                "SELECT devices_bit_integer FROM vehicles WHERE vehicle_id=?",
                (vehicle_id,)
            ).fetchone()
            if row is None:
                return 0
            vehicle_mask = int(row[0])
            tasks = conn.execute(
                "SELECT task_bit_integer, devices_need_bit_integer FROM tasks WHERE active=1"
            ).fetchall()
            result = 0
            for task_bit, required_mask in tasks:
                required_mask = int(required_mask)
                if (vehicle_mask & required_mask) == required_mask:
                    result |= int(task_bit)
            return result
        finally:
            conn.close()

    def save_vehicle_state(self, state: dict) -> None:
        if not self.db_path.exists():
            return
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            conn.execute(
                """INSERT INTO vehicle_states
                   (vehicle_id, window_start_timestamp, window_duration_s,
                    velocity_mps, direction_degrees, task_capable_bit_integer)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    state["vehicle_id"], state["window_start_timestamp"],
                    state["window_duration_s"], state["velocity_mps"],
                    state["direction_degrees"], state["task_capable_bit_integer"]
                )
            )
            conn.commit()
        finally:
            conn.close()


class LiveGPSProcessor:
    """Maintains a time-based sliding window per vehicle and emits on cadence."""
    def __init__(self, settings: dict, capabilities: dict | None, output_path: Path,
                 logger, registry_path: Path | None = None, database_path: Path | None = None):
        self.window_s = float(settings.get("window_duration_s", 10.0))
        self.update_s = float(settings.get("update_interval_s", 2.0))
        self.max_gap_s = float(settings.get("max_fix_gap_s", 5.0))
        self.stationary_mps = float(settings.get("stationary_speed_threshold_mps", 0.5))
        if self.window_s <= 0 or self.update_s <= 0 or self.window_s <= self.update_s:
            raise ValueError("Require window_duration_s > update_interval_s > 0")
        self.history = defaultdict(deque)
        self.last_emit = {}
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.logger = logger
        self.database = SharedCapabilityStore(database_path) if database_path else None
        self.lock = threading.Lock()

    def process(self, record: dict[str, Any]) -> dict | None:
        # Component 1 sends device_id; Component 2 names the same identity vehicle_id.
        vehicle_id = record.get("vehicle_id", record.get("device_id"))
        if not isinstance(vehicle_id, str) or not vehicle_id.strip():
            raise ValueError("GPS record must contain a non-empty device_id")
        timestamp_text = str(record["timestamp"])
        timestamp = parse_timestamp(timestamp_text)
        latitude, longitude = float(record["latitude"]), float(record["longitude"])
        if not math.isfinite(latitude) or not -90 <= latitude <= 90:
            raise ValueError("Invalid latitude")
        if not math.isfinite(longitude) or not -180 <= longitude <= 180:
            raise ValueError("Invalid longitude")
        fix = {
            "timestamp": timestamp, "timestamp_text": timestamp_text,
            "latitude": latitude, "longitude": longitude
        }

        with self.lock:
            points = self.history[vehicle_id]
            if points and timestamp <= points[-1]["timestamp"]:
                self.logger.warning("Ignoring out-of-order/duplicate fix for %s", vehicle_id)
                return None
            points.append(fix)
            while len(points) > 1 and (timestamp - points[0]["timestamp"]).total_seconds() > self.window_s:
                points.popleft()
            previous_emit = self.last_emit.get(vehicle_id)
            if previous_emit is not None and (timestamp - previous_emit).total_seconds() < self.update_s:
                return None
            self.last_emit[vehicle_id] = timestamp
            snapshot = list(points)

        state = self._build_state(vehicle_id, snapshot)
        self._write_state(state)
        return state

    def _build_state(self, vehicle_id: str, points: list[dict]) -> dict:
        task_mask = self.database.get_task_capable_bit_integer(vehicle_id) if self.database else 0
        elapsed = max(
            0.0,
            (points[-1]["timestamp"] - points[0]["timestamp"]).total_seconds()
        )
        # First observation is emitted for the vehicle, but motion is unknown:
        # both velocity and direction remain null until a second usable fix exists.
        state = {
            "vehicle_id": vehicle_id,
            "window_start_timestamp": points[0]["timestamp_text"],
            "window_duration_s": elapsed,
            "velocity_mps": None,
            "direction_degrees": None,
            "task_capable_bit_integer": int(task_mask)
        }
        if len(points) < 2:
            return state

        gaps = [
            (points[i]["timestamp"] - points[i - 1]["timestamp"]).total_seconds()
            for i in range(1, len(points))
        ]
        if any(gap <= 0 or gap > self.max_gap_s for gap in gaps) or elapsed <= 0:
            return state

        first, last = points[0], points[-1]
        displacement = haversine_m(
            first["latitude"], first["longitude"], last["latitude"], last["longitude"]
        )
        path_distance = sum(
            haversine_m(a["latitude"], a["longitude"], b["latitude"], b["longitude"])
            for a, b in zip(points, points[1:])
        )
        velocity = displacement / elapsed
        path_speed = path_distance / elapsed
        bearing = initial_bearing_deg(
            first["latitude"], first["longitude"], last["latitude"], last["longitude"]
        )
        state["velocity_mps"] = round(velocity, 3)
        if displacement >= 0.5 and path_speed >= self.stationary_mps:
            state["direction_degrees"] = round(bearing, 2)
        return state

    def _write_state(self, state: dict) -> None:
        with self.output_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(state, separators=(",", ":")) + "\n")
        if self.database:
            self.database.save_vehicle_state(state)


class ProcessingWorker(threading.Thread):
    def __init__(self, records, processor: LiveGPSProcessor, logger):
        super().__init__(name="gps-preprocessor", daemon=True)
        self.records, self.processor, self.logger = records, processor, logger
        self.stop_event = threading.Event()

    def run(self):
        while not self.stop_event.is_set() or not self.records.empty():
            try:
                record = self.records.get(timeout=0.25)
            except Exception:
                continue
            try:
                state = self.processor.process(record)
                if state is not None:
                    self.logger.info(
                        "Processed %s | window_start=%s | duration=%ss | velocity=%s m/s | direction=%s deg | task_mask=%s",
                        state["vehicle_id"], state["window_start_timestamp"],
                        state["window_duration_s"], state["velocity_mps"],
                        state["direction_degrees"], state["task_capable_bit_integer"]
                    )
            except Exception:
                self.logger.exception(
                    "GPS preprocessing failed for %s",
                    record.get("device_id", record.get("vehicle_id"))
                )
            finally:
                self.records.task_done()

    def stop(self):
        self.stop_event.set()
