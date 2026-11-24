from __future__ import annotations

from collections import defaultdict
from statistics import mean, stdev
from time import perf_counter
import random
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator

from prioria_sim import (
    BeamSearchPolicy,
    Box,
    EventSimulator,
    FixedPipeline,
    ReactivePolicy,
    generate_scenario,
)


app = FastAPI(
    title="PriorIA — API de simulación",
    description="Comparación pareada de políticas sintéticas de asignación a boxes con procesos SimPy.",
    version="0.6.0",
)


class BoxInput(BaseModel):
    box_id: str = Field(min_length=1, max_length=20)
    capabilities: list[str] = Field(min_length=1)


class SimulationRequest(BaseModel):
    seed: int = 20261113
    scenarios: int = Field(default=1, ge=1, le=80)
    patients_per_scenario: int = Field(default=18, ge=1, le=100)
    horizon_minutes: int = Field(default=240, ge=30, le=720)
    patience_minutes: int = Field(default=60, ge=5, le=300)
    arrival_interval_minutes: int = Field(default=15, ge=1, le=120)
    arrivals_per_batch: int = Field(default=3, ge=1, le=20)
    service_short_minutes: int = Field(default=20, ge=1, le=300)
    service_medium_minutes: int = Field(default=40, ge=1, le=300)
    service_long_minutes: int = Field(default=65, ge=1, le=300)
    service_sd_minutes: float = Field(default=5.0, ge=0, le=60)
    priority_probabilities: list[float] = Field(default_factory=lambda: [0.2] * 5)
    requirement_catalog: list[list[str]] = Field(default_factory=lambda: [
        ["basic"], ["basic", "monitoring"], ["basic", "procedure"]
    ])
    boxes: list[BoxInput] = Field(default_factory=lambda: [
        BoxInput(box_id="B01", capabilities=["basic"]),
        BoxInput(box_id="B02", capabilities=["basic", "monitoring"]),
        BoxInput(box_id="B03", capabilities=["basic", "procedure"]),
    ])
    beam_width: int = Field(default=20, ge=1, le=100)
    search_horizon: int = Field(default=5, ge=1, le=15)

    @model_validator(mode="after")
    def check_scenario_configuration(self) -> "SimulationRequest":
        if not self.boxes:
            raise ValueError("Debe existir al menos un box.")
        box_ids = [box.box_id for box in self.boxes]
        if len(box_ids) != len(set(box_ids)):
            raise ValueError("Los identificadores de box deben ser únicos.")
        if not self.requirement_catalog or any(not req for req in self.requirement_catalog):
            raise ValueError("Cada requisito debe contener al menos una capacidad.")
        if len(self.priority_probabilities) != 5:
            raise ValueError("Ingrese cinco probabilidades de prioridad (1 a 5).")
        if any(p < 0 for p in self.priority_probabilities) or sum(self.priority_probabilities) <= 0:
            raise ValueError("Las probabilidades deben ser no negativas y sumar más que cero.")
        return self


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_policy: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_policy[row["policy"]].append(row)
    summary = []
    numeric_fields = [
        "primary_mean_restricted_wait_to_occupancy_min",
        "restricted_wait_p50_min",
        "restricted_wait_p90_min",
        "restricted_wait_p95_min",
        "mean_wait_to_occupancy_assigned_only_min",
        "mean_observed_wait_to_terminal_min",
        "box_utilization",
        "abandonment_rate",
        "patients_eligible",
        "assigned",
        "abandoned",
        "not_assigned_at_horizon",
        "executed_incompatible_assignments",
        "invalid_assignment_attempts_rejected",
        "mean_decision_time_ms",
        "p95_decision_time_ms",
        *(f"restricted_wait_priority_{priority}_min" for priority in range(1, 6)),
    ]
    for policy, items in by_policy.items():
        entry: dict[str, Any] = {"policy": policy, "scenarios": len(items)}
        for key in numeric_fields:
            values = [float(item[key]) for item in items if item.get(key) is not None]
            # Raw metrics that already start with "mean_" must not receive a
            # second prefix: the UI and API contract use mean_decision_time_ms.
            if key == "primary_mean_restricted_wait_to_occupancy_min":
                metric_name = "restricted_wait_to_occupancy_min"
            else:
                metric_name = key.removeprefix("mean_") if key.startswith("mean_") else key
            entry[f"mean_{metric_name}"] = round(mean(values), 4) if values else None
            entry[f"sd_{metric_name}"] = round(stdev(values), 4) if len(values) > 1 else 0.0 if values else None
        summary.append(entry)
    order = {"fixed_pipeline": 0, "reactive": 1, "prioria_beam": 2}
    return sorted(summary, key=lambda item: order.get(item["policy"], 99))


