"""Canvas keyframe generation based on the authored V3 floorplan routes."""
from __future__ import annotations

import re
import hashlib
from typing import Any

def _column_number(label: str) -> int:
    value = 0
    for char in label:
        value = value * 26 + ord(char) - ord("A") + 1
    return value

def _grid_point(coordinate: str) -> tuple[float, float]:
    match = re.fullmatch(r"([A-Z]+)(\d{2})", coordinate)
    if not match:
        raise ValueError(f"Coordenada inválida: {coordinate}")
    # Coordinates are cell centers, with the first row at the top of the plan.
    return _column_number(match.group(1)) - 0.5, int(match.group(2)) - 0.5

def _orthogonal_path(coordinates: list[str]) -> list[tuple[float, float]]:
    path = [_grid_point(coordinates[0])]
    for coordinate in coordinates[1:]:
        x0, y0 = path[-1]
        x1, y1 = _grid_point(coordinate)
        # Route waypoints are connected with Manhattan segments, no diagonals.
        path.extend([(x1, y0), (x1, y1)])
    return path

def _point_at_distance(path: list[tuple[float, float]], distance_px: float,
                       cell_width: float, cell_height: float) -> tuple[float, float]:
    remaining = max(0.0, distance_px)
    for start, end in zip(path, path[1:]):
        segment = abs(end[0] - start[0]) * cell_width + abs(end[1] - start[1]) * cell_height
        if segment == 0:
            continue
        if remaining <= segment:
            fraction = remaining / segment
            return start[0] + (end[0] - start[0]) * fraction, start[1] + (end[1] - start[1]) * fraction
        remaining -= segment
    return path[-1]

def _column_label(number: int) -> str:
    label = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        label = chr(ord("A") + remainder) + label
    return label

def _walkable_cells(layout: dict[str, Any], internal: bool) -> list[str]:
    """Return corridor cells for illustrative standing positions."""
    cells: list[str] = []
    for group in layout.get("walkable_areas", {}).get("groups", []):
        for rectangle in group.get("rectangles", []):
            x0, y0 = _grid_point(rectangle["from"])
            x1, y1 = _grid_point(rectangle["to"])
            min_col, max_col = sorted((round(x0 + 0.5), round(x1 + 0.5)))
            min_row, max_row = sorted((round(y0 + 0.5), round(y1 + 0.5)))
            for row in range(min_row, max_row + 1):
                if (row > 20) != internal:
                    continue
                for col in range(min_col, max_col + 1):
                    cells.append(f"{_column_label(col)}{row:02d}")
    return list(dict.fromkeys(cells))

