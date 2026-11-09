import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_seed_script_creates_stable_zero_based_bit_mappings(tmp_path):
    db = tmp_path / "project.db"
    subprocess.run(
        [sys.executable, str(ROOT / "shared/database/seed_database.py"),
         "--db", str(db), "--no-import-existing-vehicles"],
        check=True, capture_output=True, text=True
    )
    conn = sqlite3.connect(db)
    devices = conn.execute(
        "SELECT device_bit_position,device_name,device_bit_integer "
        "FROM device_types ORDER BY device_bit_position"
    ).fetchall()
    tasks = conn.execute(
        "SELECT task_bit_position,task_id,task_bit_integer "
        "FROM tasks ORDER BY task_bit_position"
    ).fetchall()
    conn.close()
    assert devices[:2] == [(0, "camera", 1), (1, "gps", 2)]
    assert tasks == [
        (0, "gps_tracking", 1),
        (1, "visual_inspection", 2),
        (2, "traffic_observation", 4)
    ]
