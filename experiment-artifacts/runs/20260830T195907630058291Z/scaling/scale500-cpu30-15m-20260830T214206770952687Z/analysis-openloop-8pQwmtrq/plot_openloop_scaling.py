#!/usr/bin/env python3
"""Plot one NoSQLMark open-loop run without overwriting any output."""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parent
BIN_SECONDS = 10
PLOT_PATH = ROOT / "openloop_scaling_timeseries.png"
BIN_CSV_PATH = ROOT / "timeseries_10s.csv"
PHASE_CSV_PATH = ROOT / "phase_summary.csv"
SUMMARY_PATH = ROOT / "analysis_summary.txt"


def refuse_overwrite(*paths: Path) -> None:
    existing = [str(path) for path in paths if path.exists()]
    if existing:
        raise SystemExit("Refusing to overwrite existing output: " + ", ".join(existing))


def parse_timestamp(value: str) -> datetime:
    if "T" in value:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S,%f").replace(
        tzinfo=timezone.utc
    )


def percentile(values: np.ndarray, q: float) -> float:
    return float(np.percentile(values, q)) if len(values) else float("nan")


result_dirs = [path.parent for path in ROOT.glob("results/*/summary.json")]
if len(result_dirs) != 1:
    raise SystemExit(f"Expected one result directory, found {len(result_dirs)}")
result_dir = result_dirs[0]
summary = json.loads((result_dir / "summary.json").read_text())
workload = json.loads((result_dir / "workload.json").read_text())

target = float(workload["target"])
warmup_count = int(workload["counts"]["warmupcount"])
operation_count = int(workload["counts"]["operationcount"])
warmup_seconds = warmup_count / target
measurement_seconds = operation_count / target

backend_text = (ROOT / "console-logs" / "backbench-console.log").read_text(
    errors="replace"
)
start_match = re.search(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}).*"
    r"start with benchmarking \(warmupcount:",
    backend_text,
)
done_match = re.search(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}).*"
    r"from Working to WorkIsDone",
    backend_text,
)
if not start_match or not done_match:
    raise SystemExit("Could not recover worker start/end timestamps")

worker_start = parse_timestamp(start_match.group(1))
worker_done = parse_timestamp(done_match.group(1))
expected_measurement_start = worker_start + timedelta(seconds=warmup_seconds)

completion_times: list[datetime] = []
offset_ms: list[float] = []
latency_ms: list[float] = []
series_paths = [
    ROOT / "timeseries-20260830_212642.0.log",
    ROOT / "timeseries.log",
]
for series_path in series_paths:
    with series_path.open(errors="replace") as handle:
        for line in handle:
            fields = line.rstrip().split(";")
            if len(fields) != 4:
                continue
            try:
                completion = parse_timestamp(fields[0])
                offset = float(fields[1])
                latency = float(fields[3])
            except ValueError:
                continue
            # This pod contains older jobs too. The worker timestamps isolate this job.
            if expected_measurement_start <= completion <= worker_done + timedelta(
                seconds=1
            ):
                completion_times.append(completion)
                offset_ms.append(offset)
                latency_ms.append(latency)

if not completion_times:
    raise SystemExit("No time-series rows matched this job")

offsets = np.asarray(offset_ms, dtype=float)
latencies = np.asarray(latency_ms, dtype=float)
completion_epoch = np.asarray([value.timestamp() for value in completion_times])

# Each row is: completion wall time; scheduled release offset; operation; latency.
# Estimating the origin per row avoids relying on millisecond-rounded log timestamps.
origin_candidates = completion_epoch - (offsets + latencies) / 1000.0
origin_epoch = float(np.median(origin_candidates))
origin = datetime.fromtimestamp(origin_epoch, tz=timezone.utc)
release_epoch = origin_epoch + offsets / 1000.0
measurement_end_epoch = origin_epoch + measurement_seconds

valid = (
    (release_epoch >= origin_epoch)
    & (release_epoch < measurement_end_epoch)
    & np.isfinite(latencies)
)
release_epoch = release_epoch[valid]
completion_epoch = completion_epoch[valid]
latencies = latencies[valid]
offsets = offsets[valid]

controller_text = (ROOT / "controller.log").read_text(errors="replace")
controller_samples: list[tuple[datetime, float, float, int]] = []
for line in controller_text.splitlines():
    timestamp_match = re.match(r"(\S+Z)", line)
    cpu_match = re.search(r'"cpu": ([0-9.eE+-]+)', line)
    io_match = re.search(r'"iowait": ([0-9.eE+-]+)', line)
    shard_match = re.search(r'"shards": (\d+)', line)
    if timestamp_match and cpu_match and io_match and shard_match:
        controller_samples.append(
            (
                parse_timestamp(timestamp_match.group(1)),
                float(cpu_match.group(1)),
                float(io_match.group(1)),
                int(shard_match.group(1)),
            )
        )


