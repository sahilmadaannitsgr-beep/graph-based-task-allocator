import json
import logging
import sqlite3
from pathlib import Path

from processing.engine import LiveGPSProcessor

SCHEMA = Path(__file__).resolve().parents[2] / "shared" / "schema" / "database_schema.sql"


def make_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    conn.execute("INSERT INTO device_types VALUES (0,1,'camera','Camera',1,'now')")
    conn.execute("INSERT INTO device_types VALUES (1,2,'gps','GPS',1,'now')")
    conn.execute("INSERT INTO vehicles(vehicle_id,devices_bit_integer) VALUES ('v1',3)")
    conn.execute(
        "INSERT INTO tasks(task_bit_position,task_bit_integer,task_id,devices_need_bit_integer,description) "
        "VALUES (0,1,'task_camera',1,'camera')"
    )
    conn.execute(
        "INSERT INTO tasks(task_bit_position,task_bit_integer,task_id,devices_need_bit_integer,description) "
        "VALUES (1,2,'task_gps',2,'gps')"
    )
    conn.execute(
        "INSERT INTO tasks(task_bit_position,task_bit_integer,task_id,devices_need_bit_integer,description) "
        "VALUES (2,4,'task_both',3,'both')"
    )
    conn.commit()
    conn.close()


def test_sliding_window_outputs_six_fields_and_first_fix_is_null(tmp_path):
    db_path = tmp_path / "project.db"
    make_db(db_path)
    output = tmp_path / "states.jsonl"
    processor = LiveGPSProcessor(
        {"window_duration_s": 10, "update_interval_s": 2, "max_fix_gap_s": 5,
         "stationary_speed_threshold_mps": 0.5},
        {}, output, logging.getLogger("test"), database_path=db_path
    )
    first = processor.process({
        "device_id": "v1", "timestamp": "2026-01-01T07:00:00",
        "latitude": 44.0, "longitude": 11.0
    })
    assert first["velocity_mps"] is None
    assert first["direction_degrees"] is None

    second = processor.process({
        "device_id": "v1", "timestamp": "2026-01-01T07:00:02",
        "latitude": 44.0001, "longitude": 11.0
    })
    expected = {
        "vehicle_id", "window_start_timestamp", "window_duration_s",
        "velocity_mps", "direction_degrees", "task_capable_bit_integer"
    }
    assert set(second) == expected
    assert second["vehicle_id"] == "v1"
    assert second["velocity_mps"] > 0
    assert second["direction_degrees"] is not None
    assert second["task_capable_bit_integer"] == 7
    assert second["window_start_timestamp"] == "2026-01-01T07:00:00"
    assert second["window_duration_s"] == 2

    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert set(json.loads(lines[-1])) == expected
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT window_start_timestamp,window_duration_s,velocity_mps,direction_degrees,"
        "task_capable_bit_integer FROM vehicle_states ORDER BY state_id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row[0] == "2026-01-01T07:00:00"
    assert row[1] == 2.0
    assert row[2] > 0
    assert row[3] is not None
    assert row[4] == 7


def test_window_cadence_and_gap_invalidation(tmp_path):
    db_path = tmp_path / "project.db"
    make_db(db_path)
    processor = LiveGPSProcessor(
        {"window_duration_s": 10, "update_interval_s": 2, "max_fix_gap_s": 3},
        {}, tmp_path / "states.jsonl", logging.getLogger("test"), database_path=db_path
    )
    processor.process({
        "device_id": "v1", "timestamp": "2026-01-01T00:00:00",
        "latitude": 1.0, "longitude": 1.0
    })
    assert processor.process({
        "device_id": "v1", "timestamp": "2026-01-01T00:00:01",
        "latitude": 1.00001, "longitude": 1.0
    }) is None
    state = processor.process({
        "device_id": "v1", "timestamp": "2026-01-01T00:00:05",
        "latitude": 1.00002, "longitude": 1.0
    })
    assert state["velocity_mps"] is None
    assert state["direction_degrees"] is None


def test_unknown_vehicle_has_no_invented_capabilities(tmp_path):
    db_path = tmp_path / "project.db"
    make_db(db_path)
    processor = LiveGPSProcessor(
        {"window_duration_s": 10, "update_interval_s": 2, "max_fix_gap_s": 5},
        {}, tmp_path / "states.jsonl", logging.getLogger("test"), database_path=db_path
    )
    state = processor.process({
        "device_id": "unregistered", "timestamp": "2026-01-01T00:00:00",
        "latitude": 1.0, "longitude": 1.0
    })
    assert state["task_capable_bit_integer"] == 0
    assert set(state) == {
        "vehicle_id", "window_start_timestamp", "window_duration_s",
        "velocity_mps", "direction_degrees", "task_capable_bit_integer"
    }



def test_two_second_stride_keeps_ten_second_sliding_window_and_writes_csv(tmp_path):
    import csv

    db_path = tmp_path / "project.db"
    make_db(db_path)
    jsonl_path = tmp_path / "states.jsonl"
    csv_path = tmp_path / "vehicle_states.csv"
    processor = LiveGPSProcessor(
        {"window_duration_s": 10, "stride_s": 2, "max_fix_gap_s": 5,
         "stationary_speed_threshold_mps": 0.5},
        {}, jsonl_path, logging.getLogger("stride-test"),
        database_path=db_path, csv_output_path=csv_path
    )

    emitted = []
    for second in range(13):
        state = processor.process({
            "device_id": "v1",
            "timestamp": f"2026-01-01T00:00:{second:02d}",
            "latitude": 1.0 + second * 0.00001,
            "longitude": 1.0
        })
        if state is not None:
            emitted.append(state)

    # t=0 is the immediate first-fix record. Then states emit every 2s.
    assert len(emitted) == 7
    assert [item["window_duration_s"] for item in emitted] == [
        0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 10.0
    ]
    assert [item["window_start_timestamp"][-2:] for item in emitted] == [
        "00", "00", "00", "00", "00", "00", "02"
    ]

    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 7
    assert list(rows[0]) == [
        "vehicle_id", "window_start_timestamp", "window_duration_s",
        "velocity_mps", "direction_degrees", "task_capable_bit_integer"
    ]
    assert rows[0]["velocity_mps"] == "null"
    assert rows[0]["direction_degrees"] == "null"
    assert rows[-1]["window_duration_s"] == "10.0"
