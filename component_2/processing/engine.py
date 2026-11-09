"""Live per-vehicle GPS mobility and IoT-task compatibility processing."""
from __future__ import annotations

import json
import math
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any

EARTH_RADIUS_M = 6_371_008.8

def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))

def initial_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0

def direction_label(bearing: float) -> str:
    names = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
    return names[int((bearing + 22.5) // 45) % 8]

def compatible_tasks(vehicle_mask: int, requirements: dict[str, int]) -> list[str]:
    if isinstance(vehicle_mask, bool) or not isinstance(vehicle_mask, int) or vehicle_mask < 0:
        raise ValueError("vehicle capability mask must be a non-negative integer")
    matched = []
    for task_id, required in requirements.items():
        if isinstance(required, bool) or not isinstance(required, int) or required < 0:
            raise ValueError(f"task mask for {task_id} must be a non-negative integer")
        if (vehicle_mask & required) == required:
            matched.append(task_id)
    return sorted(matched)

class LiveGPSProcessor:
    """Consumes validated GPS records and emits one state per vehicle on cadence."""
    def __init__(self, settings: dict, capabilities: dict, output_path: Path, logger):
        self.window_s = float(settings.get("window_duration_s", 10.0))
        self.update_s = float(settings.get("update_interval_s", 2.0))
        self.max_gap_s = float(settings.get("max_fix_gap_s", 5.0))
        self.stationary_mps = float(settings.get("stationary_speed_threshold_mps", 0.5))
        if self.window_s <= 0 or self.update_s <= 0 or self.window_s <= self.update_s:
            raise ValueError("Require window_duration_s > update_interval_s > 0")
        self.vehicle_masks = capabilities.get("vehicle_capabilities", {})
        self.task_requirements = capabilities.get("task_requirements", {})
        self.history = defaultdict(deque)
        self.last_emit = {}
        self.output_path = output_path
        self.logger = logger
        self.lock = threading.Lock()
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def process(self, record: dict[str, Any]) -> dict | None:
        device_id = record["device_id"]
        timestamp = parse_timestamp(record["timestamp"])
        fix = {
            "timestamp": timestamp,
            "timestamp_text": record["timestamp"],
            "latitude": float(record["latitude"]),
            "longitude": float(record["longitude"]),
        }
        with self.lock:
            points = self.history[device_id]
            if points and timestamp <= points[-1]["timestamp"]:
                self.logger.warning("Ignoring out-of-order/duplicate fix for %s", device_id)
                return None
            points.append(fix)
            cutoff = timestamp.timestamp() - self.window_s if timestamp.tzinfo else None
            while len(points) > 2:
                age = (timestamp - points[0]["timestamp"]).total_seconds()
                if age <= self.window_s:
                    break
                points.popleft()

            previous_emit = self.last_emit.get(device_id)
            elapsed_since_emit = (
                (timestamp - previous_emit).total_seconds() if previous_emit else None
            )
            if previous_emit is not None and elapsed_since_emit < self.update_s:
                return None
            self.last_emit[device_id] = timestamp
            snapshot = list(points)

        state = self._build_state(device_id, snapshot)
        self._write_state(state)
        return state

    def _build_state(self, device_id: str, points: list[dict]) -> dict:
        vehicle_mask = self.vehicle_masks.get(device_id)
        capability_known = vehicle_mask is not None
        tasks = compatible_tasks(int(vehicle_mask), self.task_requirements) if capability_known else []
        state = {
            "device_id": device_id,
            "observed_at": points[-1]["timestamp_text"],
            "observation_count": len(points),
            "window_duration_s": self.window_s,
            "capability_known": capability_known,
            "vehicle_iot_mask": int(vehicle_mask) if capability_known else None,
            "compatible_tasks": tasks,
            "motion_available": False,
            "velocity_mps": None,
            "speed_mps": None,
            "direction": None,
            "bearing_deg": None,
            "displacement_m": None,
            "motion_quality": "insufficient_fixes",
        }
        if len(points) < 2:
            return state

        gaps = [
            (points[i]["timestamp"] - points[i - 1]["timestamp"]).total_seconds()
            for i in range(1, len(points))
        ]
        if any(gap <= 0 or gap > self.max_gap_s for gap in gaps):
            state["motion_quality"] = "gap_exceeds_limit"
            return state

        elapsed = (points[-1]["timestamp"] - points[0]["timestamp"]).total_seconds()
        if elapsed <= 0:
            state["motion_quality"] = "invalid_elapsed_time"
            return state

        first, last = points[0], points[-1]
        displacement = haversine_m(
            first["latitude"], first["longitude"], last["latitude"], last["longitude"]
        )
        path_distance = sum(
            haversine_m(a["latitude"], a["longitude"], b["latitude"], b["longitude"])
            for a, b in zip(points, points[1:])
        )
        bearing = initial_bearing_deg(
            first["latitude"], first["longitude"], last["latitude"], last["longitude"]
        )
        velocity = displacement / elapsed
        speed = path_distance / elapsed
        state.update({
            "motion_available": True,
            "velocity_mps": round(velocity, 3),
            "speed_mps": round(speed, 3),
            "direction": direction_label(bearing) if displacement >= 0.5 else "STATIONARY",
            "bearing_deg": round(bearing, 1) if displacement >= 0.5 else None,
            "displacement_m": round(displacement, 2),
            "motion_quality": "ok",
        })
        if speed < self.stationary_mps:
            state["direction"] = "STATIONARY"
            state["bearing_deg"] = None
        return state

    def _write_state(self, state: dict):
        with self.output_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(state, separators=(",", ":")) + "\\n")

class ProcessingWorker(threading.Thread):
    def __init__(self, records, processor: LiveGPSProcessor, logger):
        super().__init__(name="gps-preprocessor", daemon=True)
        self.records = records
        self.processor = processor
        self.logger = logger
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
                        "Processed %s | tasks=%s | direction=%s | velocity=%s m/s | quality=%s",
                        state["device_id"], state["compatible_tasks"], state["direction"],
                        state["velocity_mps"], state["motion_quality"]
                    )
            except Exception:
                self.logger.exception("GPS preprocessing failed for %s", record.get("device_id"))
            finally:
                self.records.task_done()

    def stop(self):
        self.stop_event.set()
