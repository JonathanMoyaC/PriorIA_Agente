"""Multi-day PriorIA runs with a discarded warm-up period."""
from __future__ import annotations

from copy import deepcopy
from collections import defaultdict
from typing import Any

from v2.generate_patients import generate_patients
from v2.simulate_day import simulate_day

MINUTES_PER_DAY = 24 * 60
BASE_PATIENTS_PER_DAY = 100


def simulate_period(config: dict[str, Any], simulation_days: int,
                    warmup_days: int = 1) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run whole warm-up days, then collect metrics during the requested period.

    Patients and resources remain in the same simulation across the warm-up and
    observation boundary. Warm-up arrivals are excluded from patient-level
    metrics, but can remain in queues or active care during the measured period.
    """
    simulation_days = int(simulation_days)
    warmup_days = int(warmup_days)
    if not 1 <= simulation_days <= 7:
        raise ValueError("simulation_days debe estar entre 1 y 7.")
    if not 0 <= warmup_days <= 3:
        raise ValueError("warmup_days debe estar entre 0 y 3.")
    if int(config.get("daily_patients", BASE_PATIENTS_PER_DAY)) != BASE_PATIENTS_PER_DAY:
        raise ValueError("PriorIA V4 usa un volumen de referencia fijo de 100 pacientes/día.")
    attention_nurses = int(config["resources"].get("box_nurses", 3))
    if not 1 <= attention_nurses <= 3:
        raise ValueError("La dotación de enfermería de atención debe estar entre 1 y 3.")

    total_days = warmup_days + simulation_days
    seed = int(config["seed"])
    profiles: list[dict[str, Any]] = []
    for day_index in range(total_days):
        day_config = deepcopy(config)
        day_config["daily_patients"] = BASE_PATIENTS_PER_DAY
        day_config["seed"] = seed + day_index * 104729
        daily = generate_patients(day_config)
        for profile in daily:
            profile["patient_id"] = f"D{day_index + 1:02d}-{profile['patient_id']}"
            profile["arrival_minute"] += day_index * MINUTES_PER_DAY
            profile["arrival_day"] = day_index + 1
            profiles.append(profile)

    observed_start = warmup_days * MINUTES_PER_DAY
    observed_end = total_days * MINUTES_PER_DAY
    run_config = deepcopy(config)
    run_config["daily_patients"] = BASE_PATIENTS_PER_DAY
    run_config["max_simulation_minutes"] = observed_end + 7 * MINUTES_PER_DAY
    summary, trace = simulate_day(
        run_config,
        patient_profiles=profiles,
        observation_window=(observed_start, observed_end),
    )
    summary.update({
        "simulation_days": simulation_days,
        "warmup_days": warmup_days,
        "observation_start_minute": observed_start,
        "observation_end_minute": observed_end,
        "warmup_patients": sum(p["arrival_minute"] < observed_start for p in profiles),
        "generated_total_with_warmup": len(profiles),
        "patients_per_day_reference": BASE_PATIENTS_PER_DAY,
    })
    return summary, trace


def dashboard_data(trace: list[dict[str, Any]], summary: dict[str, Any],
                   config: dict[str, Any]) -> dict[str, Any]:
    """Summarize arrivals, categories, waits and observed resource utilization."""
    start = int(summary["observation_start_minute"])
    end = int(summary["observation_end_minute"])
    duration = max(1, end - start)
    patient_rows = [row for row in trace if row.get("event") == "patient_summary"]
    events_by_patient: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in trace:
        if event.get("patient_id") and event.get("event") != "patient_summary":
            events_by_patient[str(event["patient_id"])].append(event)

    arrivals_by_hour = [0] * 24
    for event in trace:
        minute = int(event.get("minute", -1))
        if event.get("event") == "arrived" and start <= minute < end:
            arrivals_by_hour[((minute - start) % MINUTES_PER_DAY) // 60] += 1

    category_counts = {f"C{i}": 0 for i in range(1, 6)}
    for patient in patient_rows:
        if start <= int(patient.get("arrival_minute", -1)) < end:
            category = patient.get("triage_category")
            if category in category_counts:
                category_counts[category] += 1

    stages: dict[str, list[float]] = {
        "Categorización": [], "Espera atención": [],
        "Egreso": [], "Hospitalización": [],
    }
    for patient in patient_rows:
        patient_id = str(patient["patient_id"])
        if not start <= int(patient.get("arrival_minute", -1)) < end:
            continue
        patient_events = sorted(events_by_patient.get(patient_id, []),
                                key=lambda row: int(row.get("minute", -1)))
        at = lambda name: next((int(row["minute"]) for row in patient_events
                                if row.get("event") == name), None)
        arrival = int(patient["arrival_minute"])
        triaged = at("triage_completed")
        consulted = at("called_to_consult")
        if triaged is not None:
            stages["Categorización"].append(max(0, triaged - arrival))
        if triaged is not None and consulted is not None:
            stages["Espera atención"].append(max(0, consulted - triaged))
        status = patient.get("status")
        terminal = next((int(row["minute"]) for row in reversed(patient_events)
                         if row.get("event") in {"discharged", "hospitalized"}), None)
        last_care = next((int(row["minute"]) for row in reversed(patient_events)
                          if row.get("event") == "care_stage_completed"), None)
        if terminal is not None and last_care is not None:
            target = "Hospitalización" if status == "hospitalized" else "Egreso"
            stages[target].append(max(0, terminal - last_care))

    def intervals(start_events: set[str], end_events: set[str],
                  condition=lambda event: True) -> list[tuple[int, int]]:
        active: dict[str, int] = {}
        found: list[tuple[int, int]] = []
        for event in trace:
            kind = event.get("event")
            minute = int(event.get("minute", -1))
            pid = str(event.get("patient_id", ""))
            if not pid:
                continue
            if kind in start_events and condition(event):
                if pid not in active:
                    active[pid] = minute
            elif kind in end_events and pid in active:
                found.append((active.pop(pid), minute))
        found.extend((opened, end) for opened in active.values())
        return [(max(start, a), min(end, b)) for a, b in found
                if min(end, b) > max(start, a)]

    def utilization(spans: list[tuple[int, int]], capacity: int) -> float:
        busy = sum(b - a for a, b in spans)
        return round(100 * busy / (max(1, capacity) * duration), 1) if capacity else 0.0

    resources = config["resources"]
    boxes = int(resources.get("consultation_boxes", 0))
    attention_nurses = int(resources.get("box_nurses", 0))
    triage_nurses = int(resources.get("triage_nurses", 0))
    cabins = int(resources.get("prioria_cabins", 0))
    doctors = sum(int(v) for v in resources["physicians_by_specialty"].values())
    box_spans = intervals({"care_stage_started"}, {"care_stage_completed"},
                          lambda e: e.get("resource") == "box")
    doctor_spans = intervals({"physician_attended_c1", "care_stage_started"},
                             {"care_stage_completed"},
                             lambda e: bool(e.get("physician_id")))
    nurse_spans = intervals({"nurse_attended_c1", "care_stage_started",
                              "treatment_nurse_procedure_started"},
                            {"care_stage_completed"},
                            lambda e: e.get("event") == "treatment_nurse_procedure_started"
                            or (e.get("event") == "nurse_attended_c1"
                                and e.get("nurse_source") == "attention")
                            or e.get("resource") == "box"
                            or (e.get("resource") == "resuscitation"
                                and e.get("nurse_source") == "attention"))
    triage_spans = intervals({"triage_started", "nurse_attended_c1"},
                             {"triage_completed", "care_stage_completed"},
                             lambda e: e.get("event") == "triage_started"
                             or e.get("nurse_source") == "triage_fallback")
    cabin_spans = intervals({"triage_started"}, {"triage_completed"},
                            lambda e: e.get("mode") == "prioria")

    chair_minutes = {
        "general": int(summary.get("chair_occupied_minutes_general", 0)),
        "c2": int(summary.get("chair_occupied_minutes_c2", 0)),
    }
    chair_capacity = {
        "general": int(resources.get("general_waiting_chairs", 0)),
        "c2": int(resources.get("c2_waiting_chairs", 0)),
    }
    utilization_rows = [
        {"label": "Sillas sala general", "value": round(100 * chair_minutes["general"] / (chair_capacity["general"] * duration), 1) if chair_capacity["general"] else 0},
        {"label": "Sillas sala C2", "value": round(100 * chair_minutes["c2"] / (chair_capacity["c2"] * duration), 1) if chair_capacity["c2"] else 0},
        {"label": "Médicos", "value": utilization(doctor_spans, doctors)},
        {"label": "Enfermeras triage", "value": utilization(triage_spans, triage_nurses)},
        {"label": "Enfermeras atención", "value": utilization(nurse_spans, attention_nurses)},
        {"label": "Cabinas PriorIA", "value": utilization(cabin_spans, cabins)},
        {"label": "Boxes", "value": utilization(box_spans, boxes)},
    ]
    return {
        "arrivals_by_hour": arrivals_by_hour,
        "category_counts": category_counts,
        "abandonment_percent": round(100 * float(summary.get("abandonment_rate", 0)), 1),
        "wait_by_stage": [
            {"label": label, "minutes": round(sum(values) / len(values), 1) if values else None,
             "n": len(values)}
            for label, values in stages.items()
        ],
        "utilization": utilization_rows,
        "period_minutes": duration,
    }