def event_time(pattern: str) -> datetime:
    match = re.search(rf"(?m)^(\S+Z).*{pattern}", controller_text)
    if not match:
        raise SystemExit(f"Missing controller event: {pattern}")
    return parse_timestamp(match.group(1))


scale_decision = event_time(r"Scaling up shards")
add_shard_complete = event_time(r"addShard Job completed successfully")
resharding_complete = event_time(r"Resharding completed")

bin_width = float(BIN_SECONDS)
bin_count = int(np.ceil(measurement_seconds / bin_width))
bin_starts = np.arange(bin_count, dtype=float) * bin_width
bin_centers_epoch = origin_epoch + bin_starts + bin_width / 2.0
bin_centers_dt = [
    datetime.fromtimestamp(value, tz=timezone.utc) for value in bin_centers_epoch
]

release_bins = np.floor((release_epoch - origin_epoch) / bin_width).astype(int)
completion_bins = np.floor((completion_epoch - origin_epoch) / bin_width).astype(int)
offered_counts = np.bincount(release_bins, minlength=bin_count)[:bin_count]
in_range_completion = (completion_bins >= 0) & (completion_bins < bin_count)
completion_counts = np.bincount(
    completion_bins[in_range_completion], minlength=bin_count
)[:bin_count]

mean_latency = np.full(bin_count, np.nan)
p50_latency = np.full(bin_count, np.nan)
p95_latency = np.full(bin_count, np.nan)
p99_latency = np.full(bin_count, np.nan)
max_latency = np.full(bin_count, np.nan)
for index in range(bin_count):
    values = latencies[release_bins == index]
    if len(values):
        mean_latency[index] = float(np.mean(values))
        p50_latency[index] = percentile(values, 50)
        p95_latency[index] = percentile(values, 95)
        p99_latency[index] = percentile(values, 99)
        max_latency[index] = float(np.max(values))

with BIN_CSV_PATH.open("x", newline="") as handle:
    writer = csv.writer(handle)
    writer.writerow(
        [
            "bin_start_utc",
            "bin_end_utc",
            "offered_ops_per_sec",
            "completed_ops_per_sec",
            "latency_mean_ms",
            "latency_p50_ms",
            "latency_p95_ms",
            "latency_p99_ms",
            "latency_max_ms",
        ]
    )
    for index in range(bin_count):
        start = datetime.fromtimestamp(
            origin_epoch + bin_starts[index], tz=timezone.utc
        )
        end = start + timedelta(seconds=bin_width)
        writer.writerow(
            [
                start.isoformat(),
                end.isoformat(),
                offered_counts[index] / bin_width,
                completion_counts[index] / bin_width,
                mean_latency[index],
                p50_latency[index],
                p95_latency[index],
                p99_latency[index],
                max_latency[index],
            ]
        )

phases = [
    ("Pre-scale (1 shard)", origin, scale_decision),
    ("Scaling 1 to 2", scale_decision, resharding_complete),
    (
        "Post-scale (2 shards)",
        resharding_complete,
        datetime.fromtimestamp(measurement_end_epoch, tz=timezone.utc),
    ),
]
phase_rows: list[dict[str, float | int | str]] = []
with PHASE_CSV_PATH.open("x", newline="") as handle:
    fieldnames = [
        "phase",
        "start_utc",
        "end_utc",
        "duration_seconds",
        "count",
        "mean_ms",
        "p50_ms",
        "p95_ms",
        "p99_ms",
        "p99_9_ms",
        "max_ms",
    ]
    writer = csv.DictWriter(handle, fieldnames=fieldnames)
    writer.writeheader()
    for name, start, end in phases:
        mask = (release_epoch >= start.timestamp()) & (release_epoch < end.timestamp())
        values = latencies[mask]
        row = {
            "phase": name,
            "start_utc": start.isoformat(),
            "end_utc": end.isoformat(),
            "duration_seconds": (end - start).total_seconds(),
            "count": len(values),
            "mean_ms": float(np.mean(values)),
            "p50_ms": percentile(values, 50),
            "p95_ms": percentile(values, 95),
            "p99_ms": percentile(values, 99),
            "p99_9_ms": percentile(values, 99.9),
            "max_ms": float(np.max(values)),
        }
        phase_rows.append(row)
        writer.writerow(row)

fig, axes = plt.subplots(
    3,
    1,
    figsize=(15, 11),
    sharex=True,
    gridspec_kw={"height_ratios": [1.0, 1.35, 1.0]},
)
fig.suptitle(
    "Mambo open-loop MongoDB experiment: 500 offered reads/s, shards 1 to 2",
    fontsize=16,
)

