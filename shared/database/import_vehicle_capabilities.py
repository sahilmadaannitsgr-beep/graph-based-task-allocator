"""Import backend-approved vehicle_id -> installed-device mappings into shared DB.

Input JSON:
{
  "vehicles": [
    {"vehicle_id": "dev_xxx", "devices": ["camera", "gps"], "description": "fleet van"}
  ]
}

Capabilities are imported from an approved manifest, never inferred from GPS.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from db_utils import connect, initialize_database, DEFAULT_DB_PATH


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="JSON vehicle inventory manifest")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    args = parser.parse_args()

    manifest = json.loads(Path(args.input).read_text(encoding="utf-8"))
    vehicles = manifest.get("vehicles", [])
    if not isinstance(vehicles, list):
        raise SystemExit("'vehicles' must be a JSON array")

    db_path = initialize_database(args.db)
    conn = connect(db_path)
    imported = 0
    try:
        for item in vehicles:
            vehicle_id = str(item.get("vehicle_id", "")).strip()
            devices = item.get("devices", [])
            if not vehicle_id or not isinstance(devices, list) or not devices:
                raise ValueError(f"Each vehicle needs vehicle_id and a non-empty devices list: {item}")
            mask, device_rows = 0, []
            for raw_name in devices:
                name = str(raw_name).strip().lower()
                if not name:
                    raise ValueError(f"Empty device name for {vehicle_id}")
                row = conn.execute(
                    "SELECT device_bit_position,device_bit_integer FROM device_types "
                    "WHERE device_name=?", (name,)
                ).fetchone()
                if row is None:
                    position = int(conn.execute(
                        "SELECT COALESCE(MAX(device_bit_position),-1)+1 FROM device_types"
                    ).fetchone()[0])
                    bit_value = 1 << position
                    conn.execute(
                        """INSERT INTO device_types
                           (device_bit_position,device_bit_integer,device_name,description)
                           VALUES (?,?,?,?)""",
                        (position, bit_value, name,
                         item.get("device_descriptions", {}).get(name))
                    )
                else:
                    position, bit_value = int(row[0]), int(row[1])
                mask |= bit_value
                device_rows.append((position, name))

            conn.execute(
                """INSERT INTO vehicles(vehicle_id,devices_bit_integer,description)
                   VALUES (?,?,?) ON CONFLICT(vehicle_id) DO UPDATE SET
                   devices_bit_integer=excluded.devices_bit_integer,
                   description=COALESCE(excluded.description,vehicles.description),
                   updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
                (vehicle_id, mask, item.get("description"))
            )
            conn.execute("DELETE FROM vehicle_devices WHERE vehicle_id=?", (vehicle_id,))
            for position, _ in device_rows:
                conn.execute(
                    "INSERT INTO vehicle_devices(vehicle_id,device_bit_position,source) "
                    "VALUES (?,?,?)",
                    (vehicle_id, position, "backend_manifest")
                )
            imported += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(f"Imported/updated {imported} vehicles in {db_path}")


if __name__ == "__main__":
    main()
