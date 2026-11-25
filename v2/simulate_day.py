#!/usr/bin/env python3
"""Minute-step synthetic patient-flow simulation for PriorIA V2."""
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
    """Simulate one synthetic patient cohort through admission to disposition."""
    profiles = generate_patients(config)
    rng = random.Random(int(config["seed"]) + 1)
    resources = config["resources"]
    care = config["care"]
    triage = config["triage"]
    admission = config["admission"]
    max_time = int(config.get("max_simulation_minutes", 10080))

    areas = WaitingAreas(
        general_chairs=int(resources["general_waiting_chairs"]),
        c2_chairs=int(resources["c2_waiting_chairs"]),
    )
    physicians: list[dict[str, Any]] = []
    physician_id_by_box: dict[str, dict[str, Any]] = {}
    for specialty, count in resources["physicians_by_specialty"].items():
        for _ in range(int(count)):
            box_number = len(physicians) + 1
            physician = {
                "physician_id": f"MED-{box_number:02d}",
                "specialty": specialty,
                "box_id": f"BOX-{box_number:02d}",
                "available": True,
            }
            physicians.append(physician)
            physician_id_by_box[physician["physician_id"]] = physician
    configured_boxes = int(resources["consultation_boxes"])
    if len(physicians) != configured_boxes:
        raise ValueError(
            "Cada médico activo debe tener un box exclusivo: la suma de "
            "physicians_by_specialty debe coincidir con consultation_boxes."
        )
    admission_free = int(resources["admission_desks"])
    boxes_free = len(physicians)
    box_nurses_free = int(resources.get("box_nurses", boxes_free))
    resuscitation_free = int(resources["resuscitation_beds"])

    patients = {p["patient_id"]: {
        **p, "status": "not_arrived", "total_wait_minutes": 0,
        "admission_completed_at": None, "triage_completed_at": None,
        "first_consult_start": None, "terminal_minute": None,
        "stages_completed": 0, "last_room": None,
        "treatment_failures": 0, "treatments_received": 0,
    } for p in profiles}
    arrivals: dict[int, list[str]] = {}
    for p in profiles:
        arrivals.setdefault(int(p["arrival_minute"]), []).append(p["patient_id"])

    admission_queue: list[str] = []
    admission_active: list[dict[str, Any]] = []
    triage_queues: dict[str, list[str]] = {"prioria": [], "human": []}
    pretriage_waiting: dict[str, dict[str, Any]] = {}
    triage_active: list[dict[str, Any]] = []
    waiting: dict[str, dict[str, Any]] = {}
    care_active: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    triage_busy_nurses = prioria_busy = human_triage_busy = 0
    max_general_chairs = max_c2_chairs = 0
    max_general_population = max_c2_population = 0
    max_standing_general = max_standing_c2 = 0
    standing_patient_minutes = 0

    def log(minute: int, event: str, patient_id: str | None = None,
            **fields: Any) -> None:
        row = {"minute": minute, "event": event}
        if patient_id is not None:
            row["patient_id"] = patient_id
        row.update(fields)
        trace.append(row)

    def record_occupancy() -> None:
        nonlocal max_general_chairs, max_c2_chairs
        nonlocal max_general_population, max_c2_population
        nonlocal max_standing_general, max_standing_c2, standing_patient_minutes
        general_standing = len(areas.general.standing_patient_ids)
        c2_standing = len(areas.c2.standing_patient_ids)
        standing_patient_minutes += general_standing + c2_standing
        max_general_chairs = max(max_general_chairs, areas.general.occupied_chairs)
        max_c2_chairs = max(max_c2_chairs, areas.c2.occupied_chairs)
        max_general_population = max(max_general_population, areas.general.active_count)
        max_c2_population = max(max_c2_population, areas.c2.active_count)
        max_standing_general = max(max_standing_general, general_standing)
        max_standing_c2 = max(max_standing_c2, c2_standing)

    def make_waiting_patient(patient_id: str, minute: int,
                             stage: str) -> WaitingPatient:
        p = patients[patient_id]
        return WaitingPatient(
            patient_id=patient_id,
            queue_entered_minute=minute,
            triage_category=p["triage_category"],
            age_group=p["age_group"],
            abandonment_threshold=float(p["abandonment_threshold"]),
            waiting_stage=stage,
            prior_wait_minutes=int(p["total_wait_minutes"]),
        )

    def enter_pretriage_wait(patient_id: str, minute: int, mode: str) -> None:
        p = patients[patient_id]
        area, seated = areas.arrive(
            make_waiting_patient(patient_id, minute, f"waiting_{mode}"),
            area_override="general",
        )
        pretriage_waiting[patient_id] = {
            "mode": mode, "entered": minute, "area": area,
            "sequence": len(trace),
        }
        triage_queues[mode].append(patient_id)
        p["last_room"] = area
        p["status"] = f"waiting_{mode}"
        log(minute, "entered_pretriage_wait", patient_id, area=area,
            next_step=mode, seated=seated)
        record_occupancy()

    def enter_clinical_wait(patient_id: str, minute: int, task: str,
                            stage: str = "initial") -> None:
        p = patients[patient_id]
        if p["triage_category"] == "C1":
            area, seated = areas.arrive(make_waiting_patient(patient_id, minute, stage))
        else:
            area, seated = areas.arrive(make_waiting_patient(patient_id, minute, stage))
        p["last_room"] = area
        p["current_queue_entered"] = minute
        p["status"] = f"waiting_{task}"
        waiting[patient_id] = {
            "task": task, "stage": stage, "entered": minute, "area": area,
            "sequence": len(trace),
        }
        log(minute, "entered_waiting_area", patient_id, area=area,
            task=task, stage=stage, seated=seated)
        record_occupancy()
        if p["triage_category"] == "C1":
            assign_c1_nurses(minute)
            assign_c1_doctors(minute)

    def assign_c1_nurses(minute: int) -> None:
        """Send an available attention nurse to C1, using triage as last resort."""
        nonlocal box_nurses_free, triage_busy_nurses
        c1_queue = sorted(
            (pid for pid, item in waiting.items()
             if patients[pid]["triage_category"] == "C1"
             and item["task"] == "consult" and "nurse_source" not in item),
            key=lambda pid: (waiting[pid]["entered"], waiting[pid]["sequence"], pid),
        )
        for patient_id in c1_queue:
            if box_nurses_free > 0:
                box_nurses_free -= 1
                source = "attention"
            elif triage_busy_nurses < int(resources["triage_nurses"]):
                triage_busy_nurses += 1
                source = "triage_fallback"
            else:
                break
            waiting[patient_id]["nurse_source"] = source
            patients[patient_id]["status"] = "c1_nurse_assigned_resuscitation"
            log(minute, "nurse_attended_c1", patient_id,
                nurse_source=source, waiting_for="physician_or_resuscitation_bed")

    def assign_c1_doctors(minute: int) -> None:
        """Reserve the first free physician to respond to a C1 emergency."""
        nonlocal boxes_free
        c1_queue = sorted(
            (pid for pid, item in waiting.items()
             if patients[pid]["triage_category"] == "C1"
             and item["task"] == "consult" and "physician_id" not in item),
            key=lambda pid: (waiting[pid]["entered"], waiting[pid]["sequence"], pid),
        )
        for patient_id in c1_queue:
            physician = next((item for item in physicians if item["available"]), None)
            if physician is None:
                break
            physician["available"] = False
            boxes_free -= 1
            waiting[patient_id]["physician_id"] = physician["physician_id"]
            patients[patient_id]["status"] = "c1_physician_assigned_resuscitation"
            log(minute, "physician_attended_c1", patient_id,
                physician_id=physician["physician_id"],
                physician_specialty=physician["specialty"],
                home_box=physician["box_id"],
                waiting_for="resuscitation_bed")

    def leave_pretriage(patient_id: str, minute: int,
                        remove_from_triage_queue: bool) -> str:
        entry = pretriage_waiting.pop(patient_id)
        if remove_from_triage_queue:
            queue = triage_queues[entry["mode"]]
            if patient_id in queue:
                queue.remove(patient_id)
        p = patients[patient_id]
        p["total_wait_minutes"] += max(0, minute - int(entry["entered"]))
        area, seat_status = areas.status(patient_id)
        areas.leave(patient_id, minute)
        p["current_queue_entered"] = None
        event = "left_pretriage_after_abandonment" if remove_from_triage_queue else "called_to_triage"
        log(minute, event, patient_id, mode=entry["mode"],
            previous_area=area, seat_released=seat_status == "seated")
        return seat_status

    def leave_waiting(patient_id: str, minute: int) -> str:
        entry = waiting.pop(patient_id)
        p = patients[patient_id]
        p["total_wait_minutes"] += max(0, minute - int(entry["entered"]))
        if entry["area"] == "resuscitation":
            seat_status = "no_waiting_chair"
        else:
            area, seat_status = areas.status(patient_id)
            areas.leave(patient_id, minute)
            log(minute, "seat_released", patient_id, area=area,
                was_seated=seat_status == "seated")
        p["current_queue_entered"] = None
        return seat_status

    def return_to_c2_wait(patient_id: str, minute: int, task: str) -> None:
        p = patients[patient_id]
        area, seated = areas.return_to_c2(
            make_waiting_patient(patient_id, minute, task), minute,
            waiting_stage="treatment",
        )
        p["last_room"] = area
        p["current_queue_entered"] = minute
        p["status"] = "waiting_treatment"
        waiting[patient_id] = {
            "task": task, "stage": "treatment", "entered": minute,
            "area": area, "sequence": len(trace),
        }
        log(minute, "returned_to_c2", patient_id, reason="treatment",
            seated=seated, chair_capacity=areas.c2.chair_capacity)
        record_occupancy()

    def finish_patient(patient_id: str, minute: int, disposition: str,
                       reason: str) -> None:
        p = patients[patient_id]
        p["status"] = disposition
        p["terminal_minute"] = minute
        exit_route = "main_door" if disposition in {"discharged", "abandoned"} else "hospital_transfer"
        log(minute, disposition, patient_id, reason=reason, exit_route=exit_route,
            treatment_failures=p["treatment_failures"])

    def treatment_probability(patient_id: str) -> float:
        category = patients[patient_id]["triage_category"]
        return float(care["treatment_probability_by_category"][category])

    def complete_care(event: dict[str, Any], minute: int) -> None:
        nonlocal boxes_free, box_nurses_free, resuscitation_free
        nonlocal triage_busy_nurses
        patient_id = event["patient_id"]
        task = event["task"]
        resource = event["resource"]
        physician_id = event.get("physician_id")
        if physician_id is not None:
            physician = physician_id_by_box[physician_id]
            physician["available"] = True
            boxes_free += 1
            if resource == "resuscitation":
                log(minute, "physician_returned_to_box", patient_id,
                    physician_id=physician_id, home_box=physician["box_id"])
        if resource == "box":
            if event.get("box_nurse"):
                box_nurses_free += 1
        elif resource == "resuscitation":
            resuscitation_free += 1
            if event.get("nurse_source") == "attention":
                box_nurses_free += 1
        elif resource == "attention_nurse":
            box_nurses_free += 1
        if event.get("nurse_source") == "triage_fallback":
            triage_busy_nurses -= 1
        patients[patient_id]["stages_completed"] += 1
        log(minute, "care_stage_completed", patient_id, task=task,
            favorable=event.get("favorable"))

        p = patients[patient_id]
        if task == "consult":
            if rng.random() < treatment_probability(patient_id):
                return_to_c2_wait(patient_id, minute, "treatment")
            else:
                category = p["triage_category"]
                if category in {"C4", "C5"}:
                    finish_patient(patient_id, minute, "discharged",
                                   "sin_tratamiento_C4_C5")
                else:
                    probability = float(care[
                        "hospitalization_probability_without_treatment_by_category"][category])
                    disposition = "hospitalized" if rng.random() < probability else "discharged"
                    finish_patient(patient_id, minute, disposition,
                                   "sin_tratamiento_indicado")
        elif task == "treatment_nursing":
            low = int(care["treatment_duration_minutes"]["minimum"])
            high = int(care["treatment_duration_minutes"]["maximum"])
            if low < 30 or high > 120 or low > high:
                raise ValueError("La duración de tratamiento debe quedar entre 30 y 120 minutos.")
            duration = rng.randint(low, high)
            p["status"] = "in_treatment_observation"
            care_active.append({
                "finish": minute + duration, "patient_id": patient_id,
                "task": "treatment", "resource": "observation",
                "specialty": None, "box_nurse": False,
            })
            log(minute, "treatment_observation_started", patient_id,
                duration_minutes=duration,
                treatment_number=p["treatments_received"] + 1,
                room="c2")
        elif task == "treatment":
            p["treatments_received"] += 1
            areas.resume_after_treatment(patient_id, minute)
            p["current_queue_entered"] = minute
            p["status"] = "waiting_reevaluation"
            waiting[patient_id] = {
                "task": "reevaluation", "stage": "reevaluation",
                "entered": minute, "area": "c2", "sequence": len(trace),
            }
            area, chair_state = areas.status(patient_id)
            log(minute, "treatment_completed_waiting_reevaluation", patient_id,
                room=area, chair_state=chair_state,
                treatment_number=p["treatments_received"])
            record_occupancy()
        elif task == "reevaluation":
            if rng.random() < float(care["reevaluation_discharge_probability"]):
                finish_patient(patient_id, minute, "discharged",
                               "reevaluacion_favorable")
            else:
                p["treatment_failures"] += 1
                log(minute, "treatment_failed", patient_id,
                    failure_count=p["treatment_failures"])
                max_failures = int(care["max_treatment_failures_before_hospitalization"])
                if p["treatment_failures"] >= max_failures:
                    finish_patient(patient_id, minute, "hospitalized",
                                   "dos_tratamientos_fallidos")
                else:
                    return_to_c2_wait(patient_id, minute, "treatment")

    def dispatch_admission(minute: int) -> None:
        nonlocal admission_free
        while admission_free > 0 and admission_queue:
            patient_id = admission_queue.pop(0)
            duration = min(2, _duration(rng, admission["duration_minutes"]))
            admission_free -= 1
            patients[patient_id]["status"] = "admission_in_progress"
            admission_active.append({"finish": minute + duration,
                                     "patient_id": patient_id})
            log(minute, "admission_started", patient_id,
                duration_minutes=duration)

    def dispatch_triage(minute: int) -> None:
        nonlocal triage_busy_nurses, prioria_busy, human_triage_busy
        prioria_capacity = int(resources["prioria_cabins"])
        human_capacity = min(int(resources["triage_stations"]),
                             int(resources["triage_nurses"]))
        while triage_busy_nurses < int(resources["triage_nurses"]):
            mode = None
            if triage_queues["prioria"] and prioria_busy < prioria_capacity:
                mode = "prioria"
            elif triage_queues["human"] and human_triage_busy < human_capacity:
                mode = "human"
            if mode is None:
                break
            patient_id = triage_queues[mode].pop(0)
            leave_pretriage(patient_id, minute, remove_from_triage_queue=False)
            if mode == "prioria":
                prioria_busy += 1
                duration = rng.randint(
                    int(triage["prioria_validation_minutes"]["minimum"]),
                    int(triage["prioria_validation_minutes"]["maximum"]),
                )
            else:
                human_triage_busy += 1
                duration = rng.randint(
                    int(triage["human_categorization_minutes"]["minimum"]),
                    int(triage["human_categorization_minutes"]["maximum"]),
                )
            triage_busy_nurses += 1
            patients[patient_id]["status"] = "triage_in_progress"
            triage_active.append({"finish": minute + duration,
                                  "patient_id": patient_id, "mode": mode})
            log(minute, "triage_started", patient_id, mode=mode,
                duration_minutes=duration)

    def dispatch_care(minute: int) -> None:
        nonlocal boxes_free, box_nurses_free, resuscitation_free
        ordered = sorted(waiting, key=lambda pid: (
            int(patients[pid]["triage_category"][1]),
            waiting[pid]["entered"], waiting[pid]["sequence"], pid))
        for patient_id in ordered:
            if patient_id not in waiting:
                continue
            task = waiting[patient_id]["task"]
            p = patients[patient_id]
            required_specialty = p["required_specialty"]
            if task == "consult" and p["triage_category"] == "C1":
                if resuscitation_free <= 0:
                    continue
                nurse_source = waiting[patient_id].get("nurse_source")
                physician_id = waiting[patient_id].get("physician_id")
                if nurse_source is None or physician_id is None:
                    continue
                physician = physician_id_by_box[physician_id]
                resource = "resuscitation"
            elif task in {"consult", "reevaluation"}:
                if boxes_free <= 0 or box_nurses_free <= 0:
                    continue
                physician = next((item for item in physicians
                                  if item["available"]
                                  and item["specialty"] == required_specialty), None)
                if physician is None:
                    continue
                physician_id = physician["physician_id"]
                resource = "box"
                nurse_source = "attention"
            else:
                continue

            chair_state = leave_waiting(patient_id, minute)
            if resource == "box":
                boxes_free -= 1
                box_nurses_free -= 1
                physician["available"] = False
            else:
                resuscitation_free -= 1

            if task == "consult":
                spec = care["consultation_minutes_by_category"][p["triage_category"]]
                duration = _duration(rng, spec, minimum=5)
                if p["first_consult_start"] is None:
                    p["first_consult_start"] = minute
                log(minute, "called_to_consult", patient_id,
                    seat_released=chair_state == "seated", resource=resource)
            else:
                duration = _duration(rng, care["reevaluation_duration_minutes"], minimum=5)
                log(minute, "called_to_reevaluation", patient_id,
                    seat_released=chair_state == "seated", resource=resource)

            care_active.append({
                "finish": minute + duration, "patient_id": patient_id,
                "task": task, "resource": resource,
                "specialty": physician["specialty"],
                "physician_id": physician_id,
                "box_nurse": resource == "box",
                "nurse_source": nurse_source if resource == "resuscitation" else None,
                "home_box": physician["box_id"],
            })
            p["status"] = f"in_{task}"
            log(minute, "care_stage_started", patient_id, task=task,
                specialty=physician["specialty"], required_specialty=required_specialty,
                physician_id=physician_id, home_box=physician["box_id"],
                patient_moves_to=physician["box_id"] if resource == "box" else "resuscitation",
                resource=resource,
                duration_minutes=duration,
                nurse_source=nurse_source if resource == "resuscitation" else None)

    def dispatch_treatments(minute: int) -> None:
        nonlocal box_nurses_free
        order = sorted(
            (pid for pid, item in waiting.items() if item["task"] == "treatment"),
            key=lambda pid: (waiting[pid]["entered"], waiting[pid]["sequence"], pid),
        )
        for patient_id in order:
            if box_nurses_free <= 0 or patient_id not in waiting:
                break
            entry = waiting.pop(patient_id)
            p = patients[patient_id]
            p["total_wait_minutes"] += max(0, minute - int(entry["entered"]))
            chair_before = areas.status(patient_id)[1]
            areas.pause_for_treatment(patient_id, minute)
            p["current_queue_entered"] = None
            p["status"] = "nurse_procedure"
            box_nurses_free -= 1
            nurse_low = int(care["treatment_nurse_minutes"]["minimum"])
            nurse_high = int(care["treatment_nurse_minutes"]["maximum"])
            if nurse_low < 5 or nurse_high > 10 or nurse_low > nurse_high:
                raise ValueError("La atención de enfermería debe quedar entre 5 y 10 minutos.")
            duration = rng.randint(nurse_low, nurse_high)
            care_active.append({
                "finish": minute + duration, "patient_id": patient_id,
                "task": "treatment_nursing", "resource": "attention_nurse",
                "specialty": None, "box_nurse": False,
            })
            log(minute, "treatment_nurse_procedure_started", patient_id,
                room="c2", chair_state=chair_before,
                duration_minutes=duration, nurse_source="attention",
                treatment_number=p["treatments_received"] + 1)

    for minute in range(max_time + 1):
        for patient_id in arrivals.get(minute, []):
            p = patients[patient_id]
            p["status"] = "admission_queue"
            admission_queue.append(patient_id)
            entry = "main_door" if p["arrival_mode"] == "own_means" else p["arrival_mode"]
            log(minute, "arrived", patient_id, entry_route=entry,
                arrival_mode=p["arrival_mode"], age_group=p["age_group"],
                cause_group=p["deis_cause_group"], uses_prioria=p["uses_prioria"],
                probable_diagnosis_group=p["probable_diagnosis_group"],
                required_specialty=p["required_specialty"],
                adoption_probability=p["prioria_adoption_probability"])

        finished_admission = [e for e in admission_active if e["finish"] <= minute]
        admission_active = [e for e in admission_active if e["finish"] > minute]
        for event in finished_admission:
            admission_free += 1
            patient_id = event["patient_id"]
            p = patients[patient_id]
            p["admission_completed_at"] = minute
            mode = "prioria" if p["uses_prioria"] else "human"
            log(minute, "admission_completed", patient_id,
                next_step=mode, adoption_probability=p["prioria_adoption_probability"])
            enter_pretriage_wait(patient_id, minute, mode)

        finished_triage = [e for e in triage_active if e["finish"] <= minute]
        triage_active = [e for e in triage_active if e["finish"] > minute]
        for event in finished_triage:
            triage_busy_nurses -= 1
            if event["mode"] == "prioria":
                prioria_busy -= 1
            else:
                human_triage_busy -= 1
            patient_id = event["patient_id"]
            patients[patient_id]["triage_completed_at"] = minute
            enter_clinical_wait(patient_id, minute, "consult")
            log(minute, "triage_completed", patient_id, mode=event["mode"],
                category=patients[patient_id]["triage_category"],
                destination=patients[patient_id]["last_room"])

        finished_care = [e for e in care_active if e["finish"] <= minute]
        care_active = [e for e in care_active if e["finish"] > minute]
        for event in finished_care:
            complete_care(event, minute)

        dispatch_admission(minute)
        dispatch_triage(minute)
        assign_c1_nurses(minute)
        assign_c1_doctors(minute)
        dispatch_care(minute)
        dispatch_treatments(minute)

        for room_name, room in (("c2", areas.c2), ("general", areas.general)):
            for patient_id, risk in room.abandonment_candidates(
                    minute, abandonment_risk, config["abandonment"]):
                if patient_id in pretriage_waiting:
                    was_standing = room.status(patient_id) == "standing"
                    leave_pretriage(patient_id, minute, remove_from_triage_queue=True)
                elif patient_id in waiting:
                    was_standing = room.status(patient_id) == "standing"
                    leave_waiting(patient_id, minute)
                else:
                    continue
                finish_patient(patient_id, minute, "abandoned", "umbral_de_abandono")
                log(minute, "abandonment_risk_crossed", patient_id,
                    room=room_name, risk=round(risk, 5), standing=was_standing)

        record_occupancy()
        if all(p["status"] in TERMINAL for p in patients.values()):
            break
    else:
        for p in patients.values():
            if p["status"] not in TERMINAL:
                p["status"] = "unresolved"
                p["terminal_minute"] = max_time

    statuses = [p["status"] for p in patients.values()]
    finished = [p for p in patients.values()
                if p["status"] in {"discharged", "hospitalized"}]
    total_waits = [p["total_wait_minutes"] for p in patients.values()]
    arrival_to_triage = [p["triage_completed_at"] - p["arrival_minute"]
                         for p in patients.values() if p["triage_completed_at"] is not None]
    arrival_to_consult = [p["first_consult_start"] - p["arrival_minute"]
                          for p in patients.values() if p["first_consult_start"] is not None]
    category_counts = {f"C{i}": sum(p["triage_category"] == f"C{i}"
                                    for p in patients.values()) for i in range(1, 6)}
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
        "physicians_active": len(physicians),
        "physician_box_assignments": [
            {"physician_id": physician["physician_id"],
             "specialty": physician["specialty"],
             "box_id": physician["box_id"]}
            for physician in physicians
        ],
        "prioria_users": sum(bool(p["uses_prioria"]) for p in patients.values()),
        "prioria_users_by_age": {
            group: sum(bool(p["uses_prioria"]) and p["age_group"] == group
                       for p in patients.values())
            for group in ("adult_young", "adult_middle", "adult_older")
        },
        "arrival_mode_counts": {mode: sum(p["arrival_mode"] == mode
                                            for p in patients.values())
                                for mode in ("own_means", "ambulance", "police")},
        "treatments_started": sum(r["event"] == "treatment_observation_started" for r in trace),
        "treatment_failures": sum(r["event"] == "treatment_failed" for r in trace),
        "mean_total_wait_minutes": round(sum(total_waits) / len(total_waits), 2) if total_waits else 0,
        "mean_arrival_to_triage_minutes": round(
            sum(arrival_to_triage) / len(arrival_to_triage), 2
        ) if arrival_to_triage else None,
        "mean_arrival_to_first_consult_minutes": round(
            sum(arrival_to_consult) / len(arrival_to_consult), 2
        ) if arrival_to_consult else None,
        "mean_time_to_terminal_minutes": round(
            sum(int(p["terminal_minute"] or 0) - p["arrival_minute"] for p in finished) / len(finished), 2
        ) if finished else None,
        "standing_patient_minutes": standing_patient_minutes,
        "max_general_waiting_chair_occupancy": max_general_chairs,
        "max_c2_waiting_chair_occupancy": max_c2_chairs,
        "max_general_room_population": max_general_population,
        "max_c2_room_population": max_c2_population,
        "max_general_standing": max_standing_general,
        "max_c2_standing": max_standing_c2,
        "last_event_minute": max((r["minute"] for r in trace), default=0),
        "metadata": config.get("metadata", {}),
    }
    patient_rows = [{k: p[k] for k in (
        "patient_id", "arrival_minute", "age", "age_group", "arrival_mode",
        "prioria_adoption_probability", "uses_prioria", "triage_category",
        "deis_cause_group", "probable_diagnosis_group", "required_specialty",
        "chief_complaint", "heart_rate", "respiratory_rate",
        "temperature_c", "spo2_pct", "systolic_bp", "diastolic_bp", "status",
        "total_wait_minutes", "first_consult_start", "terminal_minute",
        "treatments_received", "treatment_failures", "stages_completed",
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
