import json
import logging

import pytest

from processing.sender import DurableComponent3Sender, STATE_FIELDS


def test_sender_persists_exact_six_field_payload_to_outbox(tmp_path):
    spool = tmp_path / "component3_outbox.jsonl"
    sender = DurableComponent3Sender(
        "http://127.0.0.1:8002/api/v1/vehicle-states",
        spool, logging.getLogger("sender-test")
    )
    state = {
        "vehicle_id": "v1",
        "window_start_timestamp": "2026-01-01T00:00:00",
        "window_duration_s": 0.0,
        "velocity_mps": None,
        "direction_degrees": None,
        "task_capable_bit_integer": 3,
    }
    sender.submit(state)
    saved = json.loads(spool.read_text(encoding="utf-8").splitlines()[0])
    assert set(saved) == STATE_FIELDS
    assert saved == state


def test_sender_rejects_extra_fields(tmp_path):
    sender = DurableComponent3Sender(
        "http://127.0.0.1:8002/api/v1/vehicle-states",
        tmp_path / "outbox.jsonl", logging.getLogger("sender-test")
    )
    with pytest.raises(ValueError):
        sender.submit({"vehicle_id": "v1", "unexpected": True})
