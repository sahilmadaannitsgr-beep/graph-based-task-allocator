import csv

from component_3 import app as receiver


def test_receiver_stores_one_csv_row_and_deduplicates_retry(monkeypatch, tmp_path):
    monkeypatch.setattr(receiver, "DATA_DIR", tmp_path)
    monkeypatch.setattr(receiver, "DB_PATH", tmp_path / "receiver.sqlite3")
    monkeypatch.setattr(receiver, "CSV_PATH", tmp_path / "vehicle_states.csv")
    receiver.init_store()

    state = receiver.VehicleState(
        vehicle_id="v1",
        window_start_timestamp="2026-01-01T00:00:00",
        window_duration_s=0,
        velocity_mps=None,
        direction_degrees=None,
        task_capable_bit_integer=3,
    )
    first = receiver.receive_vehicle_state(state, "same-key")
    retry = receiver.receive_vehicle_state(state, "same-key")

    assert first["status"] == "accepted"
    assert retry["status"] == "duplicate"
    with (tmp_path / "vehicle_states.csv").open(
        "r", newline="", encoding="utf-8"
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["vehicle_id"] == "v1"
    assert rows[0]["velocity_mps"] == "null"
    assert rows[0]["direction_degrees"] == "null"
    assert set(rows[0]) == set(receiver.FIELDS)
