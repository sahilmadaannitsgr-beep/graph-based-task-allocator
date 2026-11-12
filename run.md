TERMINAL 1

$ cd component_2
$ cd registration/
$ python provision_vehicle.py dev_d3dc3bcc773f

change token in device_config.json
$ export COMPONENT2_ADMIN_TOKEN="5k5nDNoNYzPT6VpaLScekqAWITzVOWs1OYzS76ZCvt4"
(.venv) 


TERMINAL 2

$  source "/d/7th semester/gnn final year project/implement/component_1/.v
nv/Scripts/activate"
$ cd component_2/
$ cd vehicle_agent/
$ python vehicle_agent.py


TERMINAL 3

$ cd component_'1
$ python run_stream_server.py --host 127.0.0.1 --port 8765 --duration 0

TERMINAL 4

cd path\to\graph-based-task-allocator\component_2
python stream_receiver.py --config receiver_config.json





Bro, to run Component 1 and Component 2 together, you need five PowerShell terminals. Keep all five running.

The commands below assume you have downloaded the latest `main` branch of your GitHub repository.

# 1. Update your repository and install dependencies

Open PowerShell in your project folder.

```
cd path\to\graph-based-task-allocator

git pull origin main
```

Install the requirements:

```
python -m pip install -r component_1/requirements.txt

python -m pip install -r component_2/requirements.txt

python -m pip install -r component_2/registration/requirements.txt

python -m pip install -r component_2/vehicle_agent/requirements.txt

python -m pip install pytest
```

# 2. Initialize the shared database

Run this once from the project root:

```
python shared/database/seed_database.py
```

This creates:

```
shared/database/project.db
```

It also populates the starter device and task bit mappings and imports existing registered vehicles when available.

The default tasks are:

| Task                | Required devices | Task bit |
| ------------------- | ---------------- | -------- |
| gps_tracking        | GPS              | 1        |
| visual_inspection   | Camera           | 2        |
| traffic_observation | Camera + GPS     | 4        |

# 3. Provision your vehicle

Open PowerShell and run:

```
cd path\to\graph-based-task-allocator\component_2\registration

python provision_vehicle.py dev_d3dc3bcc773f
```

It will generate a vehicle authentication token.

Important: The vehicle ID must match the `device_id` appearing in Component 1's GPS records. Replace `dev_d3dc3bcc773f` with the matching ID when necessary.

Now open:

```
component_2/vehicle_agent/device_config.json
```

Paste the generated token into `api_token`.

Also make sure the `vehicle_id` matches the ID you provisioned, and that your device inventory contains the devices installed on that vehicle. The sample inventory has camera and GPS enabled.

# 4. Start the five terminals

Start in this order

## Terminal 1 — Component 2 registration API

Open a new PowerShell terminal:

```
cd path\to\graph-based-task-allocator\component_2\registration
```

Set your admin token:

```
$env:COMPONENT2_ADMIN_TOKEN="your_long_random_admin_secret"
```

Start the registration server:

```
uvicorn app:app --host 127.0.0.1 --port 8001
```

Keep this terminal running.

Registration API:

```
http://127.0.0.1:8001
```

API documentation:

```
http://127.0.0.1:8001/docs
```

## Terminal 2 — Vehicle-side IoT registration agent

Open another PowerShell terminal:

```
cd path\to\graph-based-task-allocator\component_2\vehicle_agent
```

Start the agent:

```
python vehicle_agent.py
```

This continuously reports the vehicle's configured IoT inventory to Component 2.

You should see registration messages showing the vehicle ID, number of devices, and capability mask.

## Terminal 3 — Component 1 live GPS simulator and streaming server

Open another PowerShell terminal:

```
cd path\to\graph-based-task-allocator\component_1
```

Start the simulator's live GPS streaming server:

```
python run_stream_server.py --host 127.0.0.1 --port 8765 --duration 0
```

This starts Component 1 and keeps the GPS stream running indefinitely.

The GPS stream endpoint is:

```
http://127.0.0.1:8765/gps/stream
```

Keep this terminal running.

## Terminal 4 — Component 2 GPS receiver and preprocessing

Open another PowerShell terminal:

```
cd path\to\graph-based-task-allocator\component_2
```

Start the receiver:

```
python stream_receiver.py --config receiver_config.json
```

This connects to Component 1, receives GPS observations, archives raw GPS data, maintains per-vehicle sliding windows, calculates movement features, and retrieves task compatibility from the shared database.

Keep this terminal running.

## Terminal 5 — Monitor Component 2 outputs

Open the fifth terminal:

```
cd path\to\graph-based-task-allocator
```

To monitor receiver logs:

```
Get-Content .\component_2\logs\receiver.log -Wait
```

To monitor the processed vehicle states instead, open another terminal and run:

```
Get-Content .\component_2\data\processed\vehicle_states.jsonl -Wait
```

# 5. Verify that everything is working

Check the registration API:

```
Invoke-RestMethod http://127.0.0.1:8001/health
```

Check Component 1's stream server:

```
Invoke-RestMethod http://127.0.0.1:8765/health
```

Run Component 2 tests from the project root:

```
python -m pytest component_2/tests -q
```

# 6. Understand the output

Component 2 generates:

```
component_2/
├── data/
│   ├── received/
│   │   └── x/
│   │       └── received_gps.csv
│   │
│   └── processed/
│       └── vehicle_states.jsonl
│
└── logs/
    └── receiver.log

shared/
└── database/
    └── project.db
```

Each processed JSONL record contains exactly six fields:

```
{
  "vehicle_id": "dev_d3dc3bcc773f",
  "window_start_timestamp": "2026-01-01T07:00:00",
  "window_duration_s": 10.0,
  "velocity_mps": 8.42,
  "direction_degrees": 87.3,
  "task_capable_bit_integer": 7
}
```

The first GPS observation for a vehicle will have:

```
{
  "velocity_mps": null,
  "direction_degrees": null
}
```

These values become available after a second usable GPS position arrives.

One important thing: If the vehicle ID registered through the agent does not match the `device_id` received from Component 1, its task capability mask will be `0`. Make sure those identities match.

## Startup order at a glance

| Order | Terminal         | Command                                                                 |
| ----- | ---------------- | ----------------------------------------------------------------------- |
| 1     | Database         | `python shared/database/seed_database.py`                               |
| 2     | Registration API | `uvicorn app:app --host 127.0.0.1 --port 8001`                          |
| 3     | Vehicle agent    | `python vehicle_agent.py`                                               |
| 4     | Component 1      | `python run_stream_server.py --host 127.0.0.1 --port 8765 --duration 0` |
| 5     | Component 2      | `python stream_receiver.py --config receiver_config.json`               |

Do not close Terminals 1–4 while you are running the complete live system. The database seeding command is a one-time setup step; the other four processes remain running.