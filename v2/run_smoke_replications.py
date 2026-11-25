#!/usr/bin/env python3
"""Run reproducible exploratory replications with a synthetic V2 fixture."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

try:
    from .simulate_day import simulate_day
except ImportError:
    from simulate_day import simulate_day


def main() -> None:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        default=root / "configs" / "smoke_test.json")
    parser.add_argument("--replications", type=int, default=10)
    parser.add_argument("--first-seed", type=int, default=2026112401)
    parser.add_argument("--output", type=Path,
                        default=Path("outputs/v2/smoke_replications.csv"))
    args = parser.parse_args()
    if args.replications < 1:
        parser.error("--replications debe ser al menos 1.")

    base = json.loads(args.config.read_text(encoding="utf-8"))
    if base.get("metadata", {}).get("test_fixture") is not True:
        parser.error("Este ejecutor solo acepta configuraciones marcadas como fixture de prueba.")
    rows = []
    base_daily_patients = int(base["daily_patients"])
    demand_multiplier = float(base.get("arrival", {}).get("demand_multiplier", 1.0))
    patients_per_day = int(base_daily_patients * demand_multiplier + 0.5)
    for offset in range(args.replications):
        config = dict(base)
        config["seed"] = args.first_seed + offset
        summary, _ = simulate_day(config)
        rows.append({
            "replication": offset + 1,
            "seed": config["seed"],
            "base_daily_patients": summary["base_daily_patients"],
            "demand_multiplier": summary["demand_multiplier"],
            "generated": summary["generated"],
            "discharged": summary["discharged"],
            "hospitalized": summary["hospitalized"],
            "hospitalization_rate": summary["hospitalization_rate"],
            "abandoned": summary["abandoned"],
            "abandonment_rate": summary["abandonment_rate"],
            "unresolved": summary["unresolved"],
            "mean_arrival_to_first_consult_minutes": summary[
                "mean_arrival_to_first_consult_minutes"],
            "mean_total_wait_minutes": summary["mean_total_wait_minutes"],
            "standing_patient_minutes": summary["standing_patient_minutes"],
            "treatments_started": summary["treatments_started"],
            "treatment_failures": summary["treatment_failures"],
            "max_general_waiting_chair_occupancy": summary[
                "max_general_waiting_chair_occupancy"],
            "max_c2_waiting_chair_occupancy": summary[
                "max_c2_waiting_chair_occupancy"],
            "deis_values_verified": summary["metadata"]["deis_values_verified"],
        })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Corridas: {len(rows)} jornadas × {patients_per_day} pacientes")
    print(f"Resultados: {args.output}")
    print(f"Abandonos totales: {sum(row['abandoned'] for row in rows)}")
    print(f"Jornadas sin casos sin resolver: {sum(row['unresolved'] == 0 for row in rows)}/{len(rows)}")


if __name__ == "__main__":
    main()
