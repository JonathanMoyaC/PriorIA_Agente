"""Execute deterministic safety-boundary scenarios outside the main experiment."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prioria_sim import (
    BeamSearchPolicy,
    Box,
    EventSimulator,
    FixedPipeline,
    Patient,
    ReactivePolicy,
)


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "outputs" / "safety"
POLICIES = (FixedPipeline, ReactivePolicy, BeamSearchPolicy)


@dataclass(frozen=True)
class SafetyScenario:
    scenario_id: str
    description: str
    patients: tuple[Patient, ...]
    boxes: tuple[Box, ...]
    horizon: int = 60
    expected_service: tuple[tuple[str, float], ...] = (("short", 10.0), ("medium", 20.0), ("long", 30.0))
    expected_first_patient: str | None = None
    expected_assignment_time: tuple[str, int] | None = None
    expected_box_assignments: tuple[tuple[str, str], ...] | None = None
    expected_no_assignments: bool = False
    expected_abandonment: tuple[str, int] | None = None
    expected_waiting_at_horizon: int | None = None


def patient(patient_id: str, priority: int, required: set[str], *, eligible_at: int = 0,
            duration: int = 10, patience: int = 30,
            service_class: str = "short") -> Patient:
    return Patient(patient_id, eligible_at, priority, frozenset(required), service_class,
                   duration, patience)


def box(box_id: str, capabilities: set[str]) -> Box:
    return Box(box_id, frozenset(capabilities))


def scenarios() -> tuple[SafetyScenario, ...]:
    basic = box("B01", {"basic"})
    special = box("B02", {"basic", "monitoring"})
    return (
        SafetyScenario(
            "S01_priority_order",
            "Higher-priority patient is assigned before a lower-priority patient.",
            (patient("P01", 1, {"basic"}), patient("P02", 3, {"basic"})),
            (basic,), expected_first_patient="P01",
        ),
        SafetyScenario(
            "S02_incompatible_high_priority",
            "A high-priority patient with no compatible free box does not block a feasible patient.",
            (patient("P01", 1, {"monitoring"}), patient("P02", 2, {"basic"})),
            (basic,), expected_first_patient="P02",
        ),
        SafetyScenario(
            "S03_no_compatible_box",
            "An incompatible patient remains safely unassigned through the episode horizon.",
            (patient("P01", 1, {"surgery"}, patience=30),),
            (basic,), horizon=20, expected_no_assignments=True,
            expected_waiting_at_horizon=1,
        ),
        SafetyScenario(
            "S04_empty_queue",
            "An empty episode terminates without a policy decision or assignment.",
            (), (basic,), expected_no_assignments=True,
        ),
        SafetyScenario(
            "S05_abandon_after_threshold",
            "A patient abandons one minute after exceeding the patience threshold.",
            (patient("P01", 1, {"surgery"}, patience=5),),
            (basic,), horizon=20, expected_no_assignments=True,
            expected_abandonment=("P01", 6),
        ),
        SafetyScenario(
            "S06_box_release",
            "A waiting patient can use a box after its service event releases it.",
            (patient("P01", 1, {"basic"}, duration=10),
             patient("P02", 2, {"basic"}, duration=5)),
            (basic,), expected_first_patient="P01",
            expected_assignment_time=("P02", 10),
        ),
        SafetyScenario(
            "S07_expected_release_is_stale",
            "A busy box is not used even if its expected release estimate has passed.",
            (patient("P01", 1, {"basic"}, duration=40),
             patient("P02", 2, {"basic"}, eligible_at=10, duration=5, patience=50)),
            (basic,), expected_service=(("short", 5.0),),
            expected_assignment_time=("P02", 40),
        ),
        SafetyScenario(
            "S08_patience_boundary",
            "A patient at the exact patience threshold remains eligible for assignment.",
            (patient("P01", 1, {"basic"}, duration=10),
             patient("P02", 2, {"basic"}, patience=10, duration=5)),
            (basic,), expected_first_patient="P01",
            expected_assignment_time=("P02", 10),
        ),
        SafetyScenario(
            "S09_horizon_boundary",
            "An arrival at the horizon is recorded as unassigned at horizon, not assigned after closure.",
            (patient("P01", 1, {"basic"}, eligible_at=60),),
            (basic,), horizon=60, expected_no_assignments=True,
            expected_waiting_at_horizon=1,
        ),
        SafetyScenario(
            "S10_specialized_box_preserved",
            "A monitoring-only patient uses the specialized box, leaving the basic box available.",
            (patient("P01", 2, {"monitoring"}), patient("P02", 2, {"basic"})),
            (basic, special), expected_first_patient="P01",
            expected_box_assignments=(("P01", "B02"), ("P02", "B01")),
        ),
    )


def event_assignments(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    final_record = next(record for record in trace if "event_log" in record)
    return [event for event in final_record["event_log"] if event["event"] == "assigned"]


def check_beam_priority(trace: list[dict[str, Any]]) -> None:
    for record in trace:
        if "observation" not in record:
            continue
        decision = record["decision"]
        if decision["kind"] != "assign":
            continue
        feasible_ids = {pair["patient_id"] for pair in record["feasible_pairs"]}
        feasible_patients = [p for p in record["observation"]["waiting"]
                             if p["patient_id"] in feasible_ids]
        if not feasible_patients:
            raise AssertionError("El agente propuso asignar sin pareja factible.")
        highest_priority = min(p["priority"] for p in feasible_patients)
        chosen_priority = next(p["priority"] for p in feasible_patients
                               if p["patient_id"] == decision["patient_id"])
        if chosen_priority != highest_priority:
            raise AssertionError("PriorIA asignó una categoría menos urgente con otra más urgente factible.")


def run() -> list[dict[str, Any]]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    all_traces: list[dict[str, Any]] = []
    for scenario in scenarios():
        service_means = dict(scenario.expected_service)
        for policy_factory in POLICIES:
            policy = policy_factory() if policy_factory is not BeamSearchPolicy else BeamSearchPolicy(20, 5)
            simulator = EventSimulator(scenario.boxes, service_means, scenario.horizon)
            metrics, trace = simulator.run(scenario.scenario_id, 20261124, scenario.patients, policy)
            assignments = event_assignments(trace)
            violations = []

            if metrics["executed_incompatible_assignments"] != 0:
                violations.append("incompatible_assignment_executed")
            if metrics["invalid_assignment_attempts_rejected"] != 0:
                violations.append("invalid_assignment_attempt")
            if scenario.expected_no_assignments and assignments:
                violations.append("unexpected_assignment")
            if scenario.expected_first_patient and assignments:
                if assignments[0]["patient_id"] != scenario.expected_first_patient:
                    violations.append("unexpected_first_assignment")
            elif scenario.expected_first_patient and not assignments:
                violations.append("expected_assignment_missing")
            if scenario.expected_assignment_time:
                pid, expected_time = scenario.expected_assignment_time
                event = next((item for item in assignments if item["patient_id"] == pid), None)
                if event is None or event["time"] != expected_time:
                    violations.append("assignment_time_mismatch")
            if scenario.expected_box_assignments:
                actual_boxes = {event["patient_id"]: event["box_id"] for event in assignments}
                if any(actual_boxes.get(pid) != box_id
                       for pid, box_id in scenario.expected_box_assignments):
                    violations.append("box_assignment_mismatch")
            if scenario.expected_abandonment:
                pid, expected_time = scenario.expected_abandonment
                events = trace[-1]["event_log"]
                event = next((item for item in events
                              if item["event"] == "abandoned" and item["patient_id"] == pid), None)
                if event is None or event["time"] != expected_time:
                    violations.append("abandonment_time_mismatch")
            if scenario.expected_waiting_at_horizon is not None:
                if metrics["not_assigned_at_horizon"] != scenario.expected_waiting_at_horizon:
                    violations.append("horizon_count_mismatch")
            if isinstance(policy, BeamSearchPolicy):
                try:
                    check_beam_priority(trace)
                except AssertionError:
                    violations.append("strict_priority_violation")

            rows.append({
                "scenario_id": scenario.scenario_id,
                "description": scenario.description,
                "policy": policy.name,
                "status": "PASS" if not violations else "FAIL",
                "violations": ";".join(violations),
                "patients_eligible": metrics["patients_eligible"],
                "assigned": metrics["assigned"],
                "abandoned": metrics["abandoned"],
                "not_assigned_at_horizon": metrics["not_assigned_at_horizon"],
                "invalid_assignment_attempts_rejected": metrics["invalid_assignment_attempts_rejected"],
                "executed_incompatible_assignments": metrics["executed_incompatible_assignments"],
            })
            for record in trace:
                record["safety_scenario_id"] = scenario.scenario_id
                record["split"] = "safety_boundary"
                all_traces.append(record)

    with (OUTPUT_DIR / "safety_scenarios.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with (OUTPUT_DIR / "safety_traces.jsonl").open("w", encoding="utf-8") as stream:
        for record in all_traces:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    report = {
        "scenarios": len(scenarios()),
        "policies": len(POLICIES),
        "scenario_policy_runs": len(rows),
        "passed": sum(row["status"] == "PASS" for row in rows),
        "failed": sum(row["status"] == "FAIL" for row in rows),
        "results": rows,
    }
    (OUTPUT_DIR / "safety_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("scenarios", "policies", "scenario_policy_runs", "passed", "failed")},
                     ensure_ascii=False, indent=2))
    return rows


if __name__ == "__main__":
    run()
