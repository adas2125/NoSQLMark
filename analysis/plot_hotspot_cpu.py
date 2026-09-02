#!/usr/bin/env python3
"""Plot per-shard CPU from a hotspot experiment's cpu-by-pod.json."""

import argparse
import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


parser = argparse.ArgumentParser()
parser.add_argument("run_dir", type=Path)
parser.add_argument("--output", type=Path)
parser.add_argument("--threshold", type=float, default=55.0)
args = parser.parse_args()

input_path = args.run_dir / "metrics" / "cpu-by-pod.json"
output_path = args.output or args.run_dir / "plots" / "cpu_vs_time.png"
if output_path.exists():
    parser.error(f"refusing to overwrite: {output_path}")

payload = json.loads(input_path.read_text())
series = payload["data"]["result"]
start = min(float(item["values"][0][0]) for item in series)

fig, ax = plt.subplots(figsize=(10, 4.5))
for item in sorted(series, key=lambda value: value["metric"]["pod"]):
    pod = item["metric"]["pod"]
    match = re.search(r"shard(\d+)-data", pod)
    label = f"Shard {match.group(1)}" if match else pod
    minutes = [(float(point[0]) - start) / 60 for point in item["values"]]
    cpu = [float(point[1]) for point in item["values"]]
    ax.plot(minutes, cpu, linewidth=1.8, label=label)

ax.axhline(args.threshold, color="black", linestyle="--", linewidth=1,
           label=f"Scale-up threshold ({args.threshold:g}%)")
ax.set(title="MongoDB CPU during hotspot workload",
       xlabel="Minutes from experiment start",
       ylabel="CPU (% of request)")
ax.grid(alpha=0.25)
ax.legend()
fig.tight_layout()

output_path.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(output_path, dpi=180)
plt.close(fig)