event_specs = [
    (scale_decision, "Scale decision", "#d62728", "--"),
    (add_shard_complete, "addShard complete", "#ff7f0e", ":"),
    (resharding_complete, "Resharding complete", "#2ca02c", "--"),
]
for axis in axes:
    axis.axvspan(
        scale_decision,
        resharding_complete,
        color="#f4a261",
        alpha=0.13,
        label="_nolegend_",
    )
    for timestamp, _label, color, style in event_specs:
        axis.axvline(timestamp, color=color, linestyle=style, linewidth=1.6)
    axis.grid(True, alpha=0.25)

axes[0].plot(
    bin_centers_dt,
    offered_counts / bin_width,
    label="Offered rate",
    color="#1f77b4",
    linewidth=1.7,
)
axes[0].plot(
    bin_centers_dt,
    completion_counts / bin_width,
    label="Completion rate",
    color="#9467bd",
    linewidth=1.3,
    alpha=0.9,
)
axes[0].axhline(target, color="black", linestyle=":", linewidth=1, label="500 target")
axes[0].set_ylabel("Operations/s")
axes[0].legend(loc="lower right", ncol=3)

axes[1].plot(bin_centers_dt, p50_latency, label="p50", linewidth=1.4)
axes[1].plot(bin_centers_dt, p95_latency, label="p95", linewidth=1.4)
axes[1].plot(bin_centers_dt, p99_latency, label="p99", linewidth=1.7)
axes[1].plot(
    bin_centers_dt,
    mean_latency,
    label="mean",
    linewidth=1.1,
    linestyle="--",
    color="#555555",
)
axes[1].set_yscale("log")
axes[1].set_ylabel("Read latency (ms, log scale)")
axes[1].legend(loc="upper right", ncol=4)

sample_times = [row[0] for row in controller_samples]
cpu_values = [row[1] for row in controller_samples]
io_values = [row[2] for row in controller_samples]
axes[2].plot(sample_times, cpu_values, marker="o", label="Mongod CPU", color="#d62728")
axes[2].plot(sample_times, io_values, marker="o", label="Node I/O wait", color="#17becf")
axes[2].axhline(20, color="#d62728", linestyle=":", linewidth=1, label="CPU target 20%")
axes[2].axhline(
    30,
    color="#d62728",
    linestyle="--",
    linewidth=1,
    alpha=0.8,
    label="CPU scale-up threshold 30%",
)
axes[2].set_ylabel("Controller metric (%)")
axes[2].set_xlabel("Time (UTC)")
axes[2].legend(loc="upper right", ncol=2)

for timestamp, label, color, _style in event_specs:
    axes[0].annotate(
        f"{label}\n{timestamp:%H:%M:%S}",
        xy=(timestamp, axes[0].get_ylim()[1]),
        xytext=(3, -5),
        textcoords="offset points",
        rotation=90,
        va="top",
        ha="left",
        color=color,
        fontsize=9,
    )

axes[2].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=timezone.utc))
axes[2].xaxis.set_major_locator(mdates.MinuteLocator(interval=2))
fig.text(
    0.01,
    0.01,
    (
        f"Measured interval {origin:%Y-%m-%d %H:%M:%S}–"
        f"{datetime.fromtimestamp(measurement_end_epoch, tz=timezone.utc):%H:%M:%S} UTC. "
        f"10 s bins; {len(latencies):,}/{operation_count:,} operations present in "
        "time-series logs. Controller CPU is a 5-minute-window metric."
    ),
    fontsize=9,
)
fig.tight_layout(rect=(0, 0.035, 1, 0.965))
fig.savefig(PLOT_PATH, dpi=180)
plt.close(fig)

queue_errors = backend_text.count("MongoWaitQueueFullException")
with SUMMARY_PATH.open("x") as handle:
    handle.write(f"Job ID: {summary['JobID']}\n")
    handle.write(f"Summary throughput: {summary['Overall']['Throughput(ops/sec)']} ops/s\n")
    handle.write(f"Summary measured count: {summary['Read']['Count']}\n")
    handle.write(f"Time-series rows used: {len(latencies)}\n")
    handle.write(f"Time-series origin: {origin.isoformat()}\n")
    handle.write(f"Scale decision: {scale_decision.isoformat()}\n")
    handle.write(f"addShard complete: {add_shard_complete.isoformat()}\n")
    handle.write(f"Resharding complete: {resharding_complete.isoformat()}\n")
    handle.write(f"MongoWaitQueueFullException log lines: {queue_errors}\n")
    handle.write("\nPhase latency summaries (ms):\n")
    for row in phase_rows:
        handle.write(
            f"- {row['phase']}: n={row['count']}, mean={row['mean_ms']:.3f}, "
            f"p50={row['p50_ms']:.3f}, p95={row['p95_ms']:.3f}, "
            f"p99={row['p99_ms']:.3f}, p99.9={row['p99_9_ms']:.3f}, "
            f"max={row['max_ms']:.3f}\n"
        )

print(PLOT_PATH)
print(BIN_CSV_PATH)
print(PHASE_CSV_PATH)
print(SUMMARY_PATH)
