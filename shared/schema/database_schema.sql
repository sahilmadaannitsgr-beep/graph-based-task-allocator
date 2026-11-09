-- Shared project database schema. Kept outside component_1 so every component can use it.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS device_types (
    device_bit_position INTEGER PRIMARY KEY CHECK (device_bit_position >= 0),
    device_bit_integer INTEGER NOT NULL UNIQUE CHECK (device_bit_integer > 0),
    device_name TEXT NOT NULL UNIQUE,
    description TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS vehicles (
    vehicle_id TEXT PRIMARY KEY,
    devices_bit_integer INTEGER NOT NULL DEFAULT 0 CHECK (devices_bit_integer >= 0),
    description TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS vehicle_devices (
    vehicle_id TEXT NOT NULL REFERENCES vehicles(vehicle_id) ON DELETE CASCADE,
    device_bit_position INTEGER NOT NULL REFERENCES device_types(device_bit_position),
    source TEXT NOT NULL DEFAULT 'configured',
    hardware_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (vehicle_id, device_bit_position)
);

CREATE TABLE IF NOT EXISTS tasks (
    task_bit_position INTEGER PRIMARY KEY CHECK (task_bit_position >= 0),
    task_bit_integer INTEGER NOT NULL UNIQUE CHECK (task_bit_integer > 0),
    task_id TEXT NOT NULL UNIQUE,
    devices_need_bit_integer INTEGER NOT NULL CHECK (devices_need_bit_integer >= 0),
    description TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS task_device_requirements (
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    device_bit_position INTEGER NOT NULL REFERENCES device_types(device_bit_position),
    PRIMARY KEY (task_id, device_bit_position)
);

CREATE TABLE IF NOT EXISTS vehicle_states (
    state_id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id TEXT NOT NULL,
    window_start_timestamp TEXT NOT NULL,
    window_duration_s REAL NOT NULL,
    velocity_mps REAL,
    direction_degrees REAL,
    task_capable_bit_integer INTEGER NOT NULL DEFAULT 0 CHECK (task_capable_bit_integer >= 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_vehicle_states_vehicle_time
    ON vehicle_states(vehicle_id, window_start_timestamp);
CREATE INDEX IF NOT EXISTS idx_vehicle_devices_device
    ON vehicle_devices(device_bit_position);
CREATE INDEX IF NOT EXISTS idx_task_device_requirements_device
    ON task_device_requirements(device_bit_position);
