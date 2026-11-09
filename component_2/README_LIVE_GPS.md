# Component 2 — Live GPS Receiver

This folder is the Component 2 side of the initial Component 1 → Component 2
communication setup. It receives GPS observations over Server-Sent Events (SSE),
validates the four-field GPS contract, places records into an in-memory queue,
and optionally archives them to CSV while writing logs.

## 1. Expected project layout

Copy/merge the `component_2` folder from this ZIP into the root of your project:

```text
graph-based-task-allocator/
├── component_1/
│   └── run_stream_server.py
└── component_2/
    ├── receiver_config.json
    ├── stream_receiver.py
    ├── README_LIVE_GPS.md
    └── ingestion/
        ├── __init__.py
        └── receiver.py
```

Do not overwrite any existing Component 2 files without checking them first.
Merge these files into your existing `component_2` folder.

## 2. Requirements

Python 3.10+ is recommended. This receiver uses only the Python standard library;
no `pip install` is required for this receiver.

## 3. Configure the connection

Open `receiver_config.json`.

For both components running on the same computer:

```json
"server_url": "http://127.0.0.1:8765/gps/stream"
```

For different computers, replace `127.0.0.1` with Component 1's LAN IP address,
and make sure the network/firewall permits TCP port 8765.

The receiver expects each observation to have exactly:

- `device_id`
- `timestamp`
- `latitude`
- `longitude`

The SSE batch envelope is expected to contain `batch_id`, `sim_time_s`,
`count`, and `observations`. The receiver does not require a `type` field
for GPS batch messages.

## 4. Start Component 1 first

Open Terminal 1 at the repository root:

```bash
cd component_1
python run_stream_server.py --host 0.0.0.0 --port 8765 --duration 0
```

Keep it running.

## 5. Start Component 2

Open Terminal 2 at the repository root:

```bash
cd component_2
python stream_receiver.py --config receiver_config.json
```

Optional URL override:

```bash
python stream_receiver.py --config receiver_config.json --url http://127.0.0.1:8765/gps/stream
```

Stop with `Ctrl+C`.

## 6. Output locations

Paths in the config are resolved relative to the directory containing
`receiver_config.json`. With the supplied configuration, the receiver creates:

```text
component_2/
├── data/
│   └── received/
│       └── x/
│           └── received_gps.csv
└── logs/
    └── receiver.log
```

The CSV contains the exact four GPS fields. The log records connection events,
batch statistics, validation failures, and reconnect attempts.

## 7. Check that it works

In another terminal:

```bash
curl http://127.0.0.1:8765/health
```

Inspect the receiver output:

```bash
# Git Bash / macOS / Linux
tail -f component_2/logs/receiver.log
```

On Windows PowerShell:

```powershell
Get-Content component_2/logs/receiver.log -Wait
```

Check the CSV:

```bash
head component_2/data/received/x/received_gps.csv
```

## 8. Important notes and limitations

- The receiver's in-memory queue currently feeds the CSV archive worker. It is
  not yet connected to your final preprocessing or graph-construction modules.
  Integrate a queue consumer/callback when those interfaces are ready.
- The queue is bounded. If it fills, the receiver reports an error and reconnects;
  inspect logs and increase `receiver.queue_max_records` or add a faster consumer.
- SSE is one-way HTTP streaming, not WebSocket.
- This initial server/receiver setup has no authentication or TLS. Keep it on a
  trusted local network.
- Batch-gap detection works only when the producer supplies monotonically
  increasing `batch_id` values.
- This receiver accepts the producer envelope described above. If your local
  `run_stream_server.py` emits a different JSON shape, share one raw SSE event
  and the parser can be adjusted to match it.


## 9. Component 2 preprocessing

The receiver is now connected to the live preprocessing engine. Validated GPS
observations are sent to both the raw CSV archive and the per-vehicle sliding
window processor. Configure vehicle/task bitmasks in `capabilities.json`.

See the top-level `README.md` for the complete Component 2 processing contract,
output schema, motion calculations, configuration, and tests.
