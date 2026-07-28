"""Plot one manual NoSQLMark async/sync Redis-pause comparison."""
from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

import matplotlib.pyplot as plt
import numpy as np


@dataclass(frozen=True)
class ModeData:
    mode: str
    release_ms: np.ndarray
    latency_ms: np.ndarray
    pause_release_ms: float
    summary: dict[str, float | int | str | bool]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plot a manual NoSQLMark open/closed Redis-pause run.")
    parser.add_argument("run_dir", type=Path, help="Directory containing open/closed timeseries and pause files.")
    parser.add_argument("--results-root", type=Path, default=Path("results"), help="NoSQLMark results directory (default: results).")
    parser.add_argument("--output-dir", type=Path, help="Output directory (default: RUN_DIR/output).")
    parser.add_argument("--bin-ms", type=float, default=100.0)
    parser.add_argument("--release-before-s", type=float, default=2.0)
    parser.add_argument("--release-after-s", type=float, default=3.0)
    parser.add_argument("--map-before-s", type=float, default=2.0)
    parser.add_argument("--map-after-s", type=float, default=3.0)
    parser.add_argument("--force", action="store_true", help="Allow existing output files to be replaced.")
    return parser.parse_args()


def read_pause_wall_ms(path: Path) -> float:
    lines = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    if len(lines) < 2 or lines[-1] != "OK":
        raise ValueError(f"{path} must contain a timestamp followed by Redis OK")
    return datetime.fromisoformat(lines[0]).timestamp() * 1000.0


def read_timeseries(path: Path) -> tuple[np.ndarray, np.ndarray, list[float]]:
    """
    Returns (release_ms, latency_ms, origins) from a timeseries log file.
    release_ms: scheduler release timestamps in milliseconds since epoch
    latency_ms: measured latencies in milliseconds
    origins: estimated origin timestamps in milliseconds since epoch
    """
    lines = path.read_text().splitlines()
    if not lines:
        raise ValueError(f"{path} is empty")

    releases, latencies, origins = [], [], []
    for line_num, line in enumerate(lines, start=1):
        try:
            # ignoring the operation name field (e.g. 'READ')
            comp_txt, rel_txt, _, lat_txt = line.split(";")
            comp = datetime.strptime(comp_txt, "%Y-%m-%d %H:%M:%S,%f").replace(tzinfo=timezone.utc)
            r, l = float(rel_txt), float(lat_txt)
            releases.append(r)
            latencies.append(l)
            origins.append((comp.timestamp() * 1000.0) - r - l)
        except ValueError:
            raise ValueError(f"{path}:{line_num}: expected four semicolon fields")

    order = np.argsort(releases)
    return np.array(releases)[order], np.array(latencies)[order], [origins[i] for i in order]


def find_result_files(results_root: Path, tag: str, mode: str) -> tuple[Path, Path]:
    batch_dir = results_root / f"{tag}-{mode}"
    summaries = sorted(batch_dir.glob("*/summary.json"))
    if len(summaries) != 1:
        raise ValueError(f"expected one summary under {batch_dir}, found {len(summaries)}")
    
    if not (workload_path := summaries[0].with_name("workload.json")).is_file():
        raise FileNotFoundError(workload_path)
    return summaries[0], workload_path


def count_category(summary: dict, suffix: str) -> int:
    return sum(int(float(v.get("Count", 0))) for k, v in summary.items() if k.endswith(suffix) and isinstance(v, dict))


def maximum_overlapping_gap(releases: np.ndarray, start: float, end: float) -> float:
    if len(releases) < 2:
        return math.nan
    left, right = releases[:-1], releases[1:]
    overlapping = (left <= end) & (right >= start)
    return float(np.max((right - left)[overlapping])) if np.any(overlapping) else math.nan


