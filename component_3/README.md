# Component 3 — Vehicle State Receiver

Component 3 receives processed vehicle-state records from Component 2 over HTTP.
It accepts only the six-field contract defined at
`../shared/schema/component2_vehicle_state.schema.json`.

## Start the receiver

From the repository root:

```powershell
cd component_3
python -m pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8002
```

Health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8002/health
```

POST endpoint:

```text
http://127.0.0.1:8002/api/v1/vehicle-states
```

Component 2 sends one JSON POST per emitted vehicle state. The HTTP request body
contains exactly these fields:

- `vehicle_id`
- `window_start_timestamp`
- `window_duration_s`
- `velocity_mps`
- `direction_degrees`
- `task_capable_bit_integer`

A first observation may contain null values for velocity and direction. Component 3
validates and stores those nulls without inventing motion data.

## Local receiver storage

- CSV: `component_3/data/received/vehicle_states.csv`
- SQLite deduplication store: `component_3/data/received/component3_receiver.sqlite3`

The receiver uses the `Idempotency-Key` header to ignore duplicate retries. The
CSV is rebuilt from the SQLite store so it represents all successfully accepted
records, with the same six columns and explicit `null` values.

## Inspect recent records

```powershell
Invoke-RestMethod "http://127.0.0.1:8002/api/v1/vehicle-states?limit=20"
```

The receiver binds to localhost by default. For a different machine, bind to a
reachable interface, configure firewall access, and use HTTPS/authentication before
deployment outside a trusted development environment.
