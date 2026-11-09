"""Initialize and seed the shared project database.

Stable zero-based device positions:
camera=0, gps=1, temperature_sensor=2, rfid=3, pollution_sensor=4.
Stable task positions: gps_tracking=0, visual_inspection=1,
traffic_observation=2.

Existing Component 2 JSON vehicle registry rows are imported as a one-time
migration source. The shared SQLite database is authoritative afterwards.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
from db_utils import connect, initialize_database, DEFAULT_DB_PATH

DEVICES = [
    (0, "camera", "Vehicle camera"),
    (1, "gps", "GPS receiver"),
    (2, "temperature_sensor", "Temperature sensor"),
    (3, "rfid", "RFID reader"),
    (4, "pollution_sensor", "Pollution sensor"),
]
TASKS = [
    (0, "gps_tracking", ["gps"], "GPS-based vehicle tracking"),
    (1, "visual_inspection", ["camera"], "Visual inspection using a camera"),
    (2, "traffic_observation", ["camera", "gps"], "Traffic observation using camera and GPS"),
]


def bit(position: int) -> int:
    return 1 << position


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--no-import-existing-vehicles", action="store_true")
    args = parser.parse_args()

    db_path = initialize_database(args.db)
    conn = connect(db_path)
    try:
        # Insert-only: existing bit assignments are never overwritten or shifted.
        for position, name, description in DEVICES:
            conn.execute(
                """INSERT OR IGNORE INTO device_types
                   (device_bit_position,device_bit_integer,device_name,description)
                   VALUES (?,?,?,?)""",
                (position, bit(position), name, description)
            )

        for task_position, task_id, required_devices, description in TASKS:
            required_mask, positions = 0, []
            for name in required_devices:
                row = conn.execute(
                    "SELECT device_bit_position,device_bit_integer FROM device_types "
                    "WHERE device_name=?", (name,)
                ).fetchone()
                if row is None:
                    raise RuntimeError(f"Unknown required device: {name}")
                required_mask |= int(row["device_bit_integer"])
                positions.append(int(row["device_bit_position"]))
            conn.execute(
                """INSERT OR IGNORE INTO tasks
                   (task_bit_position,task_bit_integer,task_id,devices_need_bit_integer,description)
                   VALUES (?,?,?,?,?)""",
                (task_position, bit(task_position), task_id, required_mask, description)
            )
            for position in positions:
                conn.execute(
                    "INSERT OR IGNORE INTO task_device_requirements"
                    "(task_id,device_bit_position) VALUES (?,?)",
                    (task_id, position)
                )

        legacy = PROJECT_ROOT / "component_2" / "registration" / "data" / "vehicle_registry.json"
        if not args.no_import_existing_vehicles and legacy.exists():
            data = json.loads(legacy.read_text(encoding="utf-8"))
            for vehicle_id, vehicle in data.get("vehicles", {}).items():
                device_rows, mask = [], 0
                for item in vehicle.get("devices", []):
                    name = str(item.get("type", "")).strip().lower()
                    if not name:
                        continue
                    row = conn.execute(
                        "SELECT device_bit_position,device_bit_integer FROM device_types "
                        "WHERE device_name=?", (name,)
                    ).fetchone()
                    if row is None:
                        next_position = conn.execute(
                            "SELECT COALESCE(MAX(device_bit_position),-1)+1 FROM device_types"
                        ).fetchone()[0]
                        conn.execute(
                            """INSERT INTO device_types
                               (device_bit_position,device_bit_integer,device_name,description)
                               VALUES (?,?,?,?)""",
                            (next_position, bit(next_position), name,
                             f"Imported device type: {name}")
                        )
                        row = conn.execute(
                            "SELECT device_bit_position,device_bit_integer FROM device_types "
                            "WHERE device_name=?", (name,)
                        ).fetchone()
                    mask |= int(row["device_bit_integer"])
                    device_rows.append((int(row["device_bit_position"]), item))
                mask = int(vehicle.get("capability_mask", mask))
                conn.execute(
                    """INSERT INTO vehicles(vehicle_id,devices_bit_integer,description)
                       VALUES (?,?,?) ON CONFLICT(vehicle_id) DO UPDATE SET
                       devices_bit_integer=excluded.devices_bit_integer,
                       updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
                    (vehicle_id, mask, "Imported from previous Component 2 vehicle registry")
                )
                for position, item in device_rows:
                    conn.execute(
                        """INSERT OR IGNORE INTO vehicle_devices
                           (vehicle_id,device_bit_position,source,hardware_id,metadata_json)
                           VALUES (?,?,?,?,?)""",
                        (vehicle_id, position, item.get("source", "legacy_import"),
                         item.get("hardware_id"), json.dumps(item.get("metadata", {})))
                    )
        conn.commit()

        print(f"Database ready: {db_path}")
        print("\nDevice mapping:")
        for row in conn.execute(
            "SELECT device_bit_position,device_name,device_bit_integer FROM device_types "
            "ORDER BY device_bit_position"
        ):
            print(f"  bit {row['device_bit_position']}: {row['device_name']} = {row['device_bit_integer']}")
        print("\nTask mapping:")
        for row in conn.execute(
            "SELECT task_bit_position,task_id,task_bit_integer,devices_need_bit_integer "
            "FROM tasks ORDER BY task_bit_position"
        ):
            print(
                f"  bit {row['task_bit_position']}: {row['task_id']} = "
                f"{row['task_bit_integer']} (device requirements mask="
                f"{row['devices_need_bit_integer']})"
            )
        print("\nVehicles imported:", conn.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
