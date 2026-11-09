# Component 2 — Live GPS Receiver, Vehicle Registration & Capability Preprocessor

Component 2 receives Component 1's live GPS stream over Server-Sent Events
(SSE), archives the raw observations, estimates each vehicle's movement,
maintains the IoT inventory reported by vehicle-side agents, and computes
task compatibility. Component 2 does **not** allocate tasks; Component 3
owns allocation.

## End-to-end architecture

```text
Component 1 simulator
      | SSE / HTTP (GPS JSON batches)
      v
Component 2 ingestion receiver ------------------> raw GPS CSV
      |
      v
per-vehicle sliding-window GPS processor
      |                          ^
      |                          | live vehicle/task masks
      v                          |
vehicle_states.jsonl       registration/data/vehicle_registry.json
                                 ^
                                 |
                   Component 2 registration API
                                 ^
                                 | authenticated JSON inventory
                                 |
                      vehicle-side registration agent

Admin registers task requirements through the registration API.
```

## Package layout

```text
component_2/
├── stream_receiver.py
├── receiver_config.json
├── capabilities.json                 # optional fallback masks
├── ingestion/                        # Component 1 SSE client + CSV archive
├── processing/                       # GPS mobility + bitmask compatibility
├── registration/
│   ├── app.py                         # FastAPI registration/task API
│   ├── provision_vehicle.py            # creates per-vehicle credentials
│   ├── requirements.txt
│   └── data/
│       ├── vehicle_registry.json       # canonical live registry
│       └── vehicle_tokens.json         # SHA-256 token hashes
├── vehicle_agent/
│   ├── vehicle_agent.py                # vehicle-side client agent
│   ├── device_config.json
│   └── requirements.txt
├── data/                               # raw and processed outputs
├── logs/
└── tests/
```

## 1. Requirements

- Python 3.10+
- Registration API: FastAPI + Uvicorn
- Vehicle agent: Requests
- GPS receiver and preprocessing: Python standard library

Install registration API dependencies:

```bash
cd component_2/registration
pip install -r requirements.txt
```

Install vehicle agent dependencies separately on the vehicle-side machine:

```bash
cd component_2/vehicle_agent
pip install -r requirements.txt
```

## 2. Start the Component 2 registration API

This API receives vehicle inventories and task definitions. It stores the
canonical registry in `registration/data/vehicle_registry.json`.

From `component_2/registration`:

Windows PowerShell:
```powershell
$env:COMPONENT2_ADMIN_TOKEN="set-a-long-admin-secret"
uvicorn app:app --host 0.0.0.0 --port 8001
```

macOS/Linux:
```bash
export COMPONENT2_ADMIN_TOKEN="set-a-long-admin-secret"
uvicorn app:app --host 0.0.0.0 --port 8001
```

Keep this process running. API docs are at `http://127.0.0.1:8001/docs`.
For separate machines, use the server's LAN address instead of localhost
in the vehicle agent config and allow TCP port 8001 through the firewall.

## 3. Provision each vehicle identity

Every vehicle must have a stable vehicle ID and its own credential. Run this
once per vehicle from `component_2/registration`:

```bash
python provision_vehicle.py dev_d3dc3bcc773f
```

Copy the generated token into that vehicle's `vehicle_agent/device_config.json`.
The server stores only the token's SHA-256 hash. The token is printed once;
if lost, provision a replacement and revoke the old hash from
`registration/data/vehicle_tokens.json`.

**Identity contract:** The `vehicle_id` reported by the vehicle agent must
equal the `device_id` in Component 1's GPS observations. Your current GPS
sample uses IDs such as `dev_d3dc3bcc773f`, so use that exact ID in the
vehicle agent config. If your real vehicle has a separate fleet ID, add an
explicit mapping layer rather than assuming the IDs are interchangeable.

## 4. Configure and run the vehicle-side agent

Edit `vehicle_agent/device_config.json`:

```json
{
  "vehicle_id": "dev_d3dc3bcc773f",
  "server_url": "http://127.0.0.1:8001",
  "api_token": "PASTE_THE_PROVISIONED_TOKEN_HERE",
  "heartbeat_interval_s": 300,
  "devices": [
    {"type": "camera", "enabled": true, "source": "configured"},
    {"type": "gps", "enabled": true, "source": "configured"},
    {"type": "rfid", "enabled": false, "source": "configured"}
  ]
}
```

One-time registration:

```bash
cd component_2/vehicle_agent
python vehicle_agent.py --once
```

Continuous startup registration + periodic refresh/heartbeat:

```bash
python vehicle_agent.py
```

When the inventory changes, update the vehicle-side config or discovery
adapter. The next registration replaces the server's latest inventory for
that vehicle. The server keeps one current inventory per vehicle ID.

### Hardware discovery and real-world adaptation

