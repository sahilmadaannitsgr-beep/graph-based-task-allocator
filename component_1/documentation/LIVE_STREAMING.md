# Live GPS streaming: Component 1 → Component 2

This add-on creates a persistent one-way HTTP Server-Sent Events (SSE) connection. Component 1 generates observations, archives every generated fix to CSV/CSV.GZ, and broadcasts GPS batches. The public payload remains exactly `device_id`, `timestamp`, `latitude`, `longitude`.

## Install files

Copy `run_stream_server.py` and `stream_receiver.py` into `component_1/`, next to `run_simulation.py`, `run_dashboard.py`, `src/`, and `config/`. No extra pip packages are required.

## Start Component 1 stream

From the `component_1` directory:

```bash
python run_stream_server.py --config config/sim_config.json --net ../datasets/TAPASCologne/cologne.net.xml --routes ../datasets/TAPASCologne/cologne6to8.trips.xml --duration 3600
```

Adjust dataset paths if your folders differ. For fallback-network software testing, omit both `--net` and `--routes`; that is not a TAPASCologne run.

Endpoints:
- `http://127.0.0.1:8765/gps/stream` — continuous GPS batches
- `http://127.0.0.1:8765/health` — server status and archived record count
- `data/live_gps_stream.csv.gz` — continuously written archive

The producer archives data even when no subscriber is connected.

## Test the Component 2 connection

Open a second terminal in `component_1`:

```bash
python stream_receiver.py --url http://127.0.0.1:8765/gps/stream --output data/component2_received_gps.csv
```

Expected console pattern:

```text
batch=1 fixes=... total=... sim_time=...s
batch=2 fixes=... total=... sim_time=...s
```

Check status:

```bash
curl http://127.0.0.1:8765/health
```

The receiver saves only records it actually receives. Compare its row count with the server archive after the run.

## Three independent timing settings

| Setting | Where to change it | Default | What it controls |
|---|---|---:|---|
| Simulation step | `config/sim_config.json` → `sim.dt_s` | 1.0 s | How frequently the vehicle dynamics advance |
| GPS reporting interval | `config/sim_config.json` → `gps.reporting_interval_s` | 1 s | How frequently each device emits a GPS fix |
| Network batch interval | `config/sim_config.json` → `stream.batch_interval_s` | 1 s | How often buffered fixes are sent to Component 2 |

Add this property at the top level of the existing `config/sim_config.json` (merge it into the current JSON; do not replace the file):

```json
"stream": {
  "batch_interval_s": 1.0,
  "simulation_speed": 1.0,
  "queue_batches": 120
}
```

### GPS cadence

Edit the existing `gps` section:

```json
"gps": {
  "noise_std_m": 3.0,
  "reporting_interval_s": 1,
  "noise_correlation": 0.98
}
```

Use `1` for one fix per second or `2` for one fix every two seconds. This simulator schedules GPS reporting on integer-second ticks; use integer values for the current baseline.

### Simulation update frequency

Edit `sim.dt_s` in the same config. Keep `1.0` initially. A value of `0.25` advances vehicle motion four times per simulated second; with `gps.reporting_interval_s=1`, GPS still emits once per second. Lower values increase compute load.

### Network send cadence

Edit `stream.batch_interval_s`, or override a single run:

```bash
python run_stream_server.py --batch-interval 0.5
```

This aims to flush buffered GPS fixes every half second. Actual transmission cannot precede data generation; if `sim.dt_s=1`, expect roughly one batch per simulation step. For sub-second batches, set `sim.dt_s` no larger than the desired batch interval.

### Simulation wall-clock speed

`stream.simulation_speed=1.0` means real-time pacing (one simulated second per wall second); `2.0` runs twice as fast and `0.5` half speed. Override with `--sim-speed 1.0`.

For the first integration test, leave all intervals and simulation speed at 1.0.

## Reliability and safety notes

- This is simulated GPS, not physical vehicle GPS.
- SSE is a one-way persistent HTTP connection; Component 2 receives, it does not send commands back.
- The default host is `127.0.0.1` for two components on the same computer. The development server has no authentication or TLS. Do not expose it to the public internet.
- A slow subscriber whose in-memory queue fills is disconnected rather than silently skipping batches. The receiver warns about batch-ID gaps. The continuously written archive is the recovery source. Guaranteed replay/acknowledgement would require a durable broker or a replay API.
- A restart starts a fresh simulation and overwrites the default archive; use a different `--output` for each experiment.
- The existing browser dashboard remains separate. Do not assume its separately launched simulation is the same run as this stream server.