def load_mode(run_dir: Path, results_root: Path, tag: str, mode: str) -> ModeData:
    release_ms, latency_ms, origins = read_timeseries(run_dir / f"{mode}-timeseries.log")
    # obtain when the Redis pause was released by subtracting the median origin from the wall-clock pause timestamp
    pause_release_ms = read_pause_wall_ms(run_dir / f"{mode}-pause.txt") - median(origins)
    
    summary_path, workload_path = find_result_files(results_root, tag, mode)
    print(f"Loading {mode} summary from {summary_path} and workload from {workload_path}")
    summary_json, workload = json.loads(summary_path.read_text()), json.loads(workload_path.read_text())

    if not isinstance(all_metrics := summary_json.get("ALL"), dict):
        raise ValueError(f"{summary_path} does not contain an ALL category")

    # the pause is 1 second long, so the end of the pause is 1000 ms after the release
    pause_end_ms = pause_release_ms + 1000.0
    return ModeData(
        mode=mode, release_ms=release_ms, latency_ms=latency_ms, pause_release_ms=pause_release_ms,
        summary={
            "mode": mode,
            "job_id": str(summary_json.get("JobID", workload.get("jobID", ""))),
            "asyncmode": bool(workload["asyncmode"]),
            "target_ops_per_sec": float(workload["target"]),
            "count": int(float(all_metrics["Count"])),
            "failures": count_category(summary_json, "-FAILED"),
            "timeouts": count_category(summary_json, "-TIMEDOUT"),
            "runtime_ms": float(summary_json["Overall"]["RunTime(ms)"]),
            "throughput_ops_per_sec": float(summary_json["Overall"]["Throughput(ops/sec)"]),
            "mean_latency_ms": float(all_metrics["Mean(ms)"]),
            "p90_latency_ms": float(all_metrics["90Percentile(ms)"]),
            "p99_latency_ms": float(all_metrics["99Percentile(ms)"]),
            "p999_latency_ms": float(all_metrics["99.9Percentile(ms)"]),
            "max_latency_ms": float(all_metrics["MaxValue(ms)"]),
            "pause_release_ms": pause_release_ms,
            "pre_pause_release_rate_ops_per_sec": float(np.count_nonzero((release_ms >= pause_release_ms - 1000.0) & (release_ms < pause_release_ms))),
            "releases_during_pause": int(np.count_nonzero((release_ms >= pause_release_ms) & (release_ms < pause_end_ms))),
            "max_pause_overlapping_release_gap_ms": maximum_overlapping_gap(release_ms, pause_release_ms, pause_end_ms),
        }
    )


def release_rate_bins(data: ModeData, bin_ms: float, before_s: float, after_s: float) -> tuple[np.ndarray, np.ndarray]:
    """
    Returns (centers_s, rates_ops_per_sec) for the given ModeData.
    """
    # time bins e.g. [-2000, -1900, -1800, ..., 2900, 3000]
    edges = -before_s * 1000.0 + np.arange(int(math.ceil((before_s + after_s) * 1000.0 / bin_ms)) + 1) * bin_ms
    # putting into bins and normalizing by bin width to get ops/sec
    counts, _ = np.histogram(data.release_ms - data.pause_release_ms, bins=edges)
    return ((edges[:-1] + edges[1:]) / 2.0) / 1000.0, counts.astype(float) * (1000.0 / bin_ms)


def reserve_outputs(output_dir: Path, force: bool) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {"plot": output_dir / "comparison.png", "summary": output_dir / "summary.csv", "rates": output_dir / "release-rate.csv"}
    return outputs


def write_csvs(outputs: dict[str, Path], modes: list[ModeData], centers_s: np.ndarray, open_rates: np.ndarray, closed_rates: np.ndarray) -> None:
    with outputs["summary"].open("w", newline="") as h:
        writer = csv.DictWriter(h, fieldnames=list(modes[0].summary.keys()))
        writer.writeheader(); writer.writerows(m.summary for m in modes)
        
    with outputs["rates"].open("w", newline="") as h:
        writer = csv.writer(h)
        writer.writerow(["time_from_pause_start_s", "async_release_rate_ops_per_sec", "sync_release_rate_ops_per_sec"])
        writer.writerows(zip(centers_s, open_rates, closed_rates))


