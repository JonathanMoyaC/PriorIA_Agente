"""Run the illustrative development episode and write its metrics and traces."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from prioria_sim import run_demo


def main() -> None:
    output_dir = Path("outputs")
    output_dir.mkdir(exist_ok=True)
    metrics, traces = run_demo()

    with (output_dir / "demo_metrics.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(metrics[0]))
        writer.writeheader()
        writer.writerows(metrics)

    with (output_dir / "demo_traces.jsonl").open("w", encoding="utf-8") as stream:
        for row in traces:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"Métricas: {output_dir / 'demo_metrics.csv'}")
    print(f"Trazas:   {output_dir / 'demo_traces.jsonl'}")
    for row in metrics:
        print(row)


if __name__ == "__main__":
    main()
