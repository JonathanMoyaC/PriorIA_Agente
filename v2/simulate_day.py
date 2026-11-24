#!/usr/bin/env python3
"""Minute-step, synthetic patient-flow simulation for PriorIA V2.

This is an operational scenario model, not a clinical decision-support system.
DEIS-derived distributions and uncalibrated pathway assumptions are reported
separately in the output metadata.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path
from typing import Any

try:
    from .generate_patients import abandonment_risk, generate_patients
    from .waiting_room import WaitingAreas, WaitingPatient
except ImportError:  # Running as: python v2/simulate_day.py
    from generate_patients import abandonment_risk, generate_patients
    from waiting_room import WaitingAreas, WaitingPatient


TERMINAL = {"discharged", "hospitalized", "abandoned", "unresolved"}


def _duration(rng: random.Random, spec: dict[str, float], minimum: int = 1) -> int:
    return max(minimum, round(rng.gauss(float(spec["mean"]), float(spec["sd"]))))


def simulate_day(config: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Simulate one 100-patient day and return a summary and event trace."""
    profiles = generate_patients(config)
    rng = random.Random(int(config["seed"]) + 1)
    resources = config["resources"]
    care = config["care"]
    triage = config.get("triage", {})
    max_time = int(config.get("max_simulation_minutes", 10080))

    areas = WaitingAreas(
        general_chairs=int(resources["general_waiting_chairs"]),
        c2_chairs=int(resources["c2_waiting_chairs"]),
    )
    doctors_free = {key: int(value) for key, value in
                    resources["physicians_by_specialty"].items()}
    boxes_free = int(resources["consultation_boxes"])
    box_nurses_free = int(resources.get("box_nurses", boxes_free))
    resuscitation_free = int(resources["resuscitation_beds"])
    procedure_free = int(resources.get("procedure_slots", 0))
    observation_free = int(resources.get("observation_beds", 0))

    patients = {p["patient_id"]: {**p, "status": "not_arrived",
                                  "total_wait_minutes": 0,
                                  "current_queue_entered": None,
                                  "first_consult_start": None,
                                  "terminal_minute": None,
                                  "stages_completed": 0,
                                  "last_room": None}
                for p in profiles}
    arrivals: dict[int, list[str]] = {}
    for p in profiles:
        arrivals.setdefault(int(p["arrival_minute"]), []).append(p["patient_id"])

    triage_queues = {"prioria": [], "human": []}
    triage_active: list[dict[str, Any]] = []
    waiting: dict[str, dict[str, Any]] = {}
    care_active: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    triage_busy_nurses = 0
    prioria_busy = 0
    human_triage_busy = 0
    max_general = max_c2 = 0
    max_general_population = max_c2_population = 0
    max_standing_general = max_standing_c2 = 0
    standing_patient_minutes = 0

    def log(minute: int, event: str, patient_id: str | None = None, **fields: Any) -> None:
        row = {"minute": minute, "event": event}
        if patient_id is not None:
            row["patient_id"] = patient_id
        row.update(fields)
        trace.append(row)

    def enter_clinical_wait(patient_id: str, minute: int,
                            task: str, stage: str = "initial") -> None:
        nonlocal max_general, max_c2, max_general_population, max_c2_population
        nonlocal max_standing_general, max_standing_c2
        p = patients[patient_id]
        wp = WaitingPatient(
            patient_id=patient_id,
            queue_entered_minute=minute,
            triage_category=p["triage_category"],
            age_group=p["age_group"],
            abandonment_threshold=float(p["abandonment_threshold"]),
            waiting_stage=stage,
            prior_wait_minutes=int(p["total_wait_minutes"]),
        )
        if p["triage_category"] == "C1":
            area, seated = "resuscitation", None
        else:
            area, seated = areas.arrive(wp)
        p["last_room"] = area
        p["current_queue_entered"] = minute
        p["status"] = f"waiting_{task}"
        waiting[patient_id] = {"task": task, "stage": stage,
                               "entered": minute, "area": area,
                               "sequence": len(trace)}
        log(minute, "entered_waiting_area", patient_id, area=area,
            task=task, stage=stage, seated=seated)
        max_general = max(max_general, areas.general.occupied_chairs)
        max_c2 = max(max_c2, areas.c2.occupied_chairs)
        max_general_population = max(max_general_population, areas.general.active_count)
        max_c2_population = max(max_c2_population, areas.c2.active_count)
        max_standing_general = max(max_standing_general,
                                   len(areas.general.standing_patient_ids))
        max_standing_c2 = max(max_standing_c2, len(areas.c2.standing_patient_ids))

    def leave_waiting(patient_id: str, minute: int) -> None:
        w = waiting.pop(patient_id)
        p = patients[patient_id]
        p["total_wait_minutes"] += max(0, minute - int(w["entered"]))
        if w["area"] != "resuscitation":
            areas.leave(patient_id, minute)
        p["current_queue_entered"] = None

    def queue_next_stage(patient_id: str, minute: int, task: str,
                         stage: str) -> None:
        p = patients[patient_id]
        wp = WaitingPatient(
            patient_id=patient_id,
            queue_entered_minute=minute,
            triage_category=p["triage_category"],
            age_group=p["age_group"],
            abandonment_threshold=float(p["abandonment_threshold"]),
            prior_wait_minutes=int(p["total_wait_minutes"]),
        )
        area, seated = areas.return_for_procedure_or_observation(
            wp, now_minute=minute,
            waiting_stage="procedure" if task.startswith("procedure") else "observation",
        )
        p["last_room"] = area
        p["current_queue_entered"] = minute
        p["status"] = f"waiting_{task}"
        waiting[patient_id] = {"task": task, "stage": stage,
                               "entered": minute, "area": area,
                               "sequence": len(trace)}
        log(minute, "returned_to_c2", patient_id, area=area,
            task=task, stage=stage, seated=seated)

    def finish_patient(patient_id: str, minute: int,
                       mandatory_hospitalization: bool = False) -> None:
        p = patients[patient_id]
        hospital_probability = float(care["hospitalization_probability_after_care"])
        hospitalized = mandatory_hospitalization or rng.random() < hospital_probability
        p["status"] = "hospitalized" if hospitalized else "discharged"
        p["terminal_minute"] = minute
        log(minute, p["status"], patient_id,
            mandatory_after_failed_second_procedure=mandatory_hospitalization)

    def procedure_probability(patient_id: str) -> float:
        return float(care.get("procedure_probability_by_cause", {}).get(
            patients[patient_id]["deis_cause_group"], 0.0))

    def observation_probability(patient_id: str) -> float:
        return float(care.get("observation_probability_by_cause", {}).get(
            patients[patient_id]["deis_cause_group"], 0.0))

    def complete_care(event: dict[str, Any], minute: int) -> None:
        nonlocal boxes_free, box_nurses_free, resuscitation_free
        nonlocal procedure_free, observation_free
        patient_id = event["patient_id"]
        task = event["task"]
        specialty = event.get("specialty")
        if specialty:
            doctors_free[specialty] += 1
        resource = event["resource"]
        if resource == "box":
            boxes_free += 1
            if event.get("box_nurse"):
                box_nurses_free += 1
        elif resource == "resuscitation":
            resuscitation_free += 1
        elif resource == "procedure":
            procedure_free += 1
        elif resource == "observation":
            observation_free += 1
        patients[patient_id]["stages_completed"] += 1
        log(minute, "care_stage_completed", patient_id, task=task,
            favorable=event.get("favorable"))

        if task == "consult":
            if rng.random() < procedure_probability(patient_id):
                queue_next_stage(patient_id, minute, "procedure_1", "procedure")
            elif rng.random() < observation_probability(patient_id):
                queue_next_stage(patient_id, minute, "observation", "observation")
            else:
                finish_patient(patient_id, minute)
        elif task == "procedure_1":
            favorable = bool(event["favorable"])
            if not favorable:
                queue_next_stage(patient_id, minute, "procedure_2", "procedure")
            elif rng.random() < float(care["observation_after_procedure_probability"]):
                queue_next_stage(patient_id, minute, "observation", "observation")
            else:
                finish_patient(patient_id, minute)
        elif task == "observation":
            if not bool(event["favorable"]):
                queue_next_stage(patient_id, minute, "procedure_2", "procedure")
            else:
                finish_patient(patient_id, minute)
        elif task == "procedure_2":
            finish_patient(patient_id, minute,
                           mandatory_hospitalization=not bool(event["favorable"]))

    def dispatch_triage(minute: int) -> None:
        nonlocal triage_busy_nurses, prioria_busy, human_triage_busy
        r_prioria = int(resources["prioria_cabins"])
        r_human = min(int(resources["triage_stations"]),
                      int(resources["triage_nurses"]))
        while triage_busy_nurses < int(resources["triage_nurses"]):
            mode = None
            if triage_queues["prioria"] and prioria_busy < r_prioria:
                mode = "prioria"
            elif triage_queues["human"] and human_triage_busy < r_human:
                mode = "human"
            if mode is None:
                break
            patient_id = triage_queues[mode].pop(0)
            if mode == "prioria":
                prioria_busy += 1
                spec = triage.get("prioria_duration_minutes", {"mean": 4, "sd": 0.5})
                duration = min(5, _duration(rng, spec))
            else:
                human_triage_busy += 1
                spec = triage.get("human_duration_minutes", {"mean": 10, "sd": 2})
                duration = _duration(rng, spec)
            triage_busy_nurses += 1
            patients[patient_id]["status"] = "triage_in_progress"
            triage_active.append({"finish": minute + duration,
                                  "patient_id": patient_id, "mode": mode})
            log(minute, "triage_started", patient_id, mode=mode,
                duration_minutes=duration)

    def dispatch_care(minute: int) -> None:
        nonlocal boxes_free, box_nurses_free, resuscitation_free
        nonlocal procedure_free, observation_free
        ordered = sorted(waiting, key=lambda pid: (
            int(patients[pid]["triage_category"][1]),
            waiting[pid]["entered"], waiting[pid]["sequence"], pid))
        for patient_id in ordered:
            if patient_id not in waiting:
                continue
            task = waiting[patient_id]["task"]
            p = patients[patient_id]
            specialty = config["specialty_by_cause"].get(p["deis_cause_group"])
            resource: str | None = None
            if task == "consult":
                if specialty is None or doctors_free.get(specialty, 0) <= 0:
                    continue
                if p["triage_category"] == "C1":
                    if resuscitation_free <= 0:
                        continue
                    resource = "resuscitation"
                else:
                    if boxes_free <= 0 or box_nurses_free <= 0:
                        continue
                    resource = "box"
            elif task.startswith("procedure"):
                if specialty is None or doctors_free.get(specialty, 0) <= 0 or procedure_free <= 0:
                    continue
                resource = "procedure"
            elif task == "observation":
                if observation_free <= 0:
                    continue
                resource = "observation"
            if resource is None:
                continue

            leave_waiting(patient_id, minute)
            if resource == "box":
                boxes_free -= 1
                box_nurses_free -= 1
            elif resource == "resuscitation":
                resuscitation_free -= 1
            elif resource == "procedure":
                procedure_free -= 1
            elif resource == "observation":
                observation_free -= 1
            if task != "observation":
                doctors_free[specialty] -= 1

            if task == "consult":
                spec = care["consultation_minutes_by_category"][p["triage_category"]]
                duration = _duration(rng, spec, minimum=5)
                if p["first_consult_start"] is None:
                    p["first_consult_start"] = minute
            elif task.startswith("procedure"):
                duration = _duration(rng, care["procedure_duration_minutes"], minimum=5)
            else:
                duration = _duration(rng, care["observation_duration_minutes"], minimum=5)

            completion = {"finish": minute + duration, "patient_id": patient_id,
                          "task": task, "resource": resource,
                          "specialty": specialty if task != "observation" else None,
                          "box_nurse": resource == "box"}
            if task == "procedure_1":
                completion["favorable"] = rng.random() < float(
                    care["procedure_favorable_probability"])
            elif task == "procedure_2":
                completion["favorable"] = rng.random() < float(
                    care["second_procedure_favorable_probability"])
            elif task == "observation":
                completion["favorable"] = rng.random() < float(
                    care["observation_favorable_probability"])
            care_active.append(completion)
            p["status"] = f"in_{task}"
            log(minute, "care_stage_started", patient_id, task=task,
                specialty=specialty, resource=resource,
                duration_minutes=duration)

    for minute in range(max_time + 1):
        for patient_id in arrivals.get(minute, []):
            p = patients[patient_id]
            p["status"] = "triage_queue"
            mode = "prioria" if p["uses_prioria"] else "human"
            triage_queues[mode].append(patient_id)
            log(minute, "arrived", patient_id, arrival_mode=p["arrival_mode"],
                age_group=p["age_group"], category=p["triage_category"],
                cause_group=p["deis_cause_group"], uses_prioria=p["uses_prioria"])

        completed_triage = [e for e in triage_active if e["finish"] <= minute]
        triage_active = [e for e in triage_active if e["finish"] > minute]
        for event in completed_triage:
            triage_busy_nurses -= 1
            if event["mode"] == "prioria":
                prioria_busy -= 1
            else:
                human_triage_busy -= 1
            enter_clinical_wait(event["patient_id"], minute, "consult")
            patients[event["patient_id"]]["status"] = "waiting_consult"
            log(minute, "triage_completed", event["patient_id"], mode=event["mode"])

        completed_care = [e for e in care_active if e["finish"] <= minute]
        care_active = [e for e in care_active if e["finish"] > minute]
        for event in completed_care:
            complete_care(event, minute)

        dispatch_triage(minute)

        for room_name, room in (("c2", areas.c2), ("general", areas.general)):
            for patient_id, risk in room.abandonment_candidates(
                    minute, abandonment_risk, config["abandonment"]):
                if patient_id not in waiting:
                    continue
                was_standing = room.status(patient_id) == "standing"
                leave_waiting(patient_id, minute)
                patients[patient_id]["status"] = "abandoned"
                patients[patient_id]["terminal_minute"] = minute
                log(minute, "abandoned", patient_id, room=room_name,
                    risk=round(risk, 5), standing=was_standing)

        standing_patient_minutes += len(areas.general.standing_patient_ids)
        standing_patient_minutes += len(areas.c2.standing_patient_ids)
        max_general = max(max_general, areas.general.occupied_chairs)
        max_c2 = max(max_c2, areas.c2.occupied_chairs)
        max_general_population = max(max_general_population, areas.general.active_count)
        max_c2_population = max(max_c2_population, areas.c2.active_count)
        max_standing_general = max(max_standing_general,
                                   len(areas.general.standing_patient_ids))
        max_standing_c2 = max(max_standing_c2, len(areas.c2.standing_patient_ids))
        dispatch_care(minute)

        if all(p["status"] in TERMINAL for p in patients.values()):
            break
    else:
        for p in patients.values():
            if p["status"] not in TERMINAL:
                p["status"] = "unresolved"
                p["terminal_minute"] = max_time

    statuses = [p["status"] for p in patients.values()]
    completed = [p for p in patients.values() if p["status"] in {"discharged", "hospitalized"}]
    total_waits = [p["total_wait_minutes"] for p in patients.values()]
    arrival_to_consult = [
        p["first_consult_start"] - p["arrival_minute"]
        for p in patients.values() if p["first_consult_start"] is not None
    ]
    category_counts = {f"C{i}": sum(p["triage_category"] == f"C{i}" for p in patients.values())
                       for i in range(1, 6)}
    summary = {
        "scenario_id": config.get("scenario_id", "v2"),
        "seed": int(config["seed"]),
        "generated": len(patients),
        "discharged": statuses.count("discharged"),
        "hospitalized": statuses.count("hospitalized"),
        "abandoned": statuses.count("abandoned"),
        "discharge_rate": round(statuses.count("discharged") / len(statuses), 4),
        "hospitalization_rate": round(statuses.count("hospitalized") / len(statuses), 4),
        "abandonment_rate": round(statuses.count("abandoned") / len(statuses), 4),
        "unresolved": statuses.count("unresolved"),
        "triage_category_counts": category_counts,
        "prioria_users": sum(bool(p["uses_prioria"]) for p in patients.values()),
        "arrival_mode_counts": {mode: sum(p["arrival_mode"] == mode for p in patients.values())
                                for mode in ("own_means", "ambulance", "police")},
        "mean_total_wait_minutes": round(sum(total_waits) / len(total_waits), 2) if total_waits else 0,
        "mean_arrival_to_first_consult_minutes": round(
            sum(arrival_to_consult) / len(arrival_to_consult), 2
        ) if arrival_to_consult else None,
        "mean_time_to_terminal_minutes": round(
            sum((p["terminal_minute"] or 0) - p["arrival_minute"] for p in completed) / len(completed), 2
        ) if completed else None,
        "standing_patient_minutes": standing_patient_minutes,
        "max_general_waiting_occupancy": max_general,
        "max_c2_waiting_occupancy": max_c2,
        "max_general_room_population": max_general_population,
        "max_c2_room_population": max_c2_population,
        "max_general_standing": max_standing_general,
        "max_c2_standing": max_standing_c2,
        "last_event_minute": max((r["minute"] for r in trace), default=0),
        "metadata": config.get("metadata", {}),
    }
    patient_rows = [{k: p[k] for k in (
        "patient_id", "arrival_minute", "age", "age_group", "arrival_mode",
        "uses_prioria", "triage_category", "deis_cause_group", "chief_complaint",
        "heart_rate", "respiratory_rate", "temperature_c", "spo2_pct",
        "systolic_bp", "diastolic_bp", "status",
        "total_wait_minutes", "first_consult_start", "terminal_minute", "stages_completed"
    )} for p in patients.values()]
    return summary, trace + [{"event": "patient_summary", **row} for row in patient_rows]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/v2"))
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    metadata = config.get("metadata", {})
    if metadata.get("deis_values_verified") is not True and metadata.get("test_fixture") is not True:
        raise SystemExit("La configuración debe usar distribuciones DEIS verificadas o declararse como fixture de prueba.")
    summary, trace = simulate_day(config)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (args.output_dir / "trace.jsonl").open("w", encoding="utf-8") as stream:
        for row in trace:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    patient_rows = [row for row in trace if row.get("event") == "patient_summary"]
    if patient_rows:
        with (args.output_dir / "patients.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(patient_rows[0]))
            writer.writeheader()
            writer.writerows(patient_rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