The included `discover_devices()` currently reads the explicit inventory
from `device_config.json`. There is no universal cross-platform Python
mechanism that reliably detects all automotive IoT hardware. For actual
automatic detection, implement hardware-specific adapters in that function:
USB/serial device enumeration, Linux device paths, CAN/OBD interfaces,
camera SDKs, vendor APIs, or sensor drivers. Report a device only when its
adapter confirms it is installed and available. Manual app selection can
remain as a fallback. Keep device type names stable and lowercase
(`camera`, `gps`, `temperature_sensor`, `rfid`, etc.).

## 5. Register task requirements

Task management is protected by the admin token. Open
`http://127.0.0.1:8001/docs`, select `POST /api/v1/tasks`, and provide:

```json
{
  "task_id": "inspection",
  "required_devices": ["camera", "gps"]
}
```

Or use curl:

```bash
curl -X POST http://127.0.0.1:8001/api/v1/tasks \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: set-a-long-admin-secret" \
  -d '{"task_id":"inspection","required_devices":["camera","gps"]}'
```

Device types receive stable power-of-two bit values when first registered.
The same device type always keeps the same bit in the canonical registry.
A vehicle mask is the bitwise OR of its installed device bits. A task mask
is the OR of its required device bits.

Compatibility uses the exact subset condition:

```python
(vehicle_mask & task_required_mask) == task_required_mask
```

The empty task requirement mask matches all registered vehicles; define
at least one required device for ordinary tasks.

## 6. Inspect the registration registry

- `GET /api/v1/vehicles` — requires `X-Admin-Token`; current inventory and mask.
- `GET /api/v1/tasks` — requires `X-Admin-Token`; task masks.
- `GET /api/v1/compatibility` — requires `X-Admin-Token`; all vehicle/task matches.
- `GET /api/v1/vehicles/{vehicle_id}/compatible-tasks` — requires admin token.
- `GET /health` — API health.

Canonical registry file:
`registration/data/vehicle_registry.json`

## 7. Start the live GPS receiver

Start Component 1 first, using its existing startup command. Then, from
`component_2`, run:

```bash
python stream_receiver.py --config receiver_config.json
```

Default Component 1 SSE endpoint:
`http://127.0.0.1:8765/gps/stream`

Override if needed:

```bash
python stream_receiver.py --config receiver_config.json \
  --url http://127.0.0.1:8765/gps/stream
```

The receiver accepts:
- SSE JSON batches containing an `observations` array.
- Individual SSE JSON records with exactly `device_id`, `timestamp`,
  `latitude`, and `longitude`.
- `complete` and `error` control messages.

## 8. Dynamic integration: vehicle registry → GPS processor

The GPS processor reads `registration/data/vehicle_registry.json` and
checks its modification time. When a vehicle reports new devices or an
admin changes task requirements, the processor reloads masks automatically
on subsequent GPS processing. It does not require exporting a second
capabilities file or restarting the receiver.

`capabilities.json` remains as a fallback for deployments that disable the
registration integration. When registration is enabled (default), the
canonical live registry is authoritative.

## 9. Component 2 GPS processing

Default settings in `receiver_config.json`:

| Setting | Default | Meaning |
|---|---:|---|
| `window_duration_s` | 10 | Sliding GPS history per vehicle |
| `update_interval_s` | 2 | Minimum time between emitted states per vehicle |
| `max_fix_gap_s` | 5 | Larger adjacent GPS gaps invalidate motion |
| `stationary_speed_threshold_mps` | 0.5 | Below this path speed, label stationary |

Direction is the first-to-last displacement bearing, clockwise from north.
`velocity_mps` is net displacement / elapsed time; `speed_mps` is total
path distance / elapsed time. Coordinates are WGS84 latitude/longitude.
At least two time-separated fixes for the same `device_id` are required.
The first output for a newly observed vehicle can therefore have null
movement fields and `motion_quality="insufficient_fixes"`.

## 10. Outputs

- Raw GPS CSV: `data/received/x/received_gps.csv`
- Processed vehicle states: `data/processed/vehicle_states.jsonl`
- Receiver logs: `logs/receiver.log`
- Canonical vehicle/task registry: `registration/data/vehicle_registry.json`

`vehicle_states.jsonl` includes vehicle ID, observation timestamp/count,
capability-known flag, vehicle mask, compatible task IDs, direction,
bearing, velocity, speed, displacement and motion-quality status.

## 11. Run tests

From `component_2`:

```bash
pip install pytest
python -m pytest -q
```

## 12. Security and deployment notes

This is a local development integration, not a fully hardened production
fleet service. Before real deployment:
- Put both APIs behind HTTPS/TLS.
- Replace JSON-file storage with a transactional database (e.g. PostgreSQL).
- Keep the admin token secret and rotate it; provision unique per-vehicle tokens.
- Restrict task-management and fleet-query endpoints to authorized admins.
- Add device attestation/allowlisting if vehicle inventory reports are security-sensitive.
- Add replay/recovery to the SSE producer if missed batches must be recovered.
- Add offline queueing on the vehicle agent if it must survive long outages.
- Implement and test real hardware discovery adapters on the target vehicle OS.
