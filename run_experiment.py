"""Run the preregistered, paired PriorIA allocation experiment."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from dataclasses import asdict
from pathlib import Path
from statistics import mean, stdev
from typing import Any

from prioria_sim import (
    BeamSearchPolicy,
    EventSimulator,
    FixedPipeline,
    ReactivePolicy,
    generate_scenario,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_MANIFEST = ROOT / "configs" / "experiment_manifest.json"
DEFAULT_OUTPUT = ROOT / "outputs" / "experiment"
SERVICE_MEANS = {"short": 20.0, "medium": 40.0, "long": 65.0}
EXPECTED_POLICIES = ("fixed_pipeline", "reactive", "prioria_beam")


def load_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    scenarios = manifest.get("scenarios", [])
    ids = [str(item["scenario_id"]) for item in scenarios]
    seeds = [int(item["seed"]) for item in scenarios]
    if len(ids) != len(set(ids)) or len(seeds) != len(set(seeds)):
        raise ValueError("El manifiesto debe tener IDs y semillas únicos.")
    for split in ("development", "evaluation"):
        count = sum(item["split"] == split for item in scenarios)
        if count == 0:
            raise ValueError(f"El manifiesto no contiene escenarios de {split}.")
    return manifest


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary = []
    for policy in EXPECTED_POLICIES:
        group = [row for row in rows if row["policy"] == policy]
        if not group:
            continue
        entry: dict[str, Any] = {"policy": policy, "scenarios": len(group)}
        field_labels = {
            "primary_mean_restricted_wait_to_occupancy_min": "restricted_wait_to_occupancy_min",
            "abandonment_rate": "abandonment_rate",
            "assigned": "assigned",
            "abandoned": "abandoned",
            "not_assigned_at_horizon": "not_assigned_at_horizon",
            "restricted_wait_p50_min": "restricted_wait_p50_min",
            "restricted_wait_p90_min": "restricted_wait_p90_min",
            "restricted_wait_p95_min": "restricted_wait_p95_min",
            "box_utilization": "box_utilization",
            "mean_decision_time_ms": "decision_time_ms",
            "p95_decision_time_ms": "p95_decision_time_ms",
            "executed_incompatible_assignments": "executed_incompatible_assignments",
            "invalid_assignment_attempts_rejected": "invalid_assignment_attempts_rejected",
        }
        for field, label in field_labels.items():
            values = [float(row[field]) for row in group if row.get(field) is not None]
            entry[f"mean_{label}"] = round(mean(values), 6) if values else None
            entry[f"sd_{label}"] = round(stdev(values), 6) if len(values) > 1 else 0.0
        for priority in range(1, 6):
            field = f"restricted_wait_priority_{priority}_min"
            values = [float(row[field]) for row in group if row.get(field) is not None]
            entry[f"mean_{field}"] = round(mean(values), 6) if values else None
            entry[f"sd_{field}"] = round(stdev(values), 6) if len(values) > 1 else 0.0
        summary.append(entry)
    return summary


def paired_bootstrap(
    rows: list[dict[str, Any]], split: str, seed: int, replicates: int = 5000
) -> list[dict[str, Any]]:
    by_scenario: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        by_scenario.setdefault(row["episode_id"], {})[row["policy"]] = row

    comparisons = []
    metrics = (
        ("primary_mean_restricted_wait_to_occupancy_min", "minutes"),
        ("abandonment_rate", "proportion"),
    )
    for candidate in ("prioria_beam", "reactive"):
        pairs = [policies for policies in by_scenario.values()
                 if candidate in policies and "fixed_pipeline" in policies]
        for metric, unit in metrics:
            differences = [float(pair[candidate][metric]) - float(pair["fixed_pipeline"][metric])
                           for pair in pairs]
            result: dict[str, Any] = {
                "split": split,
                "comparison": f"{candidate} - fixed_pipeline",
                "metric": metric,
                "unit": unit,
                "paired_scenarios": len(differences),
                "mean_difference": round(mean(differences), 6) if differences else None,
                "ci95_low": None,
                "ci95_high": None,
                "bootstrap_replicates": replicates if len(differences) >= 2 else 0,
            }
            if len(differences) >= 2:
                rng = random.Random(seed + len(differences) * 997 + sum(map(ord, metric + candidate)))
                estimates = sorted(
                    mean(differences[rng.randrange(len(differences))]
                         for _ in differences)
                    for _ in range(replicates)
                )
                result["ci95_low"] = round(estimates[int(0.025 * (replicates - 1))], 6)
                result["ci95_high"] = round(estimates[int(0.975 * (replicates - 1))], 6)
            comparisons.append(result)
    return comparisons


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_split(
    split: str,
    scenarios: list[dict[str, Any]],
    output_root: Path,
    horizon_minutes: int,
    patient_count: int,
    beam_width: int,
    search_horizon: int,
    search_timeout_ms: float,
) -> dict[str, Any]:
    split_dir = output_root / split
    split_dir.mkdir(parents=True, exist_ok=True)
    metrics_rows: list[dict[str, Any]] = []
    truth_path = split_dir / "scenario_truth.jsonl"
    traces_path = split_dir / "traces.jsonl"

    with truth_path.open("w", encoding="utf-8") as truth_stream, \
            traces_path.open("w", encoding="utf-8") as trace_stream:
        for scenario in scenarios:
            seed = int(scenario["seed"])
            episode_id = str(scenario["scenario_id"])
            patients, boxes = generate_scenario(seed=seed, patient_count=patient_count)
            truth_stream.write(json.dumps({
                "episode_id": episode_id,
                "seed": seed,
                "patients": [
                    {**asdict(patient),
                     "required_capabilities": sorted(patient.required_capabilities)}
                    for patient in patients
                ],
                "boxes": [
                    {**asdict(box), "capabilities": sorted(box.capabilities)}
                    for box in boxes
                ],
            }, ensure_ascii=False) + "\n")

            for policy in (
                FixedPipeline(),
                ReactivePolicy(),
                BeamSearchPolicy(width=beam_width, horizon=search_horizon,
                                 timeout_ms=search_timeout_ms),
            ):
                simulator = EventSimulator(boxes, SERVICE_MEANS, horizon_minutes)
                metrics, traces = simulator.run(episode_id, seed, patients, policy)
                metrics["split"] = split
                metrics_rows.append(metrics)
                for record in traces:
                    record["split"] = split
                    trace_stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    write_csv(split_dir / "scenario_metrics.csv", metrics_rows)
    summary = summarize(metrics_rows)
    comparisons = paired_bootstrap(
        metrics_rows,
        split,
        seed=int(scenarios[0]["seed"]),
    ) if scenarios else []
    (split_dir / "summary.json").write_text(
        json.dumps({"summary": summary, "paired_comparisons": comparisons},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"split": split, "scenarios": len(scenarios), "policies": len(EXPECTED_POLICIES),
            "summary": summary, "paired_comparisons": comparisons}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("development", "evaluation", "all"),
                        default="development")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--horizon", type=int, default=240)
    parser.add_argument("--patients", type=int, default=18)
    parser.add_argument("--beam-width", type=int, default=20)
    parser.add_argument("--search-horizon", type=int, default=5)
    parser.add_argument("--search-timeout-ms", type=float, default=50.0)
    parser.add_argument("--confirm-evaluation", action="store_true",
                        help="Confirm that the reserved evaluation split may be opened now.")
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    selected_splits = ("development", "evaluation") if args.split == "all" else (args.split,)
    if "evaluation" in selected_splits and not args.confirm_evaluation:
        parser.error("La evaluación reservada requiere --confirm-evaluation después de congelar código y parámetros.")
    evaluation_dir = args.output / "evaluation"
    if "evaluation" in selected_splits and evaluation_dir.exists() and any(evaluation_dir.iterdir()):
        parser.error("Ya existe una evaluación reservada. No se sobrescribe automáticamente.")
    source_paths = (ROOT / "prioria_sim.py", ROOT / "run_experiment.py", ROOT / "main.py", args.manifest)
    source_sha256 = {path.name: sha256_file(path) for path in source_paths}
    run_config = {
        "manifest": str(args.manifest),
        "manifest_master_seed": manifest["master_seed"],
        "design": manifest["design"],
        "split_run": list(selected_splits),
        "horizon_minutes": args.horizon,
        "patients_per_scenario": args.patients,
        "service_means_minutes": SERVICE_MEANS,
        "beam_width": args.beam_width,
        "search_horizon": args.search_horizon,
        "search_timeout_ms": args.search_timeout_ms,
        "policies": list(EXPECTED_POLICIES),
        "source_sha256": source_sha256,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "run_config.json").write_text(
        json.dumps(run_config, ensure_ascii=False, indent=2), encoding="utf-8")

    reports = []
    for split in selected_splits:
        subset = [item for item in manifest["scenarios"] if item["split"] == split]
        split_config = {**run_config, "split_run": [split]}
        split_dir = args.output / split
        split_dir.mkdir(parents=True, exist_ok=True)
        (split_dir / "run_config.json").write_text(
            json.dumps(split_config, ensure_ascii=False, indent=2), encoding="utf-8")
        reports.append(run_split(split, subset, args.output, args.horizon, args.patients,
                                 args.beam_width, args.search_horizon, args.search_timeout_ms))
    evaluation_report = next((report for report in reports if report["split"] == "evaluation"), None)
    if evaluation_report is not None:
        write_csv(args.output / "main_table.csv", evaluation_report["summary"])
        (args.output / "paired_comparisons.json").write_text(
            json.dumps(evaluation_report["paired_comparisons"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(json.dumps(reports, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