def patient_states_at_minute(layout: dict[str, Any], trace: list[dict[str, Any]],
                             minute: int, config: dict[str, Any]
                             ) -> list[dict[str, Any]]:
    """Reconstruct an illustrative patient snapshot from the V2 event log."""
    resources = layout["resource_locations"]
    entrances = resources["own_means_access_and_exit"]["coordinates"]
    admission = [item["coordinate"] for item in resources["admission_desks"]["positions"]]
    cabins = [item["coordinate"] for item in resources["prioria_cabins"]["positions"]]
    triage = [item["coordinate"] for item in resources["triage_seats"]["positions"]]
    boxes = {item["id"]: item["coordinate"]
             for item in resources["physician_boxes"]["positions"]}
    beds = [item["coordinate"] for item in resources["resuscitation_area"]["beds"]]
    general_chairs = layout["waiting_chairs"]["general"]["coordinates"]
    c2_chairs = layout["waiting_chairs"]["c2"]["coordinates"]
    return_routes = next(item for item in layout["route_definitions"]
                         if item["id"] == "c2_waiting_chair_to_box")
    box_beds: dict[str, str] = {}
    for branch in return_routes["branches"]:
        for box_id, destination in branch["destinations_by_box"].items():
            box_beds[box_id] = destination["bed"]

    profiles = {row["patient_id"]: {**row, "zone": "not_arrived"}
                for row in trace if row.get("event") == "patient_summary"}
    patient_order = {patient_id: index for index, patient_id in enumerate(profiles)}
    admission_capacity = max(1, int(config["resources"]["admission_desks"]))
    prioria_capacity = max(1, int(config["resources"]["prioria_cabins"]))
    triage_capacity = max(1, int(config["resources"]["triage_stations"]))

    for event in trace:
        event_minute = int(event.get("minute", -1))
        if event_minute > minute or event.get("event") == "patient_summary":
            continue
        patient_id = event.get("patient_id")
        if patient_id not in profiles:
            continue
        patient = profiles[patient_id]
        kind = event.get("event")
        if kind == "arrived":
            if event.get("arrival_mode") == "ambulance":
                patient.update(zone="ambulance", coordinate="BC03", waiting=False)
            else:
                patient.update(zone="admission_queue",
                               coordinate=entrances[patient_order[patient_id] % len(entrances)],
                               waiting=False)
        elif kind == "admission_started":
            patient.update(zone="admission", coordinate=admission[patient_order[patient_id] % admission_capacity], waiting=False)
        elif kind == "entered_pretriage_wait":
            patient.update(zone="general", area="general", waiting=True,
                           seated=bool(event.get("seated")),
                           chair_index=event.get("chair_index"),
                           entered=event_minute, event_order=len(patient_order))
        elif kind == "triage_started":
            if event.get("mode") == "prioria":
                coord = cabins[patient_order[patient_id] % prioria_capacity]
                zone = "prioria"
            else:
                coord = triage[patient_order[patient_id] % triage_capacity]
                zone = "triage"
            patient.update(zone=zone, coordinate=coord, waiting=False)
        elif kind == "entered_waiting_area":
            area = event.get("area", "general")
            if area == "resuscitation":
                patient.update(zone=area, coordinate=beds[patient_order[patient_id] % len(beds)], waiting=False)
            else:
                patient.update(zone=area, area=area, waiting=True,
                               seated=bool(event.get("seated")),
                               chair_index=event.get("chair_index"),
                               entered=event_minute, event_order=len(patient_order))
        elif kind == "seat_released":
            patient.update(seated=False, chair_index=None)
        elif kind == "seat_assigned":
            patient.update(seated=True, chair_index=event.get("chair_index"))
        elif kind == "care_stage_started":
            if event.get("resource") == "resuscitation":
                bed_index = patient_order[patient_id] % len(beds)
                patient.update(zone="resuscitation", coordinate=beds[bed_index],
                               bed_coordinate=beds[bed_index], resource="resuscitation", waiting=False)
            else:
                box_id = str(event.get("home_box", "BOX-01"))
                box_number = int(box_id.split("-")[-1])
                box_key = f"box_{box_number}"
                patient.update(zone="box", coordinate=box_beds.get(box_key, boxes.get(box_key, "AO37")),
                               home_box=box_id, resource="box", waiting=False)
        elif kind == "returned_to_c2":
            patient.update(zone="c2", area="c2", waiting=True,
                           seated=bool(event.get("seated")),
                           chair_index=event.get("chair_index"),
                           entered=event_minute, event_order=len(patient_order))
        elif kind in {"treatment_nurse_procedure_started", "treatment_observation_started"}:
            patient.update(zone="c2", area="c2", waiting=True,
                           seated=patient.get("seated", True),
                           entered=patient.get("entered", event_minute), event_order=len(patient_order))
        elif kind == "treatment_completed_waiting_reevaluation":
            patient.update(zone="c2", area="c2", waiting=True,
                           seated=event.get("chair_state") == "seated",
                           chair_index=event.get("chair_index"),
                           entered=event_minute, event_order=len(patient_order))
        elif kind in {"called_to_triage", "called_to_consult", "called_to_reevaluation"}:
            patient["waiting"] = False
        elif kind in {"discharged", "hospitalized", "abandoned", "unresolved"}:
            patient.update(zone="departed", waiting=False)

    standing_coords = {
        "general": _walkable_cells(layout, internal=False),
        "c2": _walkable_cells(layout, internal=True),
    }
    visible: list[dict[str, Any]] = []
    for patient_id, patient in profiles.items():
        zone = patient.get("zone")
        if zone in {"not_arrived", "departed"}:
            continue
        if patient.get("waiting") and patient.get("area") in {"general", "c2"}:
            area = patient["area"]
            chairs = general_chairs if area == "general" else c2_chairs
            chair_index = patient.get("chair_index")
            if (patient.get("seated") and isinstance(chair_index, int)
                    and 0 <= chair_index < len(chairs)):
                coord = chairs[chair_index]
            else:
                candidates = standing_coords[area]
                if candidates:
                    # A patient's standing spot is stable across snapshots too.
                    idx = patient_order[patient_id] % len(candidates)
                    coord = candidates[idx]
                else:
                    coord = general_chairs[0] if area == "general" else c2_chairs[0]
        else:
            coord = patient.get("coordinate", entrances[0])
        visible.append({**patient, "patient_id": patient_id, "coordinate": coord})
    return visible

