import logging
from pathlib import Path

from processing.engine import LiveGPSProcessor, compatible_tasks

def test_subset_mask_matching():
    assert compatible_tasks(0b1011, {"a": 0b0011, "b": 0b1100, "c": 0}) == ["a", "c"]

def test_live_motion_and_task_compatibility(tmp_path):
    processor = LiveGPSProcessor(
        {"window_duration_s": 10, "update_interval_s": 1, "max_fix_gap_s": 5},
        {
            "vehicle_capabilities": {"v1": 0b1011},
            "task_requirements": {"task_a": 0b0011, "task_b": 0b1100}
        },
        tmp_path / "states.jsonl",
        logging.getLogger("test")
    )
    assert processor.process({
        "device_id": "v1", "timestamp": "2026-01-01T07:00:00",
        "latitude": 44.494891, "longitude": 11.397630
    })["motion_quality"] == "insufficient_fixes"
    state = processor.process({
        "device_id": "v1", "timestamp": "2026-01-01T07:00:02",
        "latitude": 44.494991, "longitude": 11.397630
    })
    assert state["compatible_tasks"] == ["task_a"]
    assert state["motion_available"] is True
    assert state["velocity_mps"] > 0
    assert state["direction"] in {"N", "NE", "E", "SE", "S", "SW", "W", "NW"}
    assert (tmp_path / "states.jsonl").exists()

def test_gap_disables_motion_but_keeps_capability_result(tmp_path):
    processor = LiveGPSProcessor(
        {"window_duration_s": 10, "update_interval_s": 1, "max_fix_gap_s": 3},
        {"vehicle_capabilities": {"v1": 3}, "task_requirements": {"t": 1}},
        tmp_path / "states.jsonl",
        logging.getLogger("test")
    )
    processor.process({"device_id":"v1","timestamp":"2026-01-01T00:00:00","latitude":1.0,"longitude":1.0})
    state = processor.process({"device_id":"v1","timestamp":"2026-01-01T00:00:05","latitude":1.1,"longitude":1.1})
    assert state["motion_quality"] == "gap_exceeds_limit"
    assert state["compatible_tasks"] == ["t"]


def test_live_registry_refresh_updates_vehicle_and_task_masks(tmp_path):
    import json
    import logging
    from processing.engine import LiveGPSProcessor

    registry = tmp_path / "vehicle_registry.json"
    registry.write_text(json.dumps({
        "iot_device_types": {},
        "vehicles": {"v1": {"capability_mask": 3}},
        "tasks": {"inspection": {"required_mask": 3}}
    }), encoding="utf-8")
    processor = LiveGPSProcessor(
        {"window_duration_s": 10, "update_interval_s": 1, "max_fix_gap_s": 5},
        {"vehicle_capabilities": {}, "task_requirements": {}},
        tmp_path / "states.jsonl", logging.getLogger("registry-test"),
        registry_path=registry
    )
    state = processor.process({
        "device_id": "v1", "timestamp": "2026-01-01T00:00:00",
        "latitude": 44.0, "longitude": 11.0
    })
    assert state["capability_known"] is True
    assert state["compatible_tasks"] == ["inspection"]

    registry.write_text(json.dumps({
        "iot_device_types": {},
        "vehicles": {"v1": {"capability_mask": 1}},
        "tasks": {"inspection": {"required_mask": 3}}
    }), encoding="utf-8")
    # Force a detectable mtime difference on filesystems with coarse timestamp resolution.
    import os, time
    os.utime(registry, ns=(registry.stat().st_atime_ns, registry.stat().st_mtime_ns + 2_000_000))
    state = processor.process({
        "device_id": "v1", "timestamp": "2026-01-01T00:00:02",
        "latitude": 44.00001, "longitude": 11.0
    })
    assert state["compatible_tasks"] == []
