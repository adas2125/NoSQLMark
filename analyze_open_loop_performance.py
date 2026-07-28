"""Compare one NoSQLMark MongoDB async/sync pause pair."""

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
import matplotlib.pyplot as plt


@dataclass(frozen=True)
class Record:
    completion_ms: float
    release_ms: float
    latency_ms: float


def parse_args():
    parser = argparse.ArgumentParser(description="Plot MongoDB async/sync pause results.")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("pair_tag")
    parser.add_argument("--timeseries", type=Path,
                        default=Path("backbench/logs/timeseries.log"))
    parser.add_argument("--results-root", type=Path, default=Path("results"))
    parser.add_argument("--bin-ms", type=float, default=100.0)
    return parser.parse_args()


def read_pause(path):
    timestamps = {}
    for line in path.read_text().splitlines():
        name, separator, timestamp = line.partition(" ")
        if separator and name in {"pause-start", "pause-end"}:
            timestamps[name] = datetime.fromisoformat(timestamp.strip()).timestamp() * 1000
    if set(timestamps) != {"pause-start", "pause-end"}:
        raise ValueError(f"invalid pause log: {path}")
    return timestamps["pause-start"], timestamps["pause-end"]


def read_timeseries(path):
    records = []
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split(";")
        if len(fields) != 4:
            raise ValueError(f"invalid line {line_number} in {path}")
        completion = datetime.strptime(fields[0], "%Y-%m-%d %H:%M:%S,%f")
        completion_ms = completion.replace(tzinfo=timezone.utc).timestamp() * 1000.0
        records.append(Record(completion_ms, float(fields[1]), float(fields[3])))
    return records


def estimate_start_ms(records):
    return median(r.completion_ms - r.release_ms - r.latency_ms for r in records)


def result_files(root, mode, tag):
    summaries = list((root / f"mongodb-{mode}-pause-{tag}").glob("*/summary.json"))
    if len(summaries) != 1:
        raise ValueError(f"expected one {mode} summary, found {len(summaries)}")
    return summaries[0], summaries[0].with_name("workload.json")


def operation_count(summary):
    return int(float(summary.get("ALL", {}).get("Count", 0)))


def metrics(records, pause_start_ms, pause_end_ms, summary):
    releases = sorted(record.release_ms for record in records)
    # estimating time wehn pause started relative to first record
    pause_release_ms = pause_start_ms - estimate_start_ms(records)
    pause_duration_ms = pause_end_ms - pause_start_ms
    # calculating time when pause ended relative to first record
    pause_end_release_ms = pause_release_ms + pause_duration_ms
    # release gaps during the pause
    release_gaps = [right - left for left, right in zip(releases, releases[1:])
                    if left <= pause_end_release_ms and right >= pause_release_ms]
    overall = summary.get("Overall", {})
    all_operations = summary.get("ALL", {})

    return {
        "recorded_count": len(records),
        "summary_count": operation_count(summary),
        "failures_or_timeouts": sum(int(float(values.get("Count", 0)))
                                    for name, values in summary.items()
                                    if name.endswith(("-FAILED", "-TIMEDOUT"))),
        "pause_duration_ms": round(pause_duration_ms, 3),
        "releases_during_pause": sum(pause_release_ms <= release < pause_end_release_ms
                                     for release in releases),
        "largest_release_gap_during_pause_ms": round(max(release_gaps, default=0), 3),
        "runtime_ms": float(overall.get("RunTime(ms)", 0)),
        "throughput_ops_sec": float(overall.get("Throughput(ops/sec)", 0)),
        "p99_latency_ms": float(all_operations.get("99Percentile(ms)", 0)),
        "max_latency_ms": float(all_operations.get("MaxValue(ms)", 0)),
        "pause_release_ms": pause_release_ms,
    }


def release_rate(records, pause_release_ms, bin_ms):
    window_start_ms, window_end_ms = -2000.0, 3000.0
    bins = [0] * int((window_end_ms - window_start_ms) / bin_ms)

    for record in records:
        relative_ms = record.release_ms - pause_release_ms
        index = int((relative_ms - window_start_ms) // bin_ms)
        if 0 <= index < len(bins):
            bins[index] += 1

    seconds = [(window_start_ms + (index + 0.5) * bin_ms) / 1000
               for index in range(len(bins))]
    rates = [count * 1000.0 / bin_ms for count in bins]
    return seconds, rates


def plot_results(modes, bin_ms, target, output):
    colors = {"async": "#1f77b4", "sync": "#d62728"}
    figure, (rate_axis, latency_axis) = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
    # obtaining the maximum pause duration across both modes for shading (should be 1s)
    pause_ms = max(data["metrics"]["pause_duration_ms"] for data in modes.values())

    # shading in the pause period on both subplots
    for axis in (rate_axis, latency_axis):
        axis.axvspan(0, pause_ms / 1000.0, color="0.85", label="MongoDB pause")
        axis.grid(alpha=0.25)

    for mode, data in modes.items():
        # obtaining the pause release time relative to the first record
        pause_release_ms = data["metrics"]["pause_release_ms"]
        # obtaining the release rate and latency data for plotting
        seconds, rates = release_rate(data["records"], pause_release_ms, bin_ms)

        # plotting the release rate data
        rate_axis.plot(seconds, rates, color=colors[mode], label=f"{mode}mode")

        # obtaining the release time and latency data for plotting
        release_seconds = [(r.release_ms - pause_release_ms) / 1000
                           for r in data["records"]]
        latencies = [r.latency_ms for r in data["records"]]
        latency_axis.scatter(
            release_seconds, latencies, s=5, alpha=0.45,
            color=colors[mode], label=f"{mode}mode"
        )

    # plotting the target release rate as a horizontal dashed line
    rate_axis.axhline(target, color="black", linestyle="--", label="target")
    rate_axis.set(ylabel="Release rate (ops/s)")
    latency_axis.set(ylabel="Latency (ms)",
                     xlabel="Seconds relative to pause start", xlim=(-2, 3))
    rate_axis.legend(loc="upper right")
    latency_axis.legend(loc="upper right")
    # figure.suptitle("NoSQLMark MongoDB: asyncmode=true vs asyncmode=false")
    figure.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=160)
    plt.close(figure)

if __name__ == "__main__":
    args = parse_args()
    output = Path("mongo-open-vs-closed.png")

    modes = {}
    timeseries = read_timeseries(args.timeseries)
    records_by_mode = {
        "async": timeseries[:-3000],
        "sync": timeseries[-3000:],
    }

    for mode in ("async", "sync"):
        summary_path, workload_path = result_files(args.results_root, mode, args.pair_tag)
        summary = json.loads(summary_path.read_text())
        workload = json.loads(workload_path.read_text())

        if workload.get("asyncmode") is not (mode == "async"):
            raise ValueError(f"unexpected asyncmode in {workload_path}")
        target = float(workload.get("target", 0))

        pause_start_ms, pause_end_ms = read_pause(args.run_dir / f"{mode}-pause.log")
        records = records_by_mode[mode]

        modes[mode] = {"records": records,
                       "metrics": metrics(records, pause_start_ms, pause_end_ms, summary)}

    plot_results(modes, args.bin_ms, target, output)
    report = {mode: {name: value for name, value in data["metrics"].items()
                     if name != "pause_release_ms"}
              for mode, data in modes.items()}
    print(json.dumps(report, indent=2, sort_keys=True))
