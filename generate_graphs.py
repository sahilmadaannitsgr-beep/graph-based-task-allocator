#!/usr/bin/env python3
"""Generate five project figures from raw GPS and processed vehicle-state CSVs.

Usage:
  python generate_graphs.py received_gps.csv vehicle_states.csv --output-dir figures

Dependencies: pandas, numpy, matplotlib, scipy
"""
from __future__ import annotations
import argparse
from pathlib import Path
import textwrap
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.spatial import cKDTree
from matplotlib.lines import Line2D
from matplotlib.ticker import MaxNLocator
from mpl_toolkits.axes_grid1.inset_locator import inset_axes


def local_xy(latitude, longitude, lat_origin=None, lon_origin=None):
    """Approximate local metric coordinates; suitable for a compact city area."""
    lat = np.asarray(latitude, dtype=float)
    lon = np.asarray(longitude, dtype=float)
    if lat_origin is None:
        lat_origin = float(np.nanmean(lat))
    if lon_origin is None:
        lon_origin = float(np.nanmean(lon))
    east_m = (lon - lon_origin) * 111_320.0 * np.cos(np.deg2rad(lat_origin))
    north_m = (lat - lat_origin) * 110_540.0
    return east_m, north_m, lat_origin, lon_origin


def read_inputs(gps_path: Path, states_path: Path):
    gps = pd.read_csv(gps_path)
    states = pd.read_csv(states_path)
    required_gps = {"device_id", "timestamp", "latitude", "longitude"}
    required_states = {
        "vehicle_id", "window_start_timestamp", "window_duration_s",
        "velocity_mps", "direction_degrees", "task_capable_bit_integer",
    }
    missing_gps = required_gps - set(gps.columns)
    missing_states = required_states - set(states.columns)
    if missing_gps:
        raise ValueError(f"GPS CSV is missing columns: {sorted(missing_gps)}")
    if missing_states:
        raise ValueError(f"Vehicle-state CSV is missing columns: {sorted(missing_states)}")

    gps["timestamp_dt"] = pd.to_datetime(gps["timestamp"], errors="coerce")
    states["timestamp_dt"] = pd.to_datetime(states["window_start_timestamp"], errors="coerce")
    for col in ["latitude", "longitude"]:
        gps[col] = pd.to_numeric(gps[col], errors="coerce")
    for col in ["velocity_mps", "window_duration_s"]:
        states[col] = pd.to_numeric(states[col], errors="coerce")
    gps = gps.dropna(subset=["device_id", "timestamp_dt", "latitude", "longitude"]).copy()
    states = states.dropna(subset=["vehicle_id", "timestamp_dt"]).copy()
    return gps, states


