#!/usr/bin/env python3
"""Plot one saved NoSQLMark open-loop scaling run.

Expected beside this script:
  controller.log
  console-logs/backbench-console.log
  results/*/workload.json
  timeseries*.log

Time-series rows must use this format:
  completion time;scheduled release offset (ms);operation;latency (ms)

Only openloop_scaling.png is generated.
"""

import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import fmean, median

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parent
BIN_SECONDS = 10
OUTPUT = ROOT / "openloop_scaling.png"


def parse_time(value: str) -> datetime:
    """Parse controller ISO timestamps and NoSQLMark log timestamps."""
    if "T" in value:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S,%f").replace(
        tzinfo=timezone.utc
    )


def backend_time(text: str, marker: str) -> datetime:
    match = re.search(
        rf"(\d{{4}}-\d{{2}}-\d{{2}} \d{{2}}:\d{{2}}:\d{{2}},\d{{3}})"
        rf".*{re.escape(marker)}",
        text,
    )
    if not match:
        raise SystemExit(f"Missing backend marker: {marker}")
    return parse_time(match.group(1))


def controller_time(text: str, marker: str) -> datetime:
    match = re.search(rf"(?m)^(\S+Z).*{re.escape(marker)}", text)
    if not match:
        raise SystemExit(f"Missing controller marker: {marker}")
    return parse_time(match.group(1))


workload_files = list(ROOT.glob("results/*/workload.json"))
workload = json.loads(workload_files[0].read_text())
target_rate = float(workload["target"])
warmup_seconds = int(workload["counts"]["warmupcount"]) / target_rate
measurement_seconds = int(workload["counts"]["operationcount"]) / target_rate

backend = (ROOT / "console-logs" / "backbench-console.log").read_text(
    errors="replace"
)
worker_start = backend_time(backend, "start with benchmarking (warmupcount:")
worker_done = backend_time(backend, "from Working to WorkIsDone")
measurement_floor = worker_start + timedelta(seconds=warmup_seconds)

# Isolate this job because the backend log files may also contain older jobs.
samples = []
file_path = sorted(ROOT.glob("timeseries*.log"))[0]
with file_path.open(errors="replace") as lines:
    for line in lines:
        fields = line.rstrip().split(";")
        if len(fields) != 4:
            continue
        try:
            completion = parse_time(fields[0])
            release_offset_ms = float(fields[1])
            latency_ms = float(fields[3])
        except ValueError:
            continue
        # make sure we are getting the right latency samples within the measurement window (from backend)
        if measurement_floor <= completion <= worker_done + timedelta(seconds=1):
            samples.append((completion, release_offset_ms, latency_ms))

# A row completes at origin + release offset + latency. Taking the median
# reconstructs the measurement origin despite millisecond-rounded timestamps.
origin_epoch = median(
    completion.timestamp() - (offset_ms + latency_ms) / 1000
    for completion, offset_ms, latency_ms in samples
)
origin = datetime.fromtimestamp(origin_epoch, tz=timezone.utc)
measurement_end = origin + timedelta(seconds=measurement_seconds)

bin_count = math.ceil(measurement_seconds / BIN_SECONDS)
latency_bins = [[] for _ in range(bin_count)]

for _completion, offset_ms, latency_ms in samples:
    release_seconds = offset_ms / 1000
    index = int(release_seconds // BIN_SECONDS)
    if 0 <= release_seconds < measurement_seconds and 0 <= index < bin_count:
        latency_bins[index].append(latency_ms)

times = [
    origin + timedelta(seconds=(index + 0.5) * BIN_SECONDS)
    for index in range(bin_count)
]
mean_latency = [fmean(values) if values else math.nan for values in latency_bins]

controller = (ROOT / "controller.log").read_text(errors="replace")
cpu_samples = []
for line in controller.splitlines():
    if '"controller": "mongodautoscaler"' not in line:
        continue
    timestamp_match = re.match(r"(\S+Z)", line)
    cpu_match = re.search(r'"cpu": ([0-9.eE+-]+)', line)
    if timestamp_match and cpu_match:
        timestamp = parse_time(timestamp_match.group(1))
        if origin <= timestamp <= measurement_end:
            cpu_samples.append((timestamp, float(cpu_match.group(1))))

events = [
    (
        controller_time(controller, "Scaling up shards"),
        "Scale 1 to 2",
        "tab:green",
    ),
    (
        controller_time(controller, "Resharding completed"),
        "Resharding complete",
        "tab:red",
    ),
]

fig, axes = plt.subplots(2, 1, figsize=(11, 6.5), sharex=True)
fig.suptitle("Open-loop YCSB & shard scaling", fontsize=15)

axes[0].plot(times, mean_latency, color="tab:orange", linewidth=1.7)
axes[0].set_ylabel("Mean latency\n(ms)")

cpu_times, cpu_values = zip(*cpu_samples)
axes[1].plot(cpu_times, cpu_values, color="tab:purple", marker="o", linewidth=1.7)
axes[1].set_ylabel("Mongod CPU\n(%)")
axes[1].set_xlabel("Time")

for axis in axes:
    for timestamp, label, color in events:
        axis.axvline(
            timestamp,
            color=color,
            linestyle="--",
            linewidth=1.4,
            label=label if axis is axes[0] else None,
        )
    axis.set_xlim(origin, measurement_end)
    axis.set_ylim(bottom=0)
    axis.grid(True, alpha=0.25)

axes[0].legend(loc="best", frameon=False)
axes[1].xaxis.set_major_locator(mdates.MinuteLocator(interval=2))
axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=timezone.utc))

fig.tight_layout(rect=(0, 0, 1, 0.96))
fig.savefig(OUTPUT, dpi=180)
plt.close(fig)

print(OUTPUT)