def _paired_bootstrap(rows: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    by_scenario: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        by_scenario[row["episode_id"]][row["policy"]] = row
    paired = [entry for entry in by_scenario.values()
              if "fixed_pipeline" in entry and "prioria_beam" in entry]
    if not paired:
        return []

    comparisons = []
    metrics = [
        ("primary_mean_restricted_wait_to_occupancy_min", "min", "minutos"),
        ("abandonment_rate", "proportion", "proporción"),
    ]
    for field, _, unit in metrics:
        diffs = [float(pair["prioria_beam"][field]) - float(pair["fixed_pipeline"][field])
                 for pair in paired
                 if pair["prioria_beam"].get(field) is not None
                 and pair["fixed_pipeline"].get(field) is not None]
        result: dict[str, Any] = {
            "comparison": "prioria_beam - fixed_pipeline",
            "metric": field,
            "unit": unit,
            "paired_scenarios": len(diffs),
            "mean_difference": round(mean(diffs), 6) if diffs else None,
            "ci95_low": None,
            "ci95_high": None,
        }
        if len(diffs) >= 2:
            rng = random.Random(seed + len(diffs) * 997 + (1 if field == "abandonment_rate" else 0))
            bootstrap_means = []
            for _ in range(2000):
                sample = [diffs[rng.randrange(len(diffs))] for _ in diffs]
                bootstrap_means.append(mean(sample))
            result["ci95_low"] = round(_percentile(bootstrap_means, 0.025), 6)
            result["ci95_high"] = round(_percentile(bootstrap_means, 0.975), 6)
        comparisons.append(result)
    return comparisons


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "prioria-api"}


@app.get("/defaults")
def defaults() -> dict[str, Any]:
    return SimulationRequest().model_dump()


@app.post("/simulate")
def simulate(request: SimulationRequest) -> dict[str, Any]:
    started = perf_counter()
    estimated_search_work = (request.scenarios * request.patients_per_scenario ** 2
                             * request.beam_width * request.search_horizon)
    if estimated_search_work > 75_000_000:
        raise HTTPException(
            status_code=422,
            detail="La combinación solicitada es demasiado grande para la interfaz interactiva. "
                   "Reduce turnos, pacientes, B o H y ejecuta lotes separados.",
        )
    service_means = {
        "short": float(request.service_short_minutes),
        "medium": float(request.service_medium_minutes),
        "long": float(request.service_long_minutes),
    }
    probability_total = sum(request.priority_probabilities)
    priority_probabilities = [value / probability_total for value in request.priority_probabilities]
    boxes = [Box(box.box_id, frozenset(cap.strip() for cap in box.capabilities if cap.strip()))
             for box in request.boxes]
    if any(not box.capabilities for box in boxes):
        raise HTTPException(status_code=422, detail="Cada box debe tener al menos una capacidad.")
    requirement_catalog = [frozenset(cap.strip() for cap in req if cap.strip())
                           for req in request.requirement_catalog]
    if any(not req for req in requirement_catalog):
        raise HTTPException(status_code=422, detail="Cada requisito debe tener al menos una capacidad.")

    all_metrics: list[dict[str, Any]] = []
    sample_traces: list[dict[str, Any]] = []
    policies = [FixedPipeline(), ReactivePolicy(),
                BeamSearchPolicy(request.beam_width, request.search_horizon)]
    for scenario_index in range(request.scenarios):
        scenario_seed = request.seed + scenario_index
        patients, _ = generate_scenario(
            seed=scenario_seed,
            patient_count=request.patients_per_scenario,
            arrival_interval_minutes=request.arrival_interval_minutes,
            arrivals_per_batch=request.arrivals_per_batch,
            patience_minutes=request.patience_minutes,
            service_means=service_means,
            service_sd_minutes=request.service_sd_minutes,
            priority_probabilities=priority_probabilities,
            boxes=boxes,
            requirement_catalog=requirement_catalog,
        )
        episode_id = f"turn-{scenario_index + 1:03d}"
        for policy in policies:
            simulator = EventSimulator(boxes, service_means, request.horizon_minutes)
            metrics, traces = simulator.run(episode_id, scenario_seed, patients, policy)
            all_metrics.append(metrics)
            if scenario_index == 0:
                sample_traces.extend(traces)

    return {
        "configuration": request.model_dump(),
        "summary": _summary(all_metrics),
        "paired_comparison": _paired_bootstrap(all_metrics, request.seed),
        "scenario_results": all_metrics,
        "sample_traces": sample_traces,
        "execution_seconds": round(perf_counter() - started, 4),
        "interpretation": {
            "synthetic_only": True,
            "primary_metric": "mean restricted wait to occupancy; non-assigned cases are capped at the shift horizon",
            "paired_design": "same seed and synthetic patient stream across the three policies",
            "reserved_test_warning": "Use reserved scenarios only after freezing parameters and code.",
        },
    }