def _transition_waypoints(layout: dict[str, Any], start: dict[str, Any],
                          end: dict[str, Any]) -> list[str]:
    """Use the authored corridor waypoints for common patient transitions."""
    route_defs = {item["id"]: item for item in layout["route_definitions"]}
    origin = start["coordinate"]
    destination = end["coordinate"]
    from_zone, to_zone = start.get("zone"), end.get("zone")
    if from_zone == "admission_queue" and to_zone == "admission":
        return [origin, "AF08", destination]
    if from_zone == "admission" and to_zone in {"general", "c2"}:
        return [origin, "AA11", destination]
    if from_zone == "prioria" and to_zone in {"general", "c2"}:
        return [origin, "N15", destination]
    if from_zone in {"general", "c2"} and to_zone == "triage":
        branch = route_defs["waiting_chair_to_exit_or_triage_or_box"]["branches"]
        triage_route = next(item for item in branch if item["id"] == "triage_call")
        return [origin, route_defs["waiting_chair_to_exit_or_triage_or_box"]["start"],
                *triage_route["waypoints"], triage_route["destination"]]
    if from_zone == "triage" and to_zone == "general":
        route = route_defs["triage_return_c3_c4_c5_to_general_waiting"]
        return [origin, route["start"], *route["waypoints"], destination]
    if from_zone == "triage" and to_zone == "c2":
        route = route_defs["triage_return_c2_to_c2_waiting"]
        return [origin, route["start"], *route["waypoints"], destination]
    if from_zone in {"general", "c2"} and to_zone == "prioria":
        return [origin, "AF15", destination]
    if from_zone == "general" and to_zone == "box":
        route = route_defs["waiting_chair_to_exit_or_triage_or_box"]
        box_number = int(str(end.get("home_box", "BOX-01")).split("-")[-1])
        branch_id = "box_call_1_to_4" if box_number <= 4 else "box_call_5_to_8"
        branch = next(item for item in route["branches"] if item["id"] == branch_id)
        detail = branch["destinations_by_box"][f"box_{box_number}"]
        return [origin, route["start"], *branch["waypoints"],
                *detail.get("waypoints", []), detail["door"], detail["bed"]]
    if from_zone == "c2" and to_zone == "box":
        route = route_defs["c2_waiting_chair_to_box"]
        box_number = int(str(end.get("home_box", "BOX-01")).split("-")[-1])
        branch = next(item for item in route["branches"]
                      if f"box_{box_number}" in item["destinations_by_box"])
        detail = branch["destinations_by_box"][f"box_{box_number}"]
        return [origin, *route["common_waypoints"], *branch["waypoints"],
                *detail.get("waypoints", []), detail["door"], detail["bed"]]
    if from_zone == "box" and to_zone == "c2":
        box_number = int(str(start.get("home_box", "BOX-01")).split("-")[-1])
        spec = route_defs["box_to_c2_waiting_after_attention"]["routes_by_box"][f"box_{box_number}"]
        return [origin, spec["start"], *spec["waypoints"], destination]
    if from_zone == "resuscitation" and to_zone == "hospitalized":
        return [origin, "BC26", "BA38"]
    if to_zone == "departed":
        return [origin, "AD01"]
    if from_zone == "ambulance" and to_zone == "resuscitation":
        route = route_defs["ambulance_parking_1_to_resuscitation"]
        return [origin, *route["waypoints"], destination]
    return [origin, destination]