def save_trajectory_plot(gps: pd.DataFrame, out: Path):
    # Choose the 12 tracks with the greatest endpoint displacement for a readable movement plot.
    east_all, north_all, _, _ = local_xy(gps.latitude, gps.longitude)
    temp = gps[["device_id", "timestamp_dt"]].copy()
    temp["east_m"] = east_all
    temp["north_m"] = north_all
    temp = temp.sort_values(["device_id", "timestamp_dt"])
    first = temp.groupby("device_id", sort=False).first()
    last = temp.groupby("device_id", sort=False).last()
    displacement = np.hypot(last.east_m - first.east_m, last.north_m - first.north_m)
    chosen = displacement.sort_values(ascending=False).head(min(12, len(displacement))).index.to_list()

    sub = gps[gps.device_id.isin(chosen)].copy()
    east, north, _, _ = local_xy(sub.latitude, sub.longitude)
    sub["east_km"] = east / 1000.0
    sub["north_km"] = north / 1000.0

    fig, ax = plt.subplots(figsize=(11.5, 8.0), constrained_layout=True)
    palette = plt.get_cmap("tab20")
    for i, vid in enumerate(chosen):
        d = sub[sub.device_id == vid].sort_values("timestamp_dt")
        color = palette(i % 20)
        ax.plot(d.east_km, d.north_km, color=color, linewidth=1.6, alpha=0.9,
                label=f"…{str(vid)[-6:]}")
        ax.scatter(d.east_km.iloc[0], d.north_km.iloc[0], marker="o", s=38,
                   color=color, edgecolor="white", linewidth=0.6, zorder=4)
        ax.scatter(d.east_km.iloc[-1], d.north_km.iloc[-1], marker="X", s=46,
                   color=color, edgecolor="white", linewidth=0.5, zorder=5)
    ax.set_title("Sample Vehicle Trajectories", loc="left", fontsize=17, weight="bold", pad=14)
    ax.text(0, 1.01,
            f"12 of {gps.device_id.nunique():,} vehicles shown · selected by largest GPS endpoint displacement · start ○  end ×",
            transform=ax.transAxes, fontsize=9.5, color="#596579")
    ax.set_xlabel("Local east–west displacement (km)")
    ax.set_ylabel("Local north–south displacement (km)")
    ax.grid(True, color="#dfe4ea", linewidth=0.8, alpha=0.85)
    ax.set_aspect("equal", adjustable="datalim")
    ax.legend(title="Vehicle ID suffix", loc="upper left", bbox_to_anchor=(1.01, 1.0),
              frameon=False, fontsize=8, title_fontsize=9, ncol=1)
    fig.savefig(out, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return len(chosen)


def save_active_vehicles_plot(gps: pd.DataFrame, out: Path):
    counts = gps.groupby("timestamp_dt")["device_id"].nunique().sort_index()
    elapsed = (counts.index - counts.index.min()).total_seconds()
    fig, ax = plt.subplots(figsize=(11.5, 5.8), constrained_layout=True)
    ax.plot(elapsed, counts.values, linewidth=2.2, color="#2463a6")
    ax.fill_between(elapsed, counts.values, 0, color="#7eadd5", alpha=0.22)
    ax.scatter(elapsed, counts.values, s=13, color="#174a7e", zorder=3)
    ax.set_title("Active Vehicles Over Time", loc="left", fontsize=17, weight="bold", pad=14)
    ax.text(0, 1.02, "Distinct vehicle IDs with at least one GPS fix at each timestamp",
            transform=ax.transAxes, fontsize=9.5, color="#596579")
    ax.set_xlabel("Elapsed time from first GPS timestamp (seconds)")
    ax.set_ylabel("Active vehicles")
    ax.xaxis.set_major_locator(MaxNLocator(integer=True, nbins=10))
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.grid(True, color="#dfe4ea", linewidth=0.8)
    ax.set_ylim(bottom=0)
    fig.savefig(out, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return counts


def save_speed_time_plot(states: pd.DataFrame, out: Path):
    # Use only complete 10-second sliding windows, then pool into 10-second time bins.
    valid = states.dropna(subset=["velocity_mps"]).copy()
    valid = valid[(valid.velocity_mps >= 0) & (valid.window_duration_s == 10)].copy()
    if valid.empty:
        raise ValueError("No valid speed records from complete 10-second windows were found.")
    origin = valid.timestamp_dt.min()
    valid["elapsed_s"] = (valid.timestamp_dt - origin).dt.total_seconds()
    valid["time_bin_s"] = (valid.elapsed_s // 10) * 10
    grouped = valid.groupby("time_bin_s")["velocity_mps"]
    summary = grouped.agg(median="median", mean="mean",
                          q25=lambda x: x.quantile(0.25),
                          q75=lambda x: x.quantile(0.75), count="count").sort_index()
    elapsed = summary.index.to_numpy(dtype=float) + 5.0
    fig, ax = plt.subplots(figsize=(11.5, 5.8), constrained_layout=True)
    ax.fill_between(elapsed, summary["q25"].values, summary["q75"].values,
                    color="#86b7a4", alpha=0.28, label="Interquartile range (25th–75th percentile)")
    ax.plot(elapsed, summary["median"].values, color="#176b55", linewidth=2.2,
            marker="o", markersize=4, label="Median speed")
    ax.plot(elapsed, summary["mean"].values, color="#d17b29", linewidth=1.7,
            linestyle="--", marker="s", markersize=3.5, label="Mean speed")
    ax.set_title("Vehicle Speed Over Time", loc="left", fontsize=17, weight="bold", pad=14)
    ax.text(0, 1.02,
            f"Complete 10-second windows · aggregated into 10-second time bins · {len(valid):,} observations",
            transform=ax.transAxes, fontsize=9.5, color="#596579")
    ax.set_xlabel("Elapsed time from first full-window timestamp (seconds; bin midpoint shown)")
    ax.set_ylabel("Speed (m/s)")
    ax.xaxis.set_major_locator(MaxNLocator(integer=True, nbins=10))
    ax.grid(True, color="#dfe4ea", linewidth=0.8)
    ax.legend(frameon=False, loc="upper left", fontsize=9)
    ax.set_ylim(bottom=0)
    fig.savefig(out, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return valid, summary


def save_speed_distribution_plot(valid: pd.DataFrame, out: Path):
    speeds = valid.velocity_mps.to_numpy(dtype=float)
    median = float(np.median(speeds))
    mean = float(np.mean(speeds))
    p95 = float(np.quantile(speeds, 0.95))
    fig, (ax_hist, ax_box) = plt.subplots(
        2, 1, figsize=(11.5, 7.0), gridspec_kw={"height_ratios": [4, 1]}, constrained_layout=True
    )
    ax_hist.hist(speeds, bins=32, color="#4f86b5", edgecolor="white", linewidth=0.65)
    ax_hist.axvline(median, color="#176b55", linewidth=2, label=f"Median: {median:.2f} m/s")
    ax_hist.axvline(mean, color="#d17b29", linewidth=2, linestyle="--", label=f"Mean: {mean:.2f} m/s")
    ax_hist.axvline(0.5, color="#7b5aa6", linewidth=1.6, linestyle=":", label="Stationary threshold: 0.5 m/s")
    ax_hist.set_title("Vehicle Speed Distribution", loc="left", fontsize=17, weight="bold", pad=14)
    ax_hist.text(0, 1.02,
                 f"{len(speeds):,} valid processed-state speed observations · 95th percentile: {p95:.2f} m/s",
                 transform=ax_hist.transAxes, fontsize=9.5, color="#596579")
    ax_hist.set_ylabel("Number of observations")
    ax_hist.grid(True, axis="y", color="#dfe4ea", linewidth=0.8)
    ax_hist.legend(frameon=False, fontsize=9)
    ax_box.boxplot(speeds, vert=False, patch_artist=True,
                   boxprops={"facecolor": "#a8c7dd", "edgecolor": "#385f7c"},
                   medianprops={"color": "#176b55", "linewidth": 2},
                   whiskerprops={"color": "#385f7c"},
                   capprops={"color": "#385f7c"},
                   flierprops={"marker": ".", "markersize": 2, "alpha": 0.25,
                               "markerfacecolor": "#596579", "markeredgecolor": "none"})
    ax_box.set_xlabel("Speed (m/s)")
    ax_box.set_yticks([])
    ax_box.grid(True, axis="x", color="#dfe4ea", linewidth=0.8)
    fig.savefig(out, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def save_proximity_plot(gps: pd.DataFrame, out: Path, radius_m: float = 100.0):
    per_time = gps.groupby("timestamp_dt")["device_id"].nunique()
    snapshot_time = per_time.idxmax()
    snap = gps[gps.timestamp_dt == snapshot_time].drop_duplicates("device_id").copy().reset_index(drop=True)
    east_m, north_m, _, _ = local_xy(snap.latitude, snap.longitude)
    points = np.column_stack([east_m, north_m])
    tree = cKDTree(points)
    pairs = sorted(tree.query_pairs(r=radius_m))
    degree = np.zeros(len(snap), dtype=int)
    for i, j in pairs:
        degree[i] += 1
        degree[j] += 1

    fig, ax = plt.subplots(figsize=(10.8, 8.2), constrained_layout=True)
    for i, j in pairs:
        ax.plot([points[i, 0] / 1000, points[j, 0] / 1000],
                [points[i, 1] / 1000, points[j, 1] / 1000],
                color="#496b8a", alpha=0.62, linewidth=1.15, zorder=1)
    scatter = ax.scatter(points[:, 0] / 1000, points[:, 1] / 1000,
                         c=degree, cmap="viridis", s=28 + degree * 4,
                         edgecolors="white", linewidths=0.55, zorder=3)
    cbar = fig.colorbar(scatter, ax=ax, pad=0.02)
    cbar.set_label(f"Number of neighboring vehicles within {radius_m:.0f} m")
    ax.set_title("Vehicle Proximity Network", loc="left", fontsize=17, weight="bold", pad=14)
    ax.text(0, 1.02,
            f"Snapshot: {snapshot_time:%Y-%m-%d %H:%M:%S} · {len(snap):,} vehicles · {len(pairs):,} links at ≤{radius_m:.0f} m",
            transform=ax.transAxes, fontsize=9.5, color="#596579")
    ax.set_xlabel("Local east–west displacement (km)")
    ax.set_ylabel("Local north–south displacement (km)")
    ax.grid(True, color="#dfe4ea", linewidth=0.8)
    ax.set_aspect("equal", adjustable="datalim")

    # Add a zoomed inset around the highest-degree vehicle so short 100 m links are legible.
    center_i = int(np.argmax(degree))
    center_x, center_y = points[center_i] / 1000.0
    local_mask = ((points[:, 0] / 1000.0 - center_x) ** 2 +
                  (points[:, 1] / 1000.0 - center_y) ** 2) <= 0.65 ** 2
    inset = inset_axes(ax, width="43%", height="43%", loc="upper right", borderpad=1.3)
    for i, j in pairs:
        if local_mask[i] and local_mask[j]:
            inset.plot([points[i, 0] / 1000, points[j, 0] / 1000],
                       [points[i, 1] / 1000, points[j, 1] / 1000],
                       color="#496b8a", alpha=0.78, linewidth=1.25, zorder=1)
    inset.scatter(points[local_mask, 0] / 1000, points[local_mask, 1] / 1000,
                  c=degree[local_mask], cmap="viridis", vmin=degree.min(), vmax=max(1, degree.max()),
                  s=38 + degree[local_mask] * 5, edgecolors="white", linewidths=0.5, zorder=3)
    inset.set_xlim(center_x - 0.65, center_x + 0.65)
    inset.set_ylim(center_y - 0.65, center_y + 0.65)
    inset.set_title("Zoom near highest-degree vehicle", fontsize=8.5, pad=4)
    inset.tick_params(labelsize=7)
    inset.grid(True, color="#e5e9ef", linewidth=0.6)
    inset.set_facecolor("white")
    ax.text(0.01, 0.01,
            f"A link joins two vehicles whose GPS positions are no more than {radius_m:.0f} m apart.\n"
            "The inset zooms into a local cluster; node color/size represent neighbor count.",
            transform=ax.transAxes, fontsize=8.5, color="#596579", va="bottom",
            bbox={"boxstyle": "round,pad=0.45", "facecolor": "white", "edgecolor": "#dfe4ea", "alpha": 0.94})
    fig.savefig(out, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return snapshot_time, len(snap), len(pairs), degree


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gps_csv", type=Path)
    parser.add_argument("vehicle_states_csv", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("vehicle_graphs"))
    parser.add_argument("--proximity-radius-m", type=float, default=100.0)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "axes.titlesize": 15,
        "axes.labelsize": 10,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "savefig.facecolor": "white",
    })

    gps, states = read_inputs(args.gps_csv, args.vehicle_states_csv)
    n_traj = save_trajectory_plot(gps, args.output_dir / "01_vehicle_trajectories.png")
    active = save_active_vehicles_plot(gps, args.output_dir / "02_active_vehicles_over_time.png")
    valid, speed_summary = save_speed_time_plot(states, args.output_dir / "03_vehicle_speed_over_time.png")
    save_speed_distribution_plot(valid, args.output_dir / "04_vehicle_speed_distribution.png")
    snap_time, snap_nodes, snap_edges, degree = save_proximity_plot(
        gps, args.output_dir / "05_vehicle_proximity_network.png", args.proximity_radius_m
    )

    gps_start, gps_end = gps.timestamp_dt.min(), gps.timestamp_dt.max()
    st_start, st_end = states.timestamp_dt.min(), states.timestamp_dt.max()
    summary = f"""GRAPH GENERATION SUMMARY
=======================

Input data
- GPS records used: {len(gps):,}
- GPS vehicles: {gps.device_id.nunique():,}
- GPS time range: {gps_start} to {gps_end}
- Processed-state records: {len(states):,}
- Vehicles in processed-state file: {states.vehicle_id.nunique():,}
- Processed-state time range: {st_start} to {st_end}
- Valid speed observations: {len(valid):,}

Speed summary (complete 10-second windows)
- Mean speed: {valid.velocity_mps.mean():.3f} m/s
- Median speed: {valid.velocity_mps.median():.3f} m/s
- Share of observations below 0.5 m/s: {(valid.velocity_mps < 0.5).mean():.1%}

Figures created
1. 01_vehicle_trajectories.png — {n_traj} representative vehicles selected by largest endpoint displacement.
2. 02_active_vehicles_over_time.png — number of distinct GPS vehicle IDs observed at each raw-GPS timestamp.
3. 03_vehicle_speed_over_time.png — median and mean speed in 10-second bins with the 25th–75th percentile band; complete 10-second windows only.
4. 04_vehicle_speed_distribution.png — histogram and box plot of valid processed-state speeds.
5. 05_vehicle_proximity_network.png — {snap_nodes} vehicles at {snap_time}; links connect pairs within {args.proximity_radius_m:.0f} m ({snap_edges} links).

Data interpretation notes
- GPS coordinates are plotted in a local metric coordinate system centered on the data; no road-network basemap was supplied.
- Trajectories show the 12 vehicles with the largest observed endpoint displacement, not every vehicle, to keep the paths legible.
- Active-vehicle counts are based on GPS observations and do not imply task availability.
- Speed plots exclude missing speeds and partial warm-up windows; only records with window_duration_s = 10 are used. The time-series groups complete-window records into 10-second bins to avoid per-second differences caused by staggered vehicle emissions.
- The GPS feed continues beyond the last processed-state timestamp; speed plots therefore cover a shorter interval than GPS plots.
- The proximity graph is a spatial snapshot. A 100 m link means proximity at that instant, not communication, task compatibility, or an assignment edge. The inset magnifies a local cluster around the highest-degree vehicle.
"""
    (args.output_dir / "README_graphs.txt").write_text(summary, encoding="utf-8")
    print(summary)


if __name__ == "__main__":
    main()
