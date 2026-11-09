# Graph-Based Task Allocator

A graph-based vehicle mobility and task allocation project using synthetic GPS trajectories, road-network information, and Graph Neural Networks (GNNs).

**Repository:** [graph-based-task-allocator](https://github.com/sahilmadaan048/graph-based-task-allocator)

---

## Table of Contents

- [Project Overview](#project-overview)
- [Project Architecture](#project-architecture)
- [Project Components](#project-components)
- [Technology Stack](#technology-stack)
- [Dataset: TAPASCologne](#dataset-tapascologne)
- [Installation and Setup](#installation-and-setup)
- [Running Component 1](#running-component-1)
- [SUMO-GUI Visualization](#sumo-gui-visualization)
- [Validation and Results](#validation-and-results)
- [Documentation](#documentation)
- [Project Roadmap](#project-roadmap)
- [Limitations and Assumptions](#limitations-and-assumptions)
- [References](#references)

---

## Project Overview

The objective of this project is to develop a graph-based system for vehicle mobility analysis and task allocation using Graph Neural Networks.

The project follows a modular architecture in which vehicle mobility data is generated, processed, represented as graphs, and used for downstream graph-learning tasks.

The initial stage uses the TAPASCologne traffic scenario as a reference for road-network structure and vehicle trip demand. A custom rule-based Python simulator generates synthetic vehicle GPS trajectories using this scenario.

The generated GPS observations provide the input for subsequent preprocessing, graph construction, and GNN development.

### Main objectives

- Generate synthetic vehicle mobility trajectories.
- Use a realistic urban road-network structure as the simulation environment.
- Extract useful mobility features from GPS observations.
- Represent vehicle movement and relationships using graph structures.
- Develop and evaluate Graph Neural Network models.
- Integrate the resulting system into a unified application.

## Project Architecture

```text
             TAPASCologne Dataset
                      |
                      v
           Component 1: Mobility
             Simulation (Python)
                      |
                      v
              Synthetic GPS Data
                      |
                      v
           Component 2: Data
          Preprocessing and Graphs
                      |
                      v
             Component 3: GNN
             Model Development
                      |
                      v
           Component 4: Evaluation
                      |
                      v
          Component 5: Integration
                      |
                      v
             Component 6:
                Dashboard
                      |
                      v
          Component 7: Research
           Paper and Documentation
```

## Project Components

| Component | Description | Status |
|---|---|---|
| Component 1 | Rule-based vehicle mobility simulator and synthetic GPS generation | Implemented |
| Component 2 | GPS preprocessing, feature extraction, graph construction, and dataset preparation | Next stage |
| Component 3 | Graph Neural Network development and training | Planned |
| Component 4 | Model evaluation and benchmarking | Planned |
| Component 5 | System integration and inference pipeline | Planned |
| Component 6 | Visualization dashboard | Planned |
| Component 7 | Research paper and final documentation | Ongoing |

---

## Technology Stack

| Technology | Purpose |
|---|---|
| Python | Core implementation |
| Eclipse SUMO | Traffic simulation and road-network visualization |
| SUMO-GUI | Graphical visualization of traffic |
| TAPASCologne | Road-network and trip-demand reference dataset |
| XML | Network, trip, and route data |
| CSV | GPS trajectory storage |
| NumPy / Pandas | Data processing |
| PyTorch / PyTorch Geometric | Planned GNN development |
| Git / GitHub | Version control |

---

## Dataset: TAPASCologne

### About the dataset

The project uses the TAPASCologne scenario provided through the Eclipse SUMO project.

TAPASCologne describes vehicle traffic in Cologne, Germany. It provides road-network information and mapped vehicle trip demand.

The dataset is used as the reference environment for our custom mobility simulator.

Official sources:

- [Eclipse SUMO — TAPASCologne documentation](https://sumo.dlr.de/docs/Data/Scenarios/TAPASCologne.html)
- [TAPASCologne dataset downloads — SourceForge](https://sourceforge.net/projects/sumo/files/traffic_data/scenarios/TAPASCologne/)
- [Eclipse SUMO official website](https://eclipse.dev/sumo/)

### Downloading the dataset

**Step 1: Open the official dataset download page**

[Download TAPASCologne from SourceForge](https://sourceforge.net/projects/sumo/files/traffic_data/scenarios/TAPASCologne/)

**Step 2: Download the scenario archive**

The documented scenario archive includes:

`TAPASCologne-0.32.0.7z`

Choose the appropriate available version from the download page.

**Step 3: Extract the archive**

Use 7-Zip or another compatible extraction utility.

**Step 4: Place the required files**

The project expects the following files under:

```text
datasets/
└── TAPASCologne/
    ├── cologne.net.xml
    ├── cologne6to8.trips.xml
    └── vtypes.add.xml
```

The extracted archive may contain additional directories and files. Locate the corresponding scenario files and place them in the expected project directory.

**Important:** Preserve the original dataset files. Do not modify the source network or trip-demand files merely to resolve routing or simulation errors.

### Dataset details used in Component 1

| Property | Value |
|---|---:|
| Directed road edges | 71,368 |
| Junctions / nodes | 31,584 |
| Vehicle trips in inspected 06:00–08:00 file | 252,731 |
| Vehicle type inspected | pkw |
| First departure | 06:00 |
| Last departure | 07:59:59 |
| Demand aggregation | 10-minute bins |

The TAPASCologne scenario is simulation-derived mobility demand. It should not be interpreted as independently measured real-world GPS trajectories.

### Dataset attribution and usage

Please refer to the official SUMO TAPASCologne documentation for licensing and usage conditions.

The SUMO documentation identifies Creative Commons licensing conditions, requires attribution to the data source, prohibits commercial use, and requests that users inform the SUMO community when using the data.

See the [official TAPASCologne documentation](https://sumo.dlr.de/docs/Data/Scenarios/TAPASCologne.html) for the applicable conditions.

---

## Installation and Setup

### 1. Clone the repository

```bash
git clone https://github.com/sahilmadaan048/graph-based-task-allocator.git

cd graph-based-task-allocator
```

### 2. Install Python

The Component 1 development environment used:

- Python 3.13.5
- Windows 10
- Git Bash / MINGW64

Check your Python installation:

```bash
python --version
```

### 3. Create a virtual environment

```bash
python -m venv .venv
```

Activate it in Git Bash:

```bash
source .venv/Scripts/activate
```

### 4. Install project dependencies

If a `requirements.txt` file is included:

```bash
pip install -r requirements.txt
```

If dependencies are maintained separately for individual components, install the dependencies specified in the respective component's documentation.

### 5. Install Eclipse SUMO

Download SUMO from:

[https://eclipse.dev/sumo/](https://eclipse.dev/sumo/)

Install the Windows version and ensure that the SUMO `bin` directory is available in your system PATH.

Check the installation:

```bash
sumo --version
sumo-gui --version
duarouter --version
```

On Windows, if SUMO is not available through PATH, you can invoke it using its installation path. For example:

```bash
"/c/Program Files (x86)/SUMO/bin/sumo-gui.exe"
```

### 6. Verify the dataset

From the repository root:

```bash
ls datasets/TAPASCologne/
```

Confirm that the following files are available:

```text
cologne.net.xml
cologne6to8.trips.xml
vtypes.add.xml
```

---

## Running Component 1

Component 1 implements a custom rule-based vehicle mobility simulator.

It uses the TAPASCologne road network and trip-demand information to generate synthetic vehicle GPS observations.

### Run the validation suite

From the repository root:

```bash
python scripts/run_full_validation.py
```

The recorded validation run completed with:

**13/13 tests passed.**

### Generated output files

The recorded 600-second simulation generated:

```text
data/
├── tapas_gps_600.0s.csv.gz
├── tapas_run_summary_600.0s.json
└── tapas_spawn_events_600.0s.json
```

The primary GPS output contains exactly four fields:

| Field | Description |
|---|---|
| `device_id` | Simulated vehicle identifier |
| `timestamp` | GPS observation timestamp |
| `latitude` | Latitude coordinate |
| `longitude` | Longitude coordinate |

Internal simulator attributes are not exposed in the public GPS output.

---

## SUMO-GUI Visualization

SUMO-GUI can be used to visualize the TAPASCologne road network and the reference traffic simulation.

**Important distinction:** The SUMO reference simulation and our custom Python simulator are separate. SUMO-GUI visualizes the TAPAS trip scenario, while the custom Python simulator generates its own vehicle movements and GPS observations.

### 1. Open the road network

```bash
sumo-gui -n datasets/TAPASCologne/cologne.net.xml
```

This displays the road network and junctions without moving vehicles.

### 2. Generate vehicle routes

The trip file contains trip definitions. Convert these trips into routes using `duarouter`:

```bash
duarouter \
  -n datasets/TAPASCologne/cologne.net.xml \
  -r datasets/TAPASCologne/cologne6to8.trips.xml \
  -o datasets/TAPASCologne/cologne6to8.rou.xml
```

The successful recorded run generated 252,731 vehicle entries.

Verify the route count:

```bash
grep -c '<vehicle ' datasets/TAPASCologne/cologne6to8.rou.xml
```

Expected output:

```text
252731
```

**Note:** In the tested workflow, the route-generation command intentionally does not load `vtypes.add.xml` using `-a`, because doing so produced a duplicate `pkw` vehicle-type error. The resulting route file was successfully loaded without separately loading that additional file.

### 3. Run the full two-hour simulation

```bash
sumo-gui \
  -n datasets/TAPASCologne/cologne.net.xml \
  -r datasets/TAPASCologne/cologne6to8.rou.xml \
  --begin 21600 \
  --end 28800 \
  --delay 50
```

This visualizes the TAPASCologne reference traffic scenario from:

- Start: 06:00
- End: 08:00
- Total simulated duration: 2 hours

The `--delay 50` option controls the GUI animation delay.

### 4. Troubleshooting

**Road network appears, but vehicles do not:**

Check that the route file is loaded using the `-r` option.

**Duplicate vehicle type `pkw`:**

Avoid loading the same vehicle type definition twice. Follow the tested commands above.

**Simulation ends immediately:**

Verify that the route file exists and contains vehicle entries.

---

## Validation and Results

The following are the recorded results from Component 1.

### Simulation results

| Metric | Result |
|---|---:|
| Simulated duration | 600 seconds |
| GPS records | 4,023,356 |
| Observed devices | 13,853 |
| Vehicles spawned | 13,963 |
| Vehicles exited | 1,356 |
| Wall-clock runtime | 888.56 seconds |

### GPS quality audit

| Metric | Result |
|---|---:|
| Expected GPS fields | 4 |
| Invalid records | 0 |
| Duplicate device/timestamp pairs | 0 |
| Sampling intervals above 1.01 seconds | 0 |
| Derived speed samples | 4,009,503 |

### Spatial validation

Starting-position distribution comparison:

| Metric | Result |
|---|---:|
| Pearson correlation | 0.9576 |
| Spearman correlation | 0.9526 |
| Top-20 overlap | 13/20 |

Broader spatial occupancy comparison:

| Metric | Result |
|---|---:|
| Pearson correlation | 0.8161 |
| Spearman correlation | 0.8273 |
| Top-20 overlap | 11/20 |

First ten-minute demand-bin count agreement:

**92.55%**

These comparisons use TAPAS-derived reference patterns. They do not constitute independent validation against measured real-world GPS data.

---

## Documentation

Detailed documentation for Component 1 is available separately.

| Document | Contents |
|---|---|
| [Setup Guide](component_1/documentation/SETUP_GUIDE.md) | Environment setup, dependencies, SUMO installation, dataset preparation, and execution commands |
| [Demonstration Guide](component_1/documentation/DEMONSTRATION_GUIDE.md) | Step-by-step demonstration, SUMO-GUI usage, validation presentation, and demonstration checklist |
| [Implementation Documentation](component_1/documentation/IMPLEMENTATION.md) | Architecture, implementation details, benchmarks, results, assumptions, and limitations |

---

## Project Roadmap

### Component 1 — Rule-Based Vehicle Mobility Simulation

- TAPASCologne network integration.
- TAPAS trip-demand integration.
- Custom rule-based vehicle movement.
- Synthetic GPS generation.
- GPS quality and spatial validation.

### Component 2 — GPS Preprocessing and Graph Construction

- GPS data cleaning and validation.
- Trajectory feature extraction.
- Temporal and spatial feature engineering.
- Graph representation design.
- Dataset preparation for GNN training.

### Component 3 — Graph Neural Network Development

- GNN architecture selection.
- Model implementation.
- Training and validation.
- Hyperparameter tuning.

### Component 4 — Evaluation and Benchmarking

- Baseline model comparisons.
- GNN performance evaluation.
- Quantitative metrics.
- Error and performance analysis.

### Component 5 — Integration

- Connect preprocessing and model inference.
- Develop a unified pipeline.
- Test end-to-end functionality.

### Component 6 — Dashboard

- Visualize vehicle trajectories.
- Display graph structures.
- Present prediction results.
- Provide an interactive interface.

### Component 7 — Research and Documentation

- Experimental analysis.
- Results and discussion.
- Research paper preparation.
- Final project documentation.

---

## Component 2 → Component 3 data handoff

Component 2 maintains a 10-second per-vehicle GPS window and emits a new state
every 2 seconds (the stride). After the initial warm-up, the windows overlap by
8 seconds. Each emitted state is saved locally as JSONL and CSV, then sent by
HTTP POST to the Component 3 receiver.

The Component 3 receiver is available at `component_3/app.py`. It validates
the six-field vehicle-state contract, deduplicates retried requests, and stores
accepted records in `component_3/data/received/vehicle_states.csv` and a local
SQLite receipt store. The GNN model and training pipeline remain a separate
planned part of Component 3.

Start the receiver in its own terminal:

```powershell
cd component_3
python -m pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8002
```

Health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8002/health
```

Component 2's sender is enabled in `component_2/receiver_config.json` and
targets `http://127.0.0.1:8002/api/v1/vehicle-states`. If Component 3 is
unavailable, Component 2 retains pending messages in
`component_2/data/processed/component3_outbox.jsonl` and retries delivery.

Local Component 2 files:
- `component_2/data/processed/vehicle_states.jsonl`
- `component_2/data/processed/vehicle_states.csv`
- `component_2/data/processed/component3_outbox.jsonl` (pending deliveries)

## Limitations and Assumptions

- The generated GPS trajectories are synthetic, not measured real-world GPS observations.
- TAPASCologne is used as the reference road network and demand scenario.
- The custom Python simulator does not reproduce the exact vehicle trajectories generated by SUMO.
- The configured density multiplier of 1.3 is a selected simulation parameter, not an independently calibrated real-world value.
- The recorded spatial comparisons are against TAPAS-derived references.
- Full 12-bin temporal-profile validation was pending in the recorded Component 1 status.
- Some SUMO road edges have unusually high configured speed limits; these should not automatically be interpreted as legal speed limits.
- Some actuated traffic-light configurations produced warnings about missing controlling detectors.

These limitations should be considered when interpreting the simulation results and designing subsequent components.

---

## References

1. [Eclipse SUMO — TAPASCologne Scenario](https://sumo.dlr.de/docs/Data/Scenarios/TAPASCologne.html)
2. [TAPASCologne Dataset Downloads](https://sourceforge.net/projects/sumo/files/traffic_data/scenarios/TAPASCologne/)
3. [Eclipse SUMO Documentation](https://sumo.dlr.de/docs/)
4. Alvarez Lopez et al. (2018), *Microscopic Traffic Simulation using SUMO*, IEEE Intelligent Transportation Systems Conference.

---

## Authors and Repository

**Project:** Graph-Based Task Allocator

**GitHub:** [https://github.com/sahilmadaan048/graph-based-task-allocator](https://github.com/sahilmadaan048/graph-based-task-allocator)