def staff_states_at_minute(layout: dict[str, Any], trace: list[dict[str, Any]],
                           minute: int, config: dict[str, Any],
                           patients: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Place clinical and administrative staff at idle or active work locations."""
    positions = layout["staff_work_positions"]
    resources = layout["resource_locations"]
    specialties = config["resources"]["physicians_by_specialty"]
    active_doctors: dict[str, dict[str, Any]] = {}
    active_nurses: dict[str, dict[str, Any]] = {}
    active_triage: dict[str, dict[str, Any]] = {}
    active_admission: dict[str, dict[str, Any]] = {}
    for event in trace:
        if int(event.get("minute", 10**9)) > minute:
            continue
        patient_id = event.get("patient_id")
        kind = event.get("event")
        if kind == "care_stage_started" and event.get("physician_id"):
            active_doctors[event["physician_id"]] = event
            if event.get("resource") in {"box", "resuscitation"}:
                active_nurses[patient_id] = event
        elif kind == "treatment_nurse_procedure_started":
            active_nurses[patient_id] = event
        elif kind in {"physician_returned_to_box", "care_stage_completed"}:
            active_doctors = {key: value for key, value in active_doctors.items()
                              if value.get("patient_id") != patient_id}
            active_nurses.pop(patient_id, None)
        elif kind == "triage_started":
            if event.get("mode") == "human":
                active_triage[patient_id] = event
        elif kind == "triage_completed":
            active_triage.pop(patient_id, None)
        elif kind == "admission_started":
            active_admission[patient_id] = event
        elif kind == "admission_completed":
            active_admission.pop(patient_id, None)

    patient_map = {item["patient_id"]: item for item in patients}
    result: list[dict[str, Any]] = []
    doctor_colors = {"internal_medicine": "#168b61", "traumatology": "#16a6a0",
                     "surgery": "#d84a4a"}
    doctor_specs = [(specialty, color) for specialty, color in
                    (("internal_medicine", doctor_colors["internal_medicine"]),
                     ("traumatology", doctor_colors["traumatology"]),
                     ("surgery", doctor_colors["surgery"]))]
    box_layouts = positions["physicians_by_box"]
    active_by_box = {}
    for event in active_doctors.values():
        box_number = int(str(event.get("home_box", "BOX-01")).split("-")[-1])
        active_by_box[f"box_{box_number}"] = event
    doctor_index = 0
    for specialty, color in doctor_specs:
        for _ in range(int(specialties.get(specialty, 0))):
            doctor_index += 1
            box_key = f"box_{doctor_index}"
            work = box_layouts.get(box_key)
            if not work:
                continue
            active = active_by_box.get(box_key)
            if active and active.get("resource") == "resuscitation":
                coordinate = "BH26" if doctor_index % 2 else "BL26"
            else:
                coordinate = work["exam"] if active else work["idle"]
            result.append({"staff_id": f"physician_{doctor_index}", "coordinate": coordinate,
                           "label": f"Médico {specialty} · {box_key}",
                           "color": color, "symbol": "x" if active else "square"})

    attention_positions = [item["coordinate"] for item in resources["nurse_station_seats"]["positions"]]
    attention_count = int(config["resources"].get("box_nurses", 0))
    active_nurse_items = list(active_nurses.items())[:attention_count]
    for index in range(attention_count):
        if index < len(active_nurse_items):
            patient_id, event = active_nurse_items[index]
            patient = patient_map.get(patient_id)
            if patient and "x" in patient:
                x, y = patient["x"], min(int(layout["grid"]["rows"]) - 0.5, patient["y"] + 1)
                coordinate = None
            else:
                coordinate = "BA34"
                x = y = None
            result.append({"staff_id": f"attention_nurse_{index + 1}",
                           "coordinate": coordinate, "x": x, "y": y,
                           "label": f"Enfermera/o de atención {index + 1} · {patient_id}",
                           "color": "#14213d", "symbol": "triangle-up"})
        elif attention_positions:
            result.append({"staff_id": f"attention_nurse_{index + 1}",
                           "coordinate": attention_positions[index % len(attention_positions)],
                           "label": f"Enfermera/o de atención · estación {index + 1}",
                           "color": "#14213d", "symbol": "square"})

    triage_idle = [item["idle"] for item in positions["triage_nurses"]["positions"]]
    triage_coords = [item["coordinate"] for item in resources["triage_seats"]["positions"]]
    cabins = [item["coordinate"] for item in resources["prioria_cabins"]["positions"]]
    triage_count = int(config["resources"].get("triage_nurses", 0))
    active_triage_items = list(active_triage.items())[:triage_count]
    for index in range(triage_count):
        if index < len(active_triage_items):
            patient_id, event = active_triage_items[index]
            patient = patient_map.get(patient_id)
            coordinate = patient.get("coordinate") if patient else triage_coords[index % len(triage_coords)]
        else:
            coordinate = triage_idle[index % len(triage_idle)]
        result.append({"staff_id": f"triage_nurse_{index + 1}", "coordinate": coordinate,
                       "label": f"Enfermera/o de triage {index + 1}",
                       "color": "#3b5bdb", "symbol": "diamond"})

    admission_idle = [item["idle"] for item in positions["admissionists"]["positions"]]
    admission_coords = [item["coordinate"] for item in resources["admission_desks"]["positions"]]
    admission_count = int(config["resources"].get("admission_desks", 0))
    active_admission_items = list(active_admission.items())[:admission_count]
    for index in range(admission_count):
        if index < len(active_admission_items):
            coordinate = admission_coords[index % len(admission_coords)]
        else:
            coordinate = admission_idle[index % len(admission_idle)]
        result.append({"staff_id": f"admissionist_{index + 1}", "coordinate": coordinate,
                       "label": f"Admisión {index + 1}",
                       "color": "#e09918", "symbol": "star"})
    return result


def build_period_frames(layout: dict[str, Any], trace: list[dict[str, Any]],
                        config: dict[str, Any], start_minute: int,
                        end_minute: int) -> dict[str, Any]:
    """Create compact minute segments for a measured window after warm-up."""
    snapshots: list[list[dict[str, Any]]] = []
    staff_snapshots: list[list[dict[str, Any]]] = []
    for minute in range(start_minute, end_minute + 1):
        patients = patient_states_at_minute(layout, trace, minute, config)
        snapshots.append(patients)
        staff_snapshots.append(staff_states_at_minute(layout, trace, minute, config, patients))

    terminal_events = {
        (int(event["minute"]), str(event["patient_id"])): event
        for event in trace
        if event.get("event") in {"discharged", "hospitalized", "abandoned"}
    }
    arrivals: dict[int, int] = {}
    departures: dict[int, int] = {}
    for event in trace:
        event_minute = int(event.get("minute", -1))
        if event.get("event") == "arrived" and start_minute <= event_minute < end_minute:
            arrivals[event_minute] = arrivals.get(event_minute, 0) + 1
        if event.get("event") in {"discharged", "hospitalized", "abandoned"} \
                and start_minute <= event_minute < end_minute:
            departures[event_minute] = departures.get(event_minute, 0) + 1

    grid = layout["grid"]
    entrance_points = layout["resource_locations"]["own_means_access_and_exit"]["coordinates"]
    profile_by_id = {row["patient_id"]: row for row in trace
                     if row.get("event") == "patient_summary"}
    arrival_by_id = {row["patient_id"]: row for row in trace
                     if row.get("event") == "arrived"}
    patient_order = {patient_id: index for index, patient_id in enumerate(profile_by_id)}

    def points(coordinates: list[str]) -> list[list[float]]:
        return [[float(x), float(y)] for x, y in _orthogonal_path(coordinates)]

    def patient_track(start: dict[str, Any], end: dict[str, Any] | None,
                      absolute_minute: int) -> list[Any]:
        if end is not None:
            route = _transition_waypoints(layout, start, end)
        else:
            terminal = terminal_events.get((absolute_minute + 1, str(start["patient_id"])))
            if terminal:
                hospital = terminal["event"] == "hospitalized"
                endpoint = {**start, "zone": "hospitalized" if hospital else "departed",
                            "coordinate": "BA38" if hospital else "AD01"}
                route = _transition_waypoints(layout, start, endpoint)
            else:
                route = [start["coordinate"]]
        category = max(0, int(str(start.get("triage_category", "C3")).replace("C", "")) - 1)
        digest = hashlib.sha256(f"{start['patient_id']}:{absolute_minute}".encode()).digest()
        delay = (digest[0] % 25) / 60.0
        duration = min(0.58 + (digest[1] % 10) / 100.0, 0.98 - delay)
        return [str(start["patient_id"]), category, str(start.get("zone", "general")),
                points(route), delay, duration]

    segments: list[dict[str, Any]] = []
    arrival_total = departure_total = 0
    for offset in range(end_minute - start_minute):
        minute = start_minute + offset
        arrival_total += arrivals.get(minute, 0)
        departure_total += departures.get(minute, 0)
        starts = {item["patient_id"]: item for item in snapshots[offset]}
        ends = {item["patient_id"]: item for item in snapshots[offset + 1]}
        people = []
        for patient_id, start in starts.items():
            people.append(patient_track(start, ends.get(patient_id), minute))
        for patient_id, end in ends.items():
            if patient_id in starts:
                continue
            profile = profile_by_id.get(patient_id, {})
            arrival = arrival_by_id.get(patient_id, {})
            ambulance = arrival.get("arrival_mode") == "ambulance"
            origin = "BC03" if ambulance else entrance_points[patient_order.get(patient_id, 0) % len(entrance_points)]
            start = {**profile, "patient_id": patient_id,
                     "zone": "ambulance" if ambulance else "admission_queue",
                     "coordinate": origin}
            people.append(patient_track(start, end, minute))

        start_staff = {item["staff_id"]: item for item in staff_snapshots[offset]}
        end_staff = {item["staff_id"]: item for item in staff_snapshots[offset + 1]}
        team = []
        for staff_id, member in start_staff.items():
            following = end_staff.get(staff_id, member)
            begin = ((float(member["x"]), float(member["y"])) if member.get("x") is not None
                     else _grid_point(member["coordinate"]))
            finish = ((float(following["x"]), float(following["y"])) if following.get("x") is not None
                      else _grid_point(following["coordinate"]))
            path = [begin] if begin == finish else [begin, (finish[0], begin[1]), finish]
            digest = hashlib.sha256(f"{staff_id}:{minute}".encode()).digest()
            delay = (digest[0] % 25) / 60.0
            duration = min(0.58 + (digest[1] % 10) / 100.0, 0.98 - delay)
            team.append([staff_id, member["label"], member["color"], member["symbol"],
                         [[float(x), float(y)] for x, y in path], delay, duration])
        segments.append({"p": people, "s": team,
                         "a": arrival_total, "d": departure_total})
    return {"grid": [int(grid["columns"]), int(grid["rows"])],
            "start_minute": start_minute, "end_minute": end_minute,
            "segments": segments}
