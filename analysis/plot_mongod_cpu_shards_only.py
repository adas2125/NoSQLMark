"""
Create the throughput plot for a saved shards-only Mambo run.
Expected directory layout::
    RUN_DIR/
      controller.log
      ycsb/run_10_*.log
Outputs ``RUN_DIR/plots/throughput_vs_time.png``.
"""

import argparse
import re
from datetime import datetime, time, timezone
from pathlib import Path
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

# Expected YCSB text includes:
# 2026-08-30 05:37:14:... 10 sec: ...; 3000.0 current ops/sec; ...
YCSB_SAMPLE_RE = re.compile(
    r"(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}:\d{3})\s+"
    r"(?P<elapsed>\d+) sec:.*?;\s*"
    r"(?P<throughput>[\d.]+) current ops/sec;"
)
CONTROLLER_TIME_RE = re.compile(r"^(?P<time>\S+Z)\s")

SHARD_SCALE_MESSAGE = "Scaling up shards"
RESHARDING_COMPLETE_MESSAGE = (
    "Resharding completed; shard scaling-up process finished"
)


def parse_ycsb_time(value: str) -> datetime:
    """Parse YCSB's millisecond wall-clock timestamp as UTC."""
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S:%f").replace(
        tzinfo=timezone.utc
    )


def parse_end_time(value: str) -> time:
    """Validate the command-line cutoff format."""
    if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", value):
        raise argparse.ArgumentTypeError("use HH:MM:SS, for example 05:50:00")
    try:
        return datetime.strptime(value, "%H:%M:%S").time()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use HH:MM:SS, for example 05:50:00") from exc


def read_ycsb_logs(log_dir: Path) -> list[tuple[datetime, float]]:
    """Read timestamped throughput samples from the matching YCSB log."""
    log_file_path = sorted(log_dir.glob("run_10_*.log"))[0]
    samples: list[tuple[datetime, float]] = []
    seen_elapsed: set[int] = set()

    for line in log_file_path.read_text(encoding="utf-8", errors="replace").splitlines():
        sample_match = YCSB_SAMPLE_RE.search(line)
        if not sample_match:
            continue

        elapsed = int(sample_match.group("elapsed"))
        # Ignore YCSB's short final sample when it repeats an elapsed time.
        if elapsed not in seen_elapsed:
            seen_elapsed.add(elapsed)
            samples.append(
                (
                    parse_ycsb_time(sample_match.group("time")),
                    float(sample_match.group("throughput")),
                )
            )

    if not samples:
        raise ValueError(f"no throughput samples could be parsed from {log_dir}")
    return sorted(samples)


def read_controller_events(log_path: Path) -> list[tuple[datetime, str, str]]:
    """Find both shard-scaling and resharding-completion cycles."""
    events: list[tuple[datetime, str, str]] = []
    scale_count = 0
    completion_count = 0
    scale_colors = ("tab:red", "tab:orange")
    completion_colors = ("tab:blue", "tab:cyan")

    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        time_match = CONTROLLER_TIME_RE.match(line)
        if not time_match:
            continue

        timestamp = datetime.fromisoformat(
            time_match.group("time").replace("Z", "+00:00")
        )
        if SHARD_SCALE_MESSAGE in line:
            old_shards = scale_count + 1
            events.append(
                (
                    timestamp,
                    f"Scale shards {old_shards} to {old_shards + 1}",
                    scale_colors[scale_count % len(scale_colors)],
                )
            )
            scale_count += 1
        elif RESHARDING_COMPLETE_MESSAGE in line:
            old_shards = completion_count + 1
            events.append(
                (
                    timestamp,
                    f"Resharding {old_shards} to {old_shards + 1} complete",
                    completion_colors[completion_count % len(completion_colors)],
                )
            )
            completion_count += 1

    return sorted(events)


def create_plot(
    samples: list[tuple[datetime, float]],
    events: list[tuple[datetime, str, str]],
    output_path: Path,
    end_time: datetime | None = None,
) -> None:
    """Plot throughput and both shard-scaling cycles."""
    fig, ax = plt.subplots(figsize=(12, 5.8))
    times, throughput = zip(*samples)
    ax.plot(times, throughput, marker="o", markersize=3, linewidth=1.2)

    for timestamp, label, color in events:
        ax.axvline(
            timestamp,
            color=color,
            linestyle="--",
            linewidth=1.5,
            label=label,
        )

    ax.set(
        title="Mambo mongod CPU Shards-Only: Throughput vs Time",
        xlabel="Time",
        ylabel="Throughput (ops/sec)",
    )
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S", tz=timezone.utc))
    if end_time is not None:
        ax.set_xlim(right=end_time)
    ax.grid(alpha=0.25)
    if events:
        ax.legend(loc="best", fontsize=8)

    fig.autofmt_xdate(rotation=45)
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Plot throughput from a saved shards-only Mambo run."
    )
    parser.add_argument("run_dir", type=Path)
    parser.add_argument(
        "--end-time",
        type=parse_end_time,
        metavar="HH:MM:SS",
        help="stop the UTC x-axis at this time on the run's sample date",
    )
    args = parser.parse_args()

    samples = read_ycsb_logs(args.run_dir / "ycsb")
    events = read_controller_events(args.run_dir / "controller.log")

    cutoff = None
    if args.end_time is not None:
        sample_dates = {sample[0].date() for sample in samples}
        if len(sample_dates) != 1:
            parser.error("--end-time requires all samples to be on one UTC date")

        cutoff = datetime.combine(sample_dates.pop(), args.end_time, tzinfo=timezone.utc)
        samples = [sample for sample in samples if sample[0] <= cutoff]
        events = [event for event in events if event[0] <= cutoff]
        if not samples:
            parser.error(f"no throughput samples occur on or before {cutoff.isoformat()}")

    output_path = args.run_dir / "plots" / "throughput_vs_time.png"
    create_plot(samples, events, output_path, cutoff)
