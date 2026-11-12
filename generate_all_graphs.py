#!/usr/bin/env python3
"""Generate all project vehicle-mobility and capability graphs from Component 1/2 CSVs.

Required inputs
---------------
GPS CSV columns:
    device_id,timestamp,latitude,longitude

Processed vehicle-state CSV columns:
    vehicle_id,window_start_timestamp,window_duration_s,velocity_mps,
    direction_degrees,task_capable_bit_integer

Run from the repository root (auto-detects common project paths):
    python generate_all_graphs.py

Or specify paths explicitly:
    python generate_all_graphs.py \
        --gps component_2/data/received/x/received_gps.csv \
        --states component_2/data/processed/vehicle_states.csv \
        --output graphs

Optional task labels CSV:
    task_bit_position,task_name
    0,Air quality sensing
    1,Noise monitoring

The task heatmap and bipartite graph use the task-capability bitmask. Without a
label file, task nodes are named "Task bit N". These are compatibility/capability
visualizations, NOT records of real task assignments.

Dependencies:
    pip install pandas numpy matplotlib
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates


GPS_REQUIRED = ["device_id", "timestamp", "latitude", "longitude"]
STATE_REQUIRED = [
    "vehicle_id", "window_start_timestamp", "window_duration_s",
    "velocity_mps", "direction_degrees", "task_capable_bit_integer",
]
SPEED_EDGES = np.linspace(0.0, 100.0, 401)  # 0.25 m/s bins; final bucket is >100 m/s
SPEED_BIN_COUNT = len(SPEED_EDGES)  # 400 regular bins + overflow bucket


def say(message: str) -> None:
    print(message, flush=True)


def resolve_input(explicit: Optional[str], names: List[str], root: Path) -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Input file not found: {path}")
        return path

    # Prefer known repository paths, then search the project tree.
    known = {
        "received_gps.csv": [
            root / "component_2/data/received/x/received_gps.csv",
            root / "component_2/data/received/received_gps.csv",
            root / "component_2/data/received_gps.csv",
            root / "received_gps.csv",
        ],
        "vehicle_states.csv": [
            root / "component_2/data/processed/vehicle_states.csv",
            root / "component_3/data/received/vehicle_states.csv",
            root / "component_2/data/vehicle_states.csv",
            root / "vehicle_states.csv",
        ],
    }
    for name in names:
        for candidate in known.get(name, []):
            if candidate.is_file():
                return candidate.resolve()
    for name in names:
        found = sorted(root.rglob(name))
        found = [p for p in found if p.is_file() and "__pycache__" not in str(p)]
        if found:
            return found[0].resolve()
    raise FileNotFoundError(
        f"Could not locate {', '.join(names)}. Use --gps and --states to specify the files."
    )


def read_header(path: Path) -> List[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return next(csv.reader(f))


def parse_times(series: pd.Series) -> pd.Series:
    # UTC conversion normalizes mixed timestamps; naive timestamps retain their clock time.
    return pd.to_datetime(series, errors="coerce", utc=True)


def as_int_mask(value) -> int:
    try:
        if pd.isna(value):
            return 0
        return max(0, int(float(value)))
    except (TypeError, ValueError, OverflowError):
        return 0


def set_bits(mask: int) -> List[int]:
    bits: List[int] = []
    bit = 0
    while mask:
        if mask & 1:
            bits.append(bit)
        mask >>= 1
        bit += 1
    return bits


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def local_xy(lat: np.ndarray, lon: np.ndarray, lat0: float, lon0: float) -> Tuple[np.ndarray, np.ndarray]:
    x = (lon - lon0) * 111320.0 * math.cos(math.radians(lat0))
    y = (lat - lat0) * 110540.0
    return x, y


def summarize_gps(path: Path, chunksize: int, progress_every: int = 5) -> dict:
    header = read_header(path)
    missing = [c for c in GPS_REQUIRED if c not in header]
    if missing:
        raise ValueError(f"GPS CSV is missing required columns: {missing}")

    per_minute: Dict[pd.Timestamp, set] = defaultdict(set)
    per_second_count: Dict[pd.Timestamp, int] = defaultdict(int)
    vehicle_info: Dict[str, dict] = {}
    min_time = max_time = None
    total_rows = bad_rows = 0
    chunk_no = 0

    reader = pd.read_csv(
        path, usecols=GPS_REQUIRED, dtype={"device_id": "string", "timestamp": "string"},
        chunksize=chunksize, low_memory=True,
    )
    for chunk in reader:
        chunk_no += 1
        total_rows += len(chunk)
        chunk["timestamp"] = parse_times(chunk["timestamp"])
        for col in ["latitude", "longitude"]:
            chunk[col] = pd.to_numeric(chunk[col], errors="coerce")
        valid = chunk.dropna(subset=GPS_REQUIRED).copy()
        valid = valid[
            valid["latitude"].between(-90, 90) & valid["longitude"].between(-180, 180)
        ]
        bad_rows += len(chunk) - len(valid)
        if valid.empty:
            continue
        tmin, tmax = valid["timestamp"].min(), valid["timestamp"].max()
        min_time = tmin if min_time is None else min(min_time, tmin)
        max_time = tmax if max_time is None else max(max_time, tmax)

        valid["minute"] = valid["timestamp"].dt.floor("min")
        pairs = valid[["minute", "device_id"]].drop_duplicates()
        for minute, group in pairs.groupby("minute", sort=False):
            per_minute[minute].update(group["device_id"].astype(str).tolist())

        valid["second"] = valid["timestamp"].dt.floor("s")
        second_counts = valid.groupby("second", sort=False).size()
        for second, count in second_counts.items():
            per_second_count[second] += int(count)

        # Per-vehicle min/max fixes across chunks, avoiding retention of all GPS records.
        for vehicle_id, group in valid.groupby("device_id", sort=False):
            group = group.sort_values("timestamp")
            first = group.iloc[0]
            last = group.iloc[-1]
            key = str(vehicle_id)
            info = vehicle_info.get(key)
            if info is None:
                vehicle_info[key] = {
                    "count": int(len(group)),
                    "first_t": first["timestamp"], "first_lat": float(first["latitude"]),
                    "first_lon": float(first["longitude"]),
                    "last_t": last["timestamp"], "last_lat": float(last["latitude"]),
                    "last_lon": float(last["longitude"]),
                }
            else:
                info["count"] += int(len(group))
                if first["timestamp"] < info["first_t"]:
                    info.update(first_t=first["timestamp"], first_lat=float(first["latitude"]), first_lon=float(first["longitude"]))
                if last["timestamp"] > info["last_t"]:
                    info.update(last_t=last["timestamp"], last_lat=float(last["latitude"]), last_lon=float(last["longitude"]))
        if chunk_no % progress_every == 0:
            say(f"  Read GPS chunks: {chunk_no:,}; rows so far: {total_rows:,}")

    if not vehicle_info:
        raise ValueError("No valid GPS rows were found in the GPS CSV.")

    snapshot_second = max(per_second_count, key=per_second_count.get)
    path_scores = []
    for vid, info in vehicle_info.items():
        displacement = haversine_m(info["first_lat"], info["first_lon"], info["last_lat"], info["last_lon"])
        path_scores.append((vid, displacement, info["count"]))
    path_scores.sort(key=lambda x: (x[1], x[2]), reverse=True)

    return {
        "per_minute": per_minute,
        "vehicle_info": vehicle_info,
        "path_scores": path_scores,
        "snapshot_second": snapshot_second,
        "snapshot_record_count": per_second_count[snapshot_second],
        "per_second_count": per_second_count,
        "total_rows": total_rows,
        "bad_rows": bad_rows,
        "vehicle_count": len(vehicle_info),
        "min_time": min_time,
        "max_time": max_time,
    }


def collect_selected_gps(path: Path, selected_ids: set, snapshot_second: pd.Timestamp,
                         chunksize: int) -> Tuple[dict, pd.DataFrame]:
    paths: Dict[str, list] = defaultdict(list)
    snapshot_rows: List[pd.DataFrame] = []
    reader = pd.read_csv(
        path, usecols=GPS_REQUIRED, dtype={"device_id": "string", "timestamp": "string"},
        chunksize=chunksize, low_memory=True,
    )
    for chunk in reader:
        chunk["timestamp"] = parse_times(chunk["timestamp"])
        chunk["latitude"] = pd.to_numeric(chunk["latitude"], errors="coerce")
        chunk["longitude"] = pd.to_numeric(chunk["longitude"], errors="coerce")
        chunk = chunk.dropna(subset=GPS_REQUIRED)
        chunk = chunk[
            chunk["latitude"].between(-90, 90) & chunk["longitude"].between(-180, 180)
        ]
        if chunk.empty:
            continue
        selected = chunk[chunk["device_id"].astype(str).isin(selected_ids)]
        for vid, group in selected.groupby("device_id", sort=False):
            paths[str(vid)].extend(
                zip(group["timestamp"].tolist(), group["latitude"].astype(float).tolist(), group["longitude"].astype(float).tolist())
            )
        exact = chunk[chunk["timestamp"].dt.floor("s") == snapshot_second]
        if not exact.empty:
            snapshot_rows.append(exact)

    path_frames = {}
    for vid, rows in paths.items():
        frame = pd.DataFrame(rows, columns=["timestamp", "latitude", "longitude"])
        frame = frame.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
        path_frames[vid] = frame

    if snapshot_rows:
        snapshot = pd.concat(snapshot_rows, ignore_index=True)
        snapshot = snapshot.sort_values("timestamp").drop_duplicates("device_id", keep="last")
    else:
        snapshot = pd.DataFrame(columns=GPS_REQUIRED)
    return path_frames, snapshot


def summarize_states(path: Path, chunksize: int, interval_seconds: int,
                     progress_every: int = 5) -> dict:
    header = read_header(path)
    missing = [c for c in STATE_REQUIRED if c not in header]
    if missing:
        raise ValueError(f"Vehicle-state CSV is missing required columns: {missing}")

    bin_hist: Dict[int, np.ndarray] = {}
    bin_count: Dict[int, int] = defaultdict(int)
    bin_sum: Dict[int, float] = defaultdict(float)
    global_hist = np.zeros(SPEED_BIN_COUNT, dtype=np.int64)
    vehicle_count: Dict[str, int] = defaultdict(int)
    latest_mask: Dict[str, Tuple[pd.Timestamp, int]] = {}
    total_rows = valid_speed_count = invalid_speed_count = partial_window_count = 0
    full_window_count = 0
    min_time = max_time = None
    chunks = 0

    reader = pd.read_csv(
        path,
        usecols=STATE_REQUIRED,
        dtype={"vehicle_id": "string", "window_start_timestamp": "string"},
        chunksize=chunksize,
        low_memory=True,
    )
    for chunk in reader:
        chunks += 1
        total_rows += len(chunk)
        chunk["window_start_timestamp"] = parse_times(chunk["window_start_timestamp"])
        chunk["window_duration_s"] = pd.to_numeric(chunk["window_duration_s"], errors="coerce")
        chunk["velocity_mps"] = pd.to_numeric(chunk["velocity_mps"], errors="coerce")
        chunk["task_capable_bit_integer"] = pd.to_numeric(chunk["task_capable_bit_integer"], errors="coerce").fillna(0)
        chunk = chunk.dropna(subset=["vehicle_id", "window_start_timestamp"])
        if chunk.empty:
            continue
        tmin, tmax = chunk["window_start_timestamp"].min(), chunk["window_start_timestamp"].max()
        min_time = tmin if min_time is None else min(min_time, tmin)
        max_time = tmax if max_time is None else max(max_time, tmax)
        for vid, count in chunk["vehicle_id"].astype(str).value_counts().items():
            vehicle_count[vid] += int(count)

        # Keep the latest bitmask for each vehicle (not the OR of old masks).
        mask_rows = chunk[["vehicle_id", "window_start_timestamp", "task_capable_bit_integer"]]
        latest_in_chunk = mask_rows.sort_values("window_start_timestamp").drop_duplicates("vehicle_id", keep="last")
        for row in latest_in_chunk.itertuples(index=False):
            vid = str(row.vehicle_id)
            stamp = row.window_start_timestamp
            mask = as_int_mask(row.task_capable_bit_integer)
            old = latest_mask.get(vid)
            if old is None or stamp >= old[0]:
                latest_mask[vid] = (stamp, mask)

        full = chunk[chunk["window_duration_s"].between(9.99, 10.01, inclusive="both")]
        full_window_count += len(full)
        partial_window_count += len(chunk) - len(full)
        if not full.empty:
            speeds = full["velocity_mps"].to_numpy(dtype=float)
            times = full["window_start_timestamp"]
            valid_mask = np.isfinite(speeds) & (speeds >= 0)
            invalid_speed_count += int((~valid_mask).sum())
            if valid_mask.any():
                speeds = speeds[valid_mask]
                time_ns = times[valid_mask].astype("int64").to_numpy()
                time_bins = (time_ns // 1_000_000_000 // interval_seconds).astype(np.int64)
                speed_idx = np.searchsorted(SPEED_EDGES, speeds, side="right") - 1
                speed_idx = np.clip(speed_idx, 0, SPEED_BIN_COUNT - 1)
                global_hist += np.bincount(speed_idx, minlength=SPEED_BIN_COUNT).astype(np.int64)
                valid_speed_count += int(len(speeds))
                unique_bins, inverse = np.unique(time_bins, return_inverse=True)
                flat = inverse * SPEED_BIN_COUNT + speed_idx
                hist2d = np.bincount(flat, minlength=len(unique_bins) * SPEED_BIN_COUNT).reshape(len(unique_bins), SPEED_BIN_COUNT)
                sums = np.bincount(inverse, weights=speeds, minlength=len(unique_bins))
                counts = np.bincount(inverse, minlength=len(unique_bins))
                for i, tb in enumerate(unique_bins):
                    key = int(tb)
                    if key not in bin_hist:
                        bin_hist[key] = np.zeros(SPEED_BIN_COUNT, dtype=np.int64)
                    bin_hist[key] += hist2d[i]
                    bin_sum[key] += float(sums[i])
                    bin_count[key] += int(counts[i])
        if chunks % progress_every == 0:
            say(f"  Read state chunks: {chunks:,}; rows so far: {total_rows:,}")

    return {
        "bin_hist": bin_hist, "bin_count": bin_count, "bin_sum": bin_sum,
        "global_hist": global_hist, "vehicle_count": vehicle_count,
        "latest_mask": {vid: item[1] for vid, item in latest_mask.items()},
        "latest_mask_time": {vid: item[0] for vid, item in latest_mask.items()},
        "total_rows": total_rows, "valid_speed_count": valid_speed_count,
        "invalid_speed_count": invalid_speed_count,
        "partial_window_count": partial_window_count,
        "full_window_count": full_window_count,
        "vehicle_count_unique": len(vehicle_count),
        "min_time": min_time, "max_time": max_time,
        "interval_seconds": interval_seconds,
    }


def quantile_from_hist(hist: np.ndarray, q: float) -> float:
    total = int(hist.sum())
    if total <= 0:
        return float("nan")
    target = q * (total - 1)
    idx = int(np.searchsorted(np.cumsum(hist), target + 1, side="left"))
    if idx >= len(SPEED_EDGES) - 1:
        return float(SPEED_EDGES[-1] + 0.125)
    return float((SPEED_EDGES[idx] + SPEED_EDGES[idx + 1]) / 2)


def load_task_labels(path: Optional[str]) -> dict:
    labels = {}
    if not path:
        return labels
    csv_path = Path(path).expanduser().resolve()
    if not csv_path.is_file():
        raise FileNotFoundError(f"Task labels CSV not found: {csv_path}")
    df = pd.read_csv(csv_path)
    bit_col = next((c for c in ["task_bit_position", "bit_position", "bit"] if c in df.columns), None)
    name_col = next((c for c in ["task_name", "task_type_name", "name", "task_type"] if c in df.columns), None)
    if not bit_col or not name_col:
        raise ValueError("Task labels CSV needs columns task_bit_position and task_name (aliases: bit_position/name).")
    for row in df[[bit_col, name_col]].dropna().itertuples(index=False):
        labels[int(row[0])] = str(row[1])
    return labels


def make_trajectory_plot(outdir: Path, path_frames: dict, path_scores: list, limit: int) -> str:
    selected = [vid for vid, displacement, count in path_scores[:limit] if vid in path_frames and len(path_frames[vid]) >= 2]
    fig, ax = plt.subplots(figsize=(11, 8))
    if not selected:
        ax.text(0.5, 0.5, "No multi-point vehicle trajectories available", ha="center", va="center", transform=ax.transAxes)
    else:
        all_lat, all_lon = [], []
        for vid in selected:
            frame = path_frames[vid]
            lat = frame["latitude"].to_numpy(float)
            lon = frame["longitude"].to_numpy(float)
            all_lat.extend(lat.tolist()); all_lon.extend(lon.tolist())
        lat0, lon0 = float(np.mean(all_lat)), float(np.mean(all_lon))
        for vid in selected:
            frame = path_frames[vid]
            x, y = local_xy(frame["latitude"].to_numpy(float), frame["longitude"].to_numpy(float), lat0, lon0)
            ax.plot(x, y, linewidth=1.4, alpha=0.82, label=vid[-13:])
            ax.scatter(x[0], y[0], s=22, marker="o", edgecolor="black", linewidth=0.4, zorder=3)
            ax.scatter(x[-1], y[-1], s=28, marker="X", edgecolor="black", linewidth=0.4, zorder=3)
        ax.set_xlabel("Local east-west displacement (m)")
        ax.set_ylabel("Local north-south displacement (m)")
        ax.legend(title="Vehicle ID (suffix)", fontsize=7, title_fontsize=8, ncol=2, loc="best")
        ax.text(0.01, 0.01, "Circle = first fix   X = last fix", transform=ax.transAxes, fontsize=8)
    ax.set_title(f"Vehicle trajectories — {len(selected)} representative vehicles")
    ax.grid(alpha=0.2)
    fig.tight_layout()
    filename = "01_vehicle_trajectories.png"
    fig.savefig(outdir / filename, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return filename


def make_active_plot(outdir: Path, gps: dict) -> str:
    items = sorted(gps["per_minute"].items())
    times = [pd.Timestamp(t).to_pydatetime() for t, _ in items]
    counts = [len(ids) for _, ids in items]
    fig, ax = plt.subplots(figsize=(11, 5.5))
    if times:
        ax.plot(times, counts, marker="o", linewidth=1.8, markersize=4)
        ax.fill_between(times, counts, alpha=0.15)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    else:
        ax.text(0.5, 0.5, "No GPS activity data", ha="center", va="center", transform=ax.transAxes)
    ax.set_title("Active vehicles over time")
    ax.set_xlabel("Time (minute bins)")
    ax.set_ylabel("Distinct vehicles with at least one GPS fix")
    ax.grid(alpha=0.25)
    fig.autofmt_xdate()
    fig.tight_layout()
    filename = "02_active_vehicles_over_time.png"
    fig.savefig(outdir / filename, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return filename


def make_speed_time_plot(outdir: Path, states: dict) -> str:
    interval = states["interval_seconds"]
    rows = []
    for tb in sorted(states["bin_hist"]):
        hist = states["bin_hist"][tb]
        n = states["bin_count"][tb]
        if n == 0:
            continue
        center = pd.to_datetime(tb * interval, unit="s", utc=True).to_pydatetime()
        rows.append((center, states["bin_sum"][tb] / n,
                     quantile_from_hist(hist, 0.25), quantile_from_hist(hist, 0.50), quantile_from_hist(hist, 0.75), n))
    fig, ax = plt.subplots(figsize=(11, 5.7))
    if rows:
        x = [r[0] for r in rows]
        means = np.array([r[1] for r in rows])
        q25 = np.array([r[2] for r in rows])
        med = np.array([r[3] for r in rows])
        q75 = np.array([r[4] for r in rows])
        ax.plot(x, med, linewidth=2, label="Median speed (approx.)")
        ax.plot(x, means, linewidth=1.6, linestyle="--", label="Mean speed")
        ax.fill_between(x, q25, q75, alpha=0.2, label="25th–75th percentile")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        ax.legend()
    else:
        ax.text(0.5, 0.5, "No valid speeds in complete 10-second windows", ha="center", va="center", transform=ax.transAxes)
    ax.set_title(f"Vehicle speed over time ({interval}-second bins; complete 10-second windows)")
    ax.set_xlabel("Window start time")
    ax.set_ylabel("Speed (m/s)")
    ax.grid(alpha=0.25)
    fig.autofmt_xdate()
    fig.tight_layout()
    filename = "03_vehicle_speed_over_time.png"
    fig.savefig(outdir / filename, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return filename


def make_speed_distribution_plot(outdir: Path, states: dict) -> str:
    hist = states["global_hist"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5.2), gridspec_kw={"width_ratios": [2.3, 1]})
    total = int(hist.sum())
    regular = hist[:-1]
    centers = (SPEED_EDGES[:-1] + SPEED_EDGES[1:]) / 2
    ax1.bar(centers, regular, width=0.24, align="center", edgecolor="none")
    if hist[-1] > 0:
        ax1.text(0.98, 0.96, f">100 m/s: {int(hist[-1]):,}", transform=ax1.transAxes, ha="right", va="top", fontsize=9)
    ax1.set_title("Speed histogram")
    ax1.set_xlabel("Speed (m/s)")
    ax1.set_ylabel("Observations")
    ax1.grid(axis="y", alpha=0.2)
    q1, med, q3 = [quantile_from_hist(hist, q) for q in (0.25, 0.5, 0.75)]
    ax2.bxp([{"label": "All valid speeds", "med": med, "q1": q1, "q3": q3,
              "whislo": quantile_from_hist(hist, 0.05), "whishi": quantile_from_hist(hist, 0.95), "fliers": []}], showfliers=False)
    ax2.set_title("Box summary (5th–95th percentile whiskers)")
    ax2.set_ylabel("Speed (m/s)")
    ax2.grid(axis="y", alpha=0.2)
    mean = float(sum((centers[i] * regular[i]) for i in range(len(regular))) / max(1, total - int(hist[-1])))
    summary = f"N = {total:,}\nMean ≈ {mean:.2f} m/s\nMedian ≈ {med:.2f} m/s\nQ1–Q3 ≈ {q1:.2f}–{q3:.2f} m/s"
    fig.suptitle("Vehicle speed distribution — valid speeds from complete windows", y=1.02, fontsize=13)
    fig.text(0.5, -0.005, summary, ha="center", fontsize=9)
    fig.tight_layout()
    filename = "04_vehicle_speed_distribution.png"
    fig.savefig(outdir / filename, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return filename


def make_proximity_plot(outdir: Path, snapshot: pd.DataFrame, snapshot_time: pd.Timestamp,
                        radius_m: float, max_nodes: int, max_edges: int) -> Tuple[str, int, int]:
    fig, ax = plt.subplots(figsize=(11, 8))
    if snapshot.empty:
        ax.text(0.5, 0.5, "No fixes found at the selected snapshot second", ha="center", va="center", transform=ax.transAxes)
        ax.set_title("Vehicle proximity network")
        filename = "05_vehicle_proximity_network.png"
        fig.savefig(outdir / filename, dpi=180, bbox_inches="tight")
        plt.close(fig)
        return filename, 0, 0

    snap = snapshot.drop_duplicates("device_id").copy()
    lat = snap["latitude"].to_numpy(float)
    lon = snap["longitude"].to_numpy(float)
    lat0, lon0 = float(np.mean(lat)), float(np.mean(lon))
    x, y = local_xy(lat, lon, lat0, lon0)
    # Spatial hash avoids an extra SciPy dependency and only checks neighboring cells.
    cell_size = float(radius_m)
    cells: Dict[Tuple[int, int], List[int]] = defaultdict(list)
    edges: List[Tuple[int, int, float]] = []
    degree = np.zeros(len(snap), dtype=int)
    for i in range(len(snap)):
        cx, cy = math.floor(x[i] / cell_size), math.floor(y[i] / cell_size)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in cells.get((cx + dx, cy + dy), []):
                    d = math.hypot(x[i] - x[j], y[i] - y[j])
                    if d <= radius_m:
                        edges.append((j, i, d))
                        degree[i] += 1; degree[j] += 1
        cells[(cx, cy)].append(i)

    original_edge_count = len(edges)
    if len(snap) > max_nodes:
        chosen = set(np.argsort(degree)[-max_nodes:].tolist())
        # Retain the highest-degree vehicles; only draw edges induced by this set.
        edges = [e for e in edges if e[0] in chosen and e[1] in chosen]
        nodes = sorted(chosen)
        subtitle = f"Showing highest-degree {len(nodes)} of {len(snap)} vehicles"
    else:
        nodes = list(range(len(snap)))
        subtitle = f"{len(nodes)} vehicles in snapshot"
    if len(edges) > max_edges:
        edges = sorted(edges, key=lambda e: e[2])[:max_edges]
        subtitle += f"; nearest {max_edges:,} of {original_edge_count:,} links drawn"
    for i, j, d in edges:
        ax.plot([x[i], x[j]], [y[i], y[j]], linewidth=0.45, alpha=0.28, zorder=1)
    node_sizes = 15 + np.clip(degree[nodes], 0, 20) * 2
    ax.scatter(x[nodes], y[nodes], s=node_sizes, alpha=0.8, edgecolors="black", linewidths=0.25, zorder=2)
    ax.set_xlabel("Local east-west displacement (m)")
    ax.set_ylabel("Local north-south displacement (m)")
    ax.set_title(f"Vehicle proximity network — links within {radius_m:g} m\n{subtitle}")
    ax.grid(alpha=0.2)
    ax.text(0.01, 0.01, f"Snapshot: {snapshot_time.strftime('%Y-%m-%d %H:%M:%S UTC')}\nA link indicates spatial proximity only.", transform=ax.transAxes, fontsize=8)
    fig.tight_layout()
    filename = "05_vehicle_proximity_network.png"
    fig.savefig(outdir / filename, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return filename, len(snap), original_edge_count


def make_capability_heatmap(outdir: Path, states: dict, task_labels: dict, max_vehicles: int, max_task_bits: int) -> Tuple[str, pd.DataFrame]:
    masks = states["latest_mask"]
    vehicle_counts = states["vehicle_count"]
    bit_counts: Dict[int, int] = defaultdict(int)
    vehicle_bits: Dict[str, set] = {}
    for vid, mask in masks.items():
        bits = set_bits(mask)
        vehicle_bits[vid] = set(bits)
        for bit in bits:
            bit_counts[bit] += 1
    all_bits = sorted(bit_counts, key=lambda b: bit_counts[b], reverse=True)[:max_task_bits]
    all_bits = sorted(all_bits)
    ranked_vehicles = sorted(
        [vid for vid, bits in vehicle_bits.items() if bits],
        key=lambda vid: (vehicle_counts.get(vid, 0), len(vehicle_bits[vid])), reverse=True,
    )[:max_vehicles]

    fig, ax = plt.subplots(figsize=(max(9, len(all_bits) * 0.65), max(5, min(15, len(ranked_vehicles) * 0.16))))
    if not ranked_vehicles or not all_bits:
        ax.text(0.5, 0.5, "No non-zero task capability masks found.\nCheck vehicle registration and task/device bitmask configuration.",
                ha="center", va="center", transform=ax.transAxes, fontsize=11)
        ax.set_axis_off()
    else:
        matrix = np.zeros((len(ranked_vehicles), len(all_bits)), dtype=np.uint8)
        bit_to_col = {bit: i for i, bit in enumerate(all_bits)}
        for row, vid in enumerate(ranked_vehicles):
            for bit in vehicle_bits[vid]:
                if bit in bit_to_col:
                    matrix[row, bit_to_col[bit]] = 1
        ax.imshow(matrix, aspect="auto", interpolation="nearest", vmin=0, vmax=1)
        ax.set_xticks(np.arange(len(all_bits)))
        ax.set_xticklabels([task_labels.get(b, f"Task bit {b}") for b in all_bits], rotation=35, ha="right", fontsize=8)
        ax.set_yticks(np.arange(len(ranked_vehicles)))
        ax.set_yticklabels([v[-14:] for v in ranked_vehicles], fontsize=6)
        ax.set_xlabel("Task-type bit (set = vehicle is capability-compatible)")
        ax.set_ylabel("Vehicle ID (suffix)")
        ax.set_title(f"Vehicle × task-type capability heatmap — showing {len(ranked_vehicles)} vehicles")
        ax.set_xticks(np.arange(-0.5, len(all_bits), 1), minor=True)
        ax.set_yticks(np.arange(-0.5, len(ranked_vehicles), 1), minor=True)
        ax.grid(which="minor", color="white", linewidth=0.35, alpha=0.6)
        ax.tick_params(which="minor", bottom=False, left=False)
    fig.tight_layout()
    filename = "06_vehicle_task_capability_heatmap.png"
    fig.savefig(outdir / filename, dpi=180, bbox_inches="tight")
    plt.close(fig)

    coverage_rows = []
    total_vehicles = max(1, len(masks))
    for bit in sorted(bit_counts):
        coverage_rows.append({
            "task_bit_position": bit,
            "task_label": task_labels.get(bit, f"Task bit {bit}"),
            "vehicle_count": bit_counts[bit],
            "vehicle_percentage": 100.0 * bit_counts[bit] / total_vehicles,
        })
    coverage = pd.DataFrame(coverage_rows, columns=["task_bit_position", "task_label", "vehicle_count", "vehicle_percentage"])
    return filename, coverage


def make_bipartite_plot(outdir: Path, states: dict, task_labels: dict,
                        max_vehicles: int, max_task_bits: int) -> Tuple[str, int, int]:
    masks = states["latest_mask"]
    vehicle_counts = states["vehicle_count"]
    vehicle_bits = {vid: set_bits(mask) for vid, mask in masks.items()}
    bit_counts: Dict[int, int] = defaultdict(int)
    for bits in vehicle_bits.values():
        for bit in bits:
            bit_counts[bit] += 1
    selected_bits = sorted(bit_counts, key=lambda b: bit_counts[b], reverse=True)[:max_task_bits]
    selected_bits = sorted(selected_bits)
    selected_vehicles = sorted(
        [vid for vid, bits in vehicle_bits.items() if bits and any(b in selected_bits for b in bits)],
        key=lambda vid: (vehicle_counts.get(vid, 0), len(vehicle_bits[vid])), reverse=True,
    )[:max_vehicles]
    selected_set = set(selected_vehicles)
    edges = [(vid, bit) for vid in selected_vehicles for bit in vehicle_bits[vid] if bit in selected_bits]

    fig, ax = plt.subplots(figsize=(12, max(6, min(16, 0.24 * len(selected_vehicles) + 2))))
    if not selected_vehicles or not selected_bits:
        ax.text(0.5, 0.5, "No vehicle–task-type compatibility edges available.\nNo non-zero task capability masks were found.",
                ha="center", va="center", transform=ax.transAxes, fontsize=11)
        ax.set_axis_off()
    else:
        vehicle_y = {vid: i for i, vid in enumerate(selected_vehicles)}
        task_y = {bit: i for i, bit in enumerate(selected_bits)}
        # Draw links first so nodes stay on top.
        for vid, bit in edges:
            ax.plot([0, 1], [vehicle_y[vid], task_y[bit]], color="0.55", alpha=0.35, linewidth=0.75, zorder=1)
        ax.scatter(np.zeros(len(selected_vehicles)), [vehicle_y[v] for v in selected_vehicles], s=25, zorder=3, label="Vehicle")
        ax.scatter(np.ones(len(selected_bits)), [task_y[b] for b in selected_bits], s=95, marker="s", zorder=3, label="Task type")
        ax.set_xlim(-0.2, 1.2)
        ax.set_xticks([0, 1]); ax.set_xticklabels(["Vehicles", "Task types"])
        ax.set_yticks([])
        for vid, yy in vehicle_y.items():
            ax.text(-0.025, yy, vid[-15:], ha="right", va="center", fontsize=6.4)
        for bit, yy in task_y.items():
            ax.text(1.025, yy, task_labels.get(bit, f"Task bit {bit}"), ha="left", va="center", fontsize=8)
        ax.set_ylim(-1, max(len(selected_vehicles), len(selected_bits)))
        ax.invert_yaxis()
        ax.set_title(f"Vehicle–task-type bipartite compatibility graph\n{len(selected_vehicles)} vehicles, {len(selected_bits)} task types, {len(edges)} compatibility links")
        ax.legend(loc="lower center", ncol=2, frameon=False)
        ax.text(0.5, 0.015, "An edge means the vehicle capability bitmask includes that task type; this is not an assignment result.",
                ha="center", va="bottom", transform=ax.transAxes, fontsize=8)
    fig.tight_layout()
    filename = "07_vehicle_task_type_bipartite_compatibility.png"
    fig.savefig(outdir / filename, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return filename, len(selected_vehicles), len(edges)


def write_summary(outdir: Path, gps_path: Path, states_path: Path, gps: dict, states: dict,
                  proximity_info: tuple, coverage: pd.DataFrame, files: list) -> None:
    proximity_nodes, proximity_edges = proximity_info
    hist = states["global_hist"]
    n = int(hist.sum())
    mean = float(np.sum(((SPEED_EDGES[:-1] + SPEED_EDGES[1:]) / 2) * hist[:-1]) / max(1, n - int(hist[-1])))
    lines = [
        "GRAPH GENERATION SUMMARY", "=" * 80,
        f"GPS source: {gps_path}",
        f"Vehicle-state source: {states_path}",
        "",
        f"GPS rows read: {gps['total_rows']:,}",
        f"Valid GPS vehicles: {gps['vehicle_count']:,}",
        f"Invalid GPS rows skipped: {gps['bad_rows']:,}",
        f"GPS time range: {gps['min_time']} to {gps['max_time']}",
        f"GPS snapshot selected: {gps['snapshot_second']} ({gps['snapshot_record_count']:,} records)",
        "",
        f"Vehicle-state rows read: {states['total_rows']:,}",
        f"Vehicles in state file: {states['vehicle_count_unique']:,}",
        f"State time range: {states['min_time']} to {states['max_time']}",
        f"Complete 10-second windows: {states['full_window_count']:,}",
        f"Other/partial windows: {states['partial_window_count']:,}",
        f"Valid full-window speeds: {states['valid_speed_count']:,}",
        f"Invalid/missing full-window speeds: {states['invalid_speed_count']:,}",
        f"Approximate mean speed: {mean:.3f} m/s",
        f"Approximate median speed: {quantile_from_hist(hist, 0.5):.3f} m/s",
        "",
        f"Vehicles shown in proximity plot: {proximity_nodes:,}",
        f"Proximity pairs within threshold (before plot sampling): {proximity_edges:,}",
        f"Vehicles with at least one current capability bit: {int((coverage['vehicle_count'].sum()) if not coverage.empty else 0):,} bit memberships (not unique vehicles)",
        "",
        "Interpretation notes:",
        "- Trajectory plot shows a configurable sample chosen by largest first-to-last displacement, not every vehicle.",
        "- Active vehicle counts are distinct GPS device IDs per minute; they do not imply task availability.",
        "- Speed plots use complete 10-second windows only. Time-series quantiles are histogram approximations.",
        "- Proximity links mean two vehicles were within the configured distance at one selected GPS snapshot only.",
        "- Capability heatmap and bipartite edges are decoded from task_capable_bit_integer.",
        "- Without a task-label file, task types are named by bit position (Task bit N).",
        "- The bipartite graph is capability compatibility, NOT actual task allocation/assignment.",
        "- No road-network basemap or real task-instance locations are inferred.",
        "",
        "Generated figures:",
    ] + [f"- {name}" for name in files]
    (outdir / "GRAPH_GENERATION_SUMMARY.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate all seven vehicle/task visualizations.")
    parser.add_argument("--gps", help="Path to received_gps.csv (auto-detected when omitted)")
    parser.add_argument("--states", help="Path to vehicle_states.csv (auto-detected when omitted)")
    parser.add_argument("--output", default="vehicle_graphs", help="Output directory (default: vehicle_graphs)")
    parser.add_argument("--task-labels", help="Optional CSV mapping task_bit_position to task_name")
    parser.add_argument("--chunksize", type=int, default=400_000, help="CSV rows per processing chunk")
    parser.add_argument("--interval-seconds", type=int, default=30, help="Speed trend aggregation interval")
    parser.add_argument("--proximity-m", type=float, default=100.0, help="Vehicle proximity link threshold in metres")
    parser.add_argument("--trajectory-count", type=int, default=15, help="Number of representative trajectories")
    parser.add_argument("--heatmap-vehicles", type=int, default=150, help="Maximum vehicle rows in capability heatmap")
    parser.add_argument("--heatmap-task-bits", type=int, default=20, help="Maximum task-bit columns in capability plots")
    parser.add_argument("--bipartite-vehicles", type=int, default=45, help="Maximum vehicle nodes in bipartite graph")
    parser.add_argument("--network-max-nodes", type=int, default=400, help="Maximum nodes drawn in proximity network")
    parser.add_argument("--network-max-edges", type=int, default=5000, help="Maximum proximity links drawn")
    args = parser.parse_args()

    if args.chunksize < 1000 or args.interval_seconds <= 0 or args.proximity_m <= 0:
        parser.error("chunksize must be >=1000; interval-seconds and proximity-m must be >0")

    root = Path.cwd().resolve()
    try:
        gps_path = resolve_input(args.gps, ["received_gps.csv"], root)
        states_path = resolve_input(args.states, ["vehicle_states.csv"], root)
        task_labels = load_task_labels(args.task_labels)
        outdir = Path(args.output).expanduser().resolve()
        outdir.mkdir(parents=True, exist_ok=True)

        say(f"GPS input: {gps_path}")
        say(f"States input: {states_path}")
        say("Step 1/5: scanning raw GPS data...")
        gps = summarize_gps(gps_path, args.chunksize)
        say(f"  Found {gps['vehicle_count']:,} GPS vehicles across {gps['total_rows']:,} rows.")

        # Determine trajectory sample after first pass, then read only those paths and one snapshot.
        selected_ids = {vid for vid, _, _ in gps["path_scores"][:args.trajectory_count]}
        say("Step 2/5: extracting selected trajectories and proximity snapshot...")
        path_frames, snapshot = collect_selected_gps(gps_path, selected_ids, gps["snapshot_second"], args.chunksize)

        say("Step 3/5: scanning processed vehicle states...")
        states = summarize_states(states_path, args.chunksize, args.interval_seconds)
        say(f"  Found {states['vehicle_count_unique']:,} state vehicles and {states['valid_speed_count']:,} valid speed observations.")

        say("Step 4/5: generating figures...")
        files = []
        files.append(make_trajectory_plot(outdir, path_frames, gps["path_scores"], args.trajectory_count))
        files.append(make_active_plot(outdir, gps))
        files.append(make_speed_time_plot(outdir, states))
        files.append(make_speed_distribution_plot(outdir, states))
        prox_file, prox_nodes, prox_edges = make_proximity_plot(
            outdir, snapshot, gps["snapshot_second"], args.proximity_m,
            args.network_max_nodes, args.network_max_edges,
        )
        files.append(prox_file)
        heat_file, coverage = make_capability_heatmap(
            outdir, states, task_labels, args.heatmap_vehicles, args.heatmap_task_bits,
        )
        files.append(heat_file)
        bip_file, bip_vehicles, bip_edges = make_bipartite_plot(
            outdir, states, task_labels, args.bipartite_vehicles, args.heatmap_task_bits,
        )
        files.append(bip_file)

        coverage.to_csv(outdir / "task_capability_coverage.csv", index=False)
        write_summary(outdir, gps_path, states_path, gps, states, (prox_nodes, prox_edges), coverage, files)

        say("Step 5/5: complete.")
        say(f"Created {len(files)} graphs in: {outdir}")
        for name in files:
            say(f"  - {name}")
        say(f"  - GRAPH_GENERATION_SUMMARY.txt")
        say(f"  - task_capability_coverage.csv")
        if int(states["global_hist"].sum()) == 0:
            say("WARNING: no valid speed observations in complete 10-second windows; speed charts will be empty.")
        if coverage.empty:
            say("WARNING: all vehicle task-capability masks are zero; heatmap/bipartite graphs contain an explanatory note.")
        elif bip_edges == 0:
            say("WARNING: capability bits exist but no bipartite compatibility edges were drawn; check bitmask values.")
        return 0
    except Exception as exc:
        say(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
