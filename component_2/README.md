# Component 2 — Live GPS Mobility and Capability Preprocessor

Component 2 consumes Component 1's live Server-Sent Events (SSE) GPS stream.
It validates the four-field GPS contract, archives received observations to CSV,
maintains per-vehicle sliding windows, estimates mobility, and checks IoT
capability masks against task requirements. It does **not** allocate tasks;
Component 3 owns allocation.

## Data flow

```text
Component 1 simulator
      |
      | SSE / HTTP: JSON batch or individual GPS observation
      v
Component 2 ingestion receiver
      |--------------------|
      v                    v
raw CSV archive       preprocessing queue
                           |
                           v
                 per-vehicle sliding windows
                           |
             direction + velocity + task compatibility
                           |
                           v
                 processed JSONL vehicle states
```

## 1. Requirements and installation

- Python 3.10+
- No third-party dependencies; the receiver and processor use the standard library.

From the repository root, merge this `component_2/` directory into the existing
project. Do not replace unrelated existing Component 2 files.

## 2. Configure Component 1 connection

Edit `receiver_config.json`:

```json
"server_url": "http://127.0.0.1:8765/gps/stream"
```

If the services run on different machines, use Component 1's reachable LAN IP
and permit TCP port 8765 through the host firewall.

Component 2 accepts:
- SSE JSON batch: `{"batch_id": 1, "sim_time_s": 1.0, "count": 2, "observations": [...]}`
- SSE JSON single observation: `{"device_id": "...", "timestamp": "...", "latitude": 44.49, "longitude": 11.39}`

Every observation must contain exactly `device_id`, `timestamp`, `latitude`,
and `longitude`. Timestamps may be ISO-8601 strings with or without a timezone,
as in the sample data.

## 3. Configure vehicle IoT capabilities and task requirements

Edit `capabilities.json`. Replace the empty maps with your actual registry values:

```json
{
  "vehicle_capabilities": {
    "dev_516bc7badf8b": 11,
    "dev_06c592c81f21": 5
  },
  "task_requirements": {
    "task_001": 3,
    "task_002": 8
  }
}
```

Masks are non-negative decimal integers. For example, decimal `11` is binary
`1011`. A task is compatible only when the vehicle has **all** required bits:

```python
(vehicle_mask & required_mask) == required_mask
```

An empty or missing vehicle registry entry is treated as unknown; Component 2
does not assume the vehicle supports every task. Update this file with the
actual vehicle IDs and task definitions before interpreting compatible-task
output.

## 4. Configure live preprocessing

In `receiver_config.json`:

| Setting | Default | Meaning |
|---|---:|---|
| `window_duration_s` | 10 | Sliding GPS history retained per vehicle |
| `update_interval_s` | 2 | Minimum time between emitted states per vehicle |
| `max_fix_gap_s` | 5 | Larger gaps make the motion estimate unavailable |
| `stationary_speed_threshold_mps` | 0.5 | Below this path speed, direction is `STATIONARY` |
| `output_file` | `data/processed/vehicle_states.jsonl` | Processed state output |
| `capabilities_file` | `capabilities.json` | Vehicle/task mask registry |

Keep `window_duration_s > update_interval_s`. Processing is event-driven: a
vehicle state is evaluated when a new fix arrives, subject to its update interval.

## 5. Start both microservices

**Terminal 1 — Component 1** (use your Component 1 startup command; example):

```bash
cd component_1
python run_stream_server.py --host 0.0.0.0 --port 8765 --duration 0
```

**Terminal 2 — Component 2** (from the repository root):

```bash
cd component_2
python stream_receiver.py --config receiver_config.json
```

Optional endpoint override:

```bash
python stream_receiver.py --config receiver_config.json --url http://127.0.0.1:8765/gps/stream
```

Stop Component 2 with `Ctrl+C`.

## 6. Outputs

- Raw received GPS CSV: `data/received/x/received_gps.csv`
- Processed vehicle states: `data/processed/vehicle_states.jsonl`
- Receiver logs: `logs/receiver.log`

Each JSONL line is one processed vehicle state. Important fields include:
- `device_id`, `observed_at`, `observation_count`
- `compatible_tasks`, `capability_known`, `vehicle_iot_mask`
- `direction`, `bearing_deg`
- `velocity_mps`: magnitude of net displacement divided by elapsed time (average vector velocity)
- `speed_mps`: path distance divided by elapsed time (average scalar speed)
- `displacement_m`, `motion_available`, `motion_quality`

Direction/bearing is based on the first-to-last GPS fix in the active window.
Bearing is clockwise from north: N=0°, E=90°, S=180°, W=270°. GPS positions
are treated as WGS84 latitude/longitude. This is appropriate for both the
simulator's GPS output and ordinary GPS receivers.

If fewer than two usable fixes exist, or any adjacent fix gap exceeds
`max_fix_gap_s`, motion is unavailable; capability matching is still performed.

## 7. Tests

From `component_2/`:

```bash
python -m pytest -q
```

## 8. Current limitations

- SSE is one-way HTTP streaming, not WebSocket.
- The receiver reconnects, but the producer must support replay for guaranteed
  recovery of batches missed during a disconnect. Batch IDs are checked for gaps.
- The receiver currently uses in-memory queues. A prolonged consumer stall can
  fill the bounded queue; queue-full conditions are logged and cause reconnect.
- There is no authentication or TLS in the starter connection. Use a trusted
  network or add transport security before exposing it publicly.
- Component 2 does not perform map matching, route prediction, or task allocation.
