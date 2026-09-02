"""
Create the throughput plot for a saved closed-loop Mambo run.
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

# ``search`` is intentional: lines prefixed with ``#OUTLIER`` are still used.
YCSB_SAMPLE_RE = re.compile(
    r"(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}:\d{3})\s+"
    r"(?P<elapsed>\d+) sec:.*?;\s*"
    r"(?P<throughput>[\d.]+) current ops/sec;"
)
CONTROLLER_TIME_RE = re.compile(r"^(?P<time>\S+Z)\s")

EVENTS = (
    ("Scaling up shards", "Scale shards 1 to 2", "tab:green"),
    (
        "Resharding completed; shard scaling-up process finished",
        "Resharding complete",
        "tab:red",
    ),
    ("Scaling up replicas", "Scale replicas 1 to 2", "tab:green"),
    (
        "membership Job encountered failures",
        "Replicas scaled",
        "tab:red",
    ),
)

def parse_ycsb_time(value: str) -> datetime:
    """Parse YCSB's millisecond wall-clock timestamp as UTC."""
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S:%f").replace(tzinfo=timezone.utc)

def parse_end_time(value: str) -> time:
    """Validate the command-line cutoff format."""
    if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", value):
        raise argparse.ArgumentTypeError("use HH:MM:SS, for example 03:15:00")
    try:
        return datetime.strptime(value, "%H:%M:%S").time()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use HH:MM:SS, for example 03:15:00") from exc

def read_ycsb_logs(log_dir: Path) -> list[tuple[datetime, float]]:
    """Read timestamped throughput samples from every matching YCSB log."""
    # find the path to the first matching YCSB log file
    log_file_path = sorted(log_dir.glob("run_10_*.log"))[0]
    samples: list[tuple[datetime, float]] = []
    seen_elapsed: set[int] = set()

    for line in log_file_path.read_text(encoding="utf-8", errors="replace").splitlines():
        sample_match = YCSB_SAMPLE_RE.search(line)
        if sample_match:
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
    """Return the first occurrence of each relevant controller event."""
    events: list[tuple[datetime, str, str]] = []
    seen_labels: set[str] = set()
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        time_match = CONTROLLER_TIME_RE.match(line)
        if not time_match:
            continue
        for message, label, color in EVENTS:
            if message in line and label not in seen_labels:
                timestamp = datetime.fromisoformat(
                    time_match.group("time").replace("Z", "+00:00")
                )
                events.append((timestamp, label, color))
                seen_labels.add(label)
    return sorted(events)

def create_plot(
    samples: list[tuple[datetime, float]],
    events: list[tuple[datetime, str, str]],
    output_path: Path,
    end_time: datetime | None = None,
) -> None:
    """Plot throughput and controller events."""

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
        title="Mambo mongod CPU Scaling: Throughput vs Time",
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
    parser = argparse.ArgumentParser(description="Plot throughput from a saved Mambo run.")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument(
        "--end-time",
        type=parse_end_time,
        metavar="HH:MM:SS",
        help="stop the UTC x-axis at this time on the first sample's date",
    )
    args = parser.parse_args()

    # parse all the sample lines
    samples = read_ycsb_logs(args.run_dir / "ycsb")
    events = read_controller_events(args.run_dir / "controller.log")

    cutoff = None
    if args.end_time is not None:
        sample_dates = {sample[0].date() for sample in samples}
        if len(sample_dates) != 1:
            parser.error("--end-time requires all samples to be on one UTC date")

        # A time-only cutoff is unambiguous now that there is one sample date.
        cutoff = datetime.combine(sample_dates.pop(), args.end_time, tzinfo=timezone.utc)
        samples = [sample for sample in samples if sample[0] <= cutoff]
        events = [event for event in events if event[0] <= cutoff]
        if not samples:
            parser.error(f"no throughput samples occur on or before {cutoff.isoformat()}")

    output_path = args.run_dir / "plots" / "throughput_vs_time.png"
    create_plot(samples, events, output_path, cutoff)