def plot_comparison(path: Path, tag: str, open_d: ModeData, closed_d: ModeData, centers_s: np.ndarray, open_r: np.ndarray, closed_r: np.ndarray, rb: float, ra: float, mb: float, ma: float) -> None:
    """
    Inputs:
        - path: output path for the plot PNG
        - tag: run tag for the plot title
        - open_d: ModeData for the asyncmode=true run
        - closed_d: ModeData for the asyncmode=false run
        - centers_s: time bin centers in seconds for the release-rate plot
        - open_r: release rates in ops/sec for the asyncmode=true run
        - closed_r: release rates in ops/sec for the asyncmode=false run
        - rb: seconds before pause to show in the release-rate plot
        - ra: seconds after pause to show in the release-rate plot
        - mb: seconds before pause to show in the scatter plot
        - ma: seconds after pause to show in the scatter plot
    """
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 9), constrained_layout=True)
    fig.suptitle(f"NoSQLMark Redis pause comparison", fontsize=15)

    def style_ax(ax, xmin, xmax, xlabel, ylabel, title):
        # shaded region for the Redis pause (0.0 to 1.0 seconds)
        ax.axvspan(0.0, 1.0, color="#8fd18f", alpha=0.22, label="Redis pause" if ax == ax1 else None)
        for x in (0.0, 1.0): ax.axvline(x, color="#8fd18f", linewidth=1.4)
        ax.set(xlim=(-xmin, xmax), ylim=(0, None), xlabel=xlabel, ylabel=ylabel, title=title)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="upper right")

    # Rate Plot
    ax1.step(centers_s, open_r, where="mid", color="#1f77b4", linewidth=1.8, label="asyncmode=true")
    ax1.step(centers_s, closed_r, where="mid", color="#e31a1c", linewidth=1.5, label="asyncmode=false")
    ax1.axhline(float(open_d.summary["target_ops_per_sec"]), color="black", linestyle="--", linewidth=1.0, label="target")
    style_ax(ax1, rb, ra, "time from pause start (s)", "scheduler release rate (ops/s)", "Operation release rate (100 ms bins)")

    # Scatter Plot
    configs = [(closed_d, "#e31a1c", "o", "sync: response-dependent", "none"), (open_d, "#1f77b4", "x", "async: response-independent", "#1f77b4")]
    for data, color, marker, label, facecolor in configs:
        aligned_s = (data.release_ms - data.pause_release_ms) / 1000.0
        sel = (aligned_s >= -mb) & (aligned_s <= ma)
        edge = color if marker == "o" else None
        c = None if marker == "o" else color
        ax2.scatter(aligned_s[sel], data.latency_ms[sel], s=22 if marker == "o" else 18, marker=marker, facecolors=facecolor, edgecolors=edge, color=c, linewidths=0.7, alpha=0.65, label=label)

    style_ax(ax2, mb, ma, "scheduler release time from pause start (s)", "latency (ms)", "Where individual latency measurements were taken")
    ax2.annotate("Redis paused for 1 s", xy=(0.5, 0.82), xycoords=("data", "axes fraction"), ha="center", color="#2e7d32")

    fig.savefig(path, dpi=180)
    plt.close(fig)


def main() -> None:
    args = parse_args()
    run_dir, results_root = args.run_dir.resolve(), args.results_root.resolve()
    output_dir = args.output_dir.resolve() if args.output_dir else run_dir / "output"
    print(f"Plotting manual run {run_dir.name} from {run_dir} with results root {results_root} to {output_dir}")

    open_data, closed_data = load_mode(run_dir, results_root, run_dir.name, "open"), load_mode(run_dir, results_root, run_dir.name, "closed")
    if open_data.summary["asyncmode"] is not True or closed_data.summary["asyncmode"] is not False:
        raise ValueError("Workloads do not have expected asyncmode booleans")

    outputs = reserve_outputs(output_dir, args.force)
    open_c, open_r = release_rate_bins(open_data, args.bin_ms, args.release_before_s, args.release_after_s)
    closed_c, closed_r = release_rate_bins(closed_data, args.bin_ms, args.release_before_s, args.release_after_s)
    
    if not np.array_equal(open_c, closed_c):
        raise AssertionError("open and closed release-rate bins do not align")

    write_csvs(outputs, [open_data, closed_data], open_c, open_r, closed_r)
    plot_comparison(outputs["plot"], run_dir.name, open_data, closed_data, open_c, open_r, closed_r, args.release_before_s, args.release_after_s, args.map_before_s, args.map_after_s)
    print(*outputs.values(), sep="\n")


if __name__ == "__main__":
    main()