"""Shared SQLite database utilities for all project components."""
from pathlib import Path
import os
import sqlite3

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = Path(os.getenv(
    "PROJECT_DB_PATH", PROJECT_ROOT / "shared" / "database" / "project.db"
))
SCHEMA_PATH = PROJECT_ROOT / "shared" / "schema" / "database_schema.sql"


def connect(db_path=None):
    path = Path(db_path or DEFAULT_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def initialize_database(db_path=None):
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        # Migrate the earlier vehicle_states layout to the current six-field contract.
        columns = {row[1] for row in conn.execute("PRAGMA table_info(vehicle_states)")}
        if columns and "window_duration_s" not in columns:
            conn.execute("ALTER TABLE vehicle_states RENAME TO vehicle_states_legacy")
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            legacy_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(vehicle_states_legacy)")
            }
            if {"vehicle_id", "window_start_timestamp", "window_end_timestamp"} <= legacy_cols:
                conn.execute(
                    "INSERT INTO vehicle_states "
                    "(vehicle_id,window_start_timestamp,window_duration_s,velocity_mps,"
                    "direction_degrees,task_capable_bit_integer) "
                    "SELECT vehicle_id,window_start_timestamp,"
                    "MAX(0.0,(julianday(window_end_timestamp)-"
                    "julianday(window_start_timestamp))*86400.0),"
                    "velocity_mps,direction_degrees,task_capable_bit_integer "
                    "FROM vehicle_states_legacy"
                )
            conn.execute("DROP TABLE vehicle_states_legacy")
        conn.commit()
    finally:
        conn.close()
    return Path(db_path or DEFAULT_DB_PATH)
