# Component 2 — GPS Sliding-Window Features and Task Capability

Component 2 consumes Component 1's existing SSE GPS stream, archives raw observations,
maintains a per-vehicle sliding window, reads registered vehicle device inventory and
task requirements from the project-wide shared database, and emits a compact record
for Component 3.

Component 1's public GPS contract remains exactly
`device_id, timestamp, latitude, longitude`. Component 2 maps `device_id` to
`vehicle_id` internally. Component 2 does not infer installed IoT devices from GPS.

## Output contract

Each JSONL record contains exactly these six fields:

```json
{
  "vehicle_id": "dev_example",
  "window_start_timestamp": "2026-01-01T07:00:00",
  "window_duration_s": 10.0,
  "velocity_mps": 8.42,
  "direction_degrees": 87.3,
  "task_capable_bit_integer": 5
}
```

- `window_start_timestamp`: timestamp of the first GPS fix retained in the current window.
- `window_duration_s`: actual elapsed time between the first and last fixes used.
- `velocity_mps`: net displacement divided by elapsed window time.
- `direction_degrees`: first-to-last bearing clockwise from north (0° north, 90° east).
- `task_capable_bit_integer`: bitwise OR of all compatible task bits.

**First-fix rule:** the first GPS observation for a vehicle is emitted with
`velocity_mps=null` and `direction_degrees=null`. These remain null until a
second usable, time-separated GPS position is available. An excessive GPS gap
also leaves both motion values null. Direction remains null below the stationary
movement threshold.

## Sliding-window configuration

The values from the existing Component 2 configuration are preserved:

| Setting | Value |
|---|---:|
| `window_duration_s` | 10.0 |
| `update_interval_s` | 2.0 |
| `max_fix_gap_s` | 5.0 |
| `stationary_speed_threshold_mps` | 0.5 |

The window is maintained independently for each vehicle and emitted at the configured
update cadence.

## Shared database

The schema is at the project root, outside Component 1:

```text
shared/
  schema/database_schema.sql
  schema/gps_observation.schema.json
  schema/component2_vehicle_state.schema.json
  database/db_utils.py
  database/seed_database.py
  database/import_vehicle_capabilities.py
  database/vehicle_capabilities.example.json
```

Tables include `device_types`, `vehicles`, `vehicle_devices`, `tasks`,
`task_device_requirements`, and `vehicle_states`. Device and task bit positions
are zero-based, permanent, and never shifted or reused.

A vehicle is compatible with a task when:

```python
(vehicle_devices_bit_integer & devices_need_bit_integer) == devices_need_bit_integer
```

The task capability mask is the OR of the `task_bit_integer` values for all compatible
tasks. Unknown vehicle IDs receive mask 0; no device inventory is invented for them.

## Setup and run

Python 3.10+.

### 1. Initialize/populate the shared database

Run from the repository root:

```bash
python shared/database/seed_database.py
```

This creates `shared/database/project.db`, seeds starter device/task mappings, and
imports existing registered vehicles from the previous Component 2 JSON registry
when present. The seed is idempotent and preserves existing bit IDs.

### 2. Start the Component 2 registration API

From the repository root:

```bash
export COMPONENT2_ADMIN_TOKEN="replace-with-a-long-random-secret"
cd component_2/registration
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8001
```

PowerShell:

```powershell
$env:COMPONENT2_ADMIN_TOKEN="replace-with-a-long-random-secret"
cd component_2/registration
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8001
```

API docs: `http://127.0.0.1:8001/docs`. All task/device/fleet administration
requires the admin token.

### 3. Provision and register a vehicle

```bash
cd component_2/registration
python provision_vehicle.py dev_d3dc3bcc773f
```

Copy the one-time token into `component_2/vehicle_agent/device_config.json`.
The repository sample contains a placeholder; do not commit real vehicle tokens.
The `vehicle_id` must match Component 1's `device_id`.

On the vehicle-side host:

```bash
cd component_2/vehicle_agent
pip install -r requirements.txt
python vehicle_agent.py --once
```

The included inventory adapter reads configured devices. Add platform/vendor hardware
discovery adapters where actual automatic detection is required.

### 4. Add task definitions

Use the admin-protected `POST /api/v1/tasks` endpoint (Swagger UI or curl):

```bash
curl -X POST http://127.0.0.1:8001/api/v1/tasks \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: replace-with-a-long-random-secret" \
  -d '{"task_id":"inspection","required_devices":["camera","gps"]}'
```

New task/device types receive the next unused bit position. Updating a task preserves
its existing task bit position.

### 5. Start Component 1 and Component 2

Start Component 1 using its existing stream command:

```bash
cd component_1
python run_stream_server.py --host 127.0.0.1 --port 8765 --duration 0
```

Then start Component 2:

```bash
cd component_2
python stream_receiver.py --config receiver_config.json
```

Outputs:
- Raw GPS: `component_2/data/received/x/received_gps.csv`
- Component 3 JSONL: `component_2/data/processed/vehicle_states.jsonl`
- Shared SQLite DB: `shared/database/project.db`
- Receiver log: `component_2/logs/receiver.log`

### 6. Optional bulk inventory import

Copy and edit `shared/database/vehicle_capabilities.example.json`, then:

```bash
python shared/database/import_vehicle_capabilities.py --input shared/database/my_vehicle_capabilities.json
```

This uses only approved backend inventory data, not GPS inference.

## Tests

From the repository root:

```bash
python -m pip install pytest
python -m pytest component_2/tests -q
```

## Deployment note

SQLite is suitable for local same-machine development. When components run on separate
machines, use a shared PostgreSQL database and configure each component to use the same
database service. Protect the registry API with HTTPS/TLS and keep the admin token and
vehicle credentials private.
