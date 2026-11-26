#!/usr/bin/env python3
"""FIFO chair allocation for PriorIA V2's separate emergency waiting rooms.

C2 patients enter the C2 room; C3/C4/C5 patients enter the general room; C1
patients go to resuscitation. Each waiting room has its own chair capacity and
FIFO queue. PriorIA cabins, boxes, and resuscitation beds are modeled separately.
"""
from __future__ import annotations

from dataclasses import dataclass
import random
from typing import Any, Callable


@dataclass(frozen=True)
class WaitingPatient:
    patient_id: str
    queue_entered_minute: int
    triage_category: str
    age_group: str
    abandonment_threshold: float
    waiting_stage: str = "initial"
    prior_wait_minutes: int = 0
    is_waiting: bool = True


class GeneralWaitingRoom:
    """Manage fixed waiting-room chairs with strict FIFO allocation."""

    def __init__(self, chair_capacity: int = 36,
                 rng: random.Random | None = None) -> None:
        if chair_capacity < 0:
            raise ValueError("La capacidad de sillas no puede ser negativa.")
        self.chair_capacity = chair_capacity
        self._sequence = 0
        self._active: dict[str, tuple[int, WaitingPatient]] = {}
        self._seated: set[str] = set()
        self._chair_by_patient: dict[str, int] = {}
        self._free_chairs = list(range(chair_capacity))
        self._rng = rng or random.Random(0)

    def arrive(self, patient: WaitingPatient) -> bool:
        """Add a patient to the FIFO queue; return True if seated immediately."""
        if patient.patient_id in self._active:
            raise ValueError(f"Paciente duplicado en sala: {patient.patient_id}")
        self._sequence += 1
        self._active[patient.patient_id] = (self._sequence, patient)
        self._allocate_free_chairs()
        return patient.patient_id in self._seated

    def leave(self, patient_id: str) -> str | None:
        """Remove a patient on service start or abandonment; return promoted ID."""
        if patient_id not in self._active:
            raise KeyError(f"Paciente no activo en sala: {patient_id}")
        was_seated = patient_id in self._seated
        del self._active[patient_id]
        self._seated.discard(patient_id)
        chair_index = self._chair_by_patient.pop(patient_id, None)
        if chair_index is not None:
            self._free_chairs.append(chair_index)
        promoted = self._allocate_free_chairs() if was_seated else None
        return promoted

    def patient(self, patient_id: str) -> WaitingPatient:
        if patient_id not in self._active:
            raise KeyError(f"Paciente no activo en sala: {patient_id}")
        return self._active[patient_id][1]

    def replace_patient(self, patient_id: str, patient: WaitingPatient) -> None:
        if patient_id not in self._active or patient_id != patient.patient_id:
            raise KeyError(f"Paciente no activo en sala: {patient_id}")
        sequence, _ = self._active[patient_id]
        self._active[patient_id] = (sequence, patient)

    def _allocate_free_chairs(self) -> str | None:
        promoted: str | None = None
        while len(self._seated) < self.chair_capacity:
            standing = [
                (sequence, patient)
                for sequence, patient in self._active.values()
                if patient.patient_id not in self._seated
            ]
            if not standing:
                break
            _, next_patient = min(
                standing,
                key=lambda item: (item[1].queue_entered_minute, item[0]),
            )
            self._seated.add(next_patient.patient_id)
            chair_index = self._rng.choice(self._free_chairs)
            self._free_chairs.remove(chair_index)
            self._chair_by_patient[next_patient.patient_id] = chair_index
            if promoted is None:
                promoted = next_patient.patient_id
        return promoted

    def status(self, patient_id: str) -> str:
        if patient_id not in self._active:
            return "left"
        patient = self._active[patient_id][1]
        if not patient.is_waiting:
            return "treatment_seated" if patient_id in self._seated else "treatment_standing"
        return "seated" if patient_id in self._seated else "standing"

    def chair_index(self, patient_id: str) -> int | None:
        return self._chair_by_patient.get(patient_id)

    def waiting_minutes(self, patient_id: str, now_minute: int) -> int:
        if patient_id not in self._active:
            raise KeyError(f"Paciente no activo en sala: {patient_id}")
        entered = self._active[patient_id][1].queue_entered_minute
        return max(0, now_minute - entered)

    def cumulative_waiting_minutes(self, patient_id: str, now_minute: int) -> int:
        if patient_id not in self._active:
            raise KeyError(f"Paciente no activo en sala: {patient_id}")
        patient = self._active[patient_id][1]
        return patient.prior_wait_minutes + self.waiting_minutes(patient_id, now_minute)

    def abandonment_candidates(
        self,
        now_minute: int,
        risk_function: Callable[[dict[str, Any], float, bool, dict[str, Any]], float],
        risk_parameters: dict[str, Any],
    ) -> list[tuple[str, float]]:
        """Return patients whose modeled risk has crossed their personal threshold.

        Risk is recalculated from waiting duration and whether the patient stands.
        Candidates are sorted by FIFO sequence to keep event handling reproducible.
        """
        candidates: list[tuple[int, str, float]] = []
        for sequence, patient in self._active.values():
            if not patient.is_waiting:
                continue
            wait = self.cumulative_waiting_minutes(patient.patient_id, now_minute)
            standing = patient.patient_id not in self._seated
            patient_data = {
                "triage_category": patient.triage_category,
                "age_group": patient.age_group,
            }
            risk = risk_function(patient_data, wait, standing, risk_parameters)
            if risk >= patient.abandonment_threshold:
                candidates.append((sequence, patient.patient_id, risk))
        candidates.sort()
        return [(patient_id, risk) for _, patient_id, risk in candidates]

    @property
    def seated_patient_ids(self) -> list[str]:
        return sorted(
            self._seated,
            key=lambda patient_id: self._active[patient_id][0],
        )

    @property
    def standing_patient_ids(self) -> list[str]:
        return [
            patient.patient_id
            for _, patient in sorted(
                self._active.values(),
                key=lambda item: (item[1].queue_entered_minute, item[0]),
            )
            if patient.patient_id not in self._seated
        ]

    @property
    def occupied_chairs(self) -> int:
        return len(self._seated)

    @property
    def active_count(self) -> int:
        return len(self._active)



class WaitingAreas:
    """Route patients to independent waiting rooms and expose their chair states.

    Routing sends C1 to resuscitation, C2 to the dedicated C2 room, and C3–C5
    to the general waiting room.
    """

    def __init__(self, general_chairs: int = 72, c2_chairs: int = 30,
                 rng: random.Random | None = None) -> None:
        seat_rng = rng or random.Random(0)
        self.general = GeneralWaitingRoom(general_chairs, seat_rng)
        self.c2 = GeneralWaitingRoom(c2_chairs, seat_rng)
        self._patient_rooms: dict[str, str] = {}
        self._patient_records: dict[str, WaitingPatient] = {}

    @staticmethod
    def route(triage_category: str) -> str:
        routes = {
            "C1": "resuscitation",
            "C2": "c2",
            "C3": "general",
            "C4": "general",
            "C5": "general",
        }
        try:
            return routes[triage_category]
        except KeyError as error:
            raise ValueError(f"Categoría de triage inválida: {triage_category}") from error

    def arrive(self, patient: WaitingPatient,
               area_override: str | None = None) -> tuple[str, bool | None]:
        """Route a patient; an override supports the shared pre-triage waiting room."""
        area = area_override or self.route(patient.triage_category)
        if area not in {"general", "c2", "resuscitation"}:
            raise ValueError(f"Sala de espera inválida: {area}")
        self._patient_records[patient.patient_id] = patient
        if area == "resuscitation":
            return area, None
        room = self.c2 if area == "c2" else self.general
        seated = room.arrive(patient)
        self._patient_rooms[patient.patient_id] = area
        return area, seated

    def return_to_c2(
        self, patient: WaitingPatient, now_minute: int,
        waiting_stage: str = "treatment",
    ) -> tuple[str, bool]:
        """Re-queue a patient in C2 for treatment or another ordered stage.

        The patient joins the C2 FIFO at now_minute and competes for the
        configured chairs, standing in the hallway when all are occupied. The caller should
        invoke this after the patient leaves their prior waiting area for care.
        """
        if waiting_stage not in {"treatment", "reevaluation"}:
            raise ValueError("Etapa de espera C2 inválida.")
        if patient.patient_id in self._patient_rooms:
            raise ValueError(
                f"Paciente aún está en una sala de espera: {patient.patient_id}"
            )
        previous = self._patient_records.get(patient.patient_id, patient)
        requeued = WaitingPatient(
            patient_id=patient.patient_id,
            queue_entered_minute=now_minute,
            triage_category=previous.triage_category,
            age_group=previous.age_group,
            abandonment_threshold=previous.abandonment_threshold,
            waiting_stage=waiting_stage,
            prior_wait_minutes=max(previous.prior_wait_minutes,
                                   patient.prior_wait_minutes),
        )
        seated = self.c2.arrive(requeued)
        self._patient_rooms[patient.patient_id] = "c2"
        self._patient_records[patient.patient_id] = requeued
        return "c2", seated

    def pause_for_treatment(self, patient_id: str, now_minute: int) -> None:
        """Keep the chair occupied while treatment is delivered; pause waiting risk."""
        if self._patient_rooms.get(patient_id) != "c2":
            raise ValueError("El tratamiento en espera debe realizarse en sala C2.")
        patient = self._patient_records[patient_id]
        accumulated = self.c2.cumulative_waiting_minutes(patient_id, now_minute)
        self.c2.replace_patient(patient_id, WaitingPatient(
            patient_id=patient.patient_id,
            queue_entered_minute=now_minute,
            triage_category=patient.triage_category,
            age_group=patient.age_group,
            abandonment_threshold=patient.abandonment_threshold,
            waiting_stage="treatment_in_progress",
            prior_wait_minutes=accumulated,
            is_waiting=False,
        ))
        self._patient_records[patient_id] = self.c2.patient(patient_id)

    def resume_after_treatment(self, patient_id: str, now_minute: int) -> None:
        """Resume the wait for physician reassessment without releasing the chair."""
        if self._patient_rooms.get(patient_id) != "c2":
            raise ValueError("El paciente debe permanecer en la sala C2.")
        patient = self._patient_records[patient_id]
        self.c2.replace_patient(patient_id, WaitingPatient(
            patient_id=patient.patient_id,
            queue_entered_minute=now_minute,
            triage_category=patient.triage_category,
            age_group=patient.age_group,
            abandonment_threshold=patient.abandonment_threshold,
            waiting_stage="reevaluation",
            prior_wait_minutes=patient.prior_wait_minutes,
            is_waiting=True,
        ))
        self._patient_records[patient_id] = self.c2.patient(patient_id)

    def leave(self, patient_id: str, now_minute: int) -> tuple[str, str | None]:
        """Remove a patient and retain accumulated waiting time for later stages."""
        if patient_id not in self._patient_rooms:
            raise KeyError(f"Paciente no está en una sala de espera: {patient_id}")
        area = self._patient_rooms.pop(patient_id)
        room = self.c2 if area == "c2" else self.general
        current = self._patient_records[patient_id]
        accumulated_wait = room.cumulative_waiting_minutes(patient_id, now_minute)
        self._patient_records[patient_id] = WaitingPatient(
            patient_id=current.patient_id,
            queue_entered_minute=current.queue_entered_minute,
            triage_category=current.triage_category,
            age_group=current.age_group,
            abandonment_threshold=current.abandonment_threshold,
            waiting_stage=current.waiting_stage,
            prior_wait_minutes=accumulated_wait,
        )
        return area, room.leave(patient_id)

    def status(self, patient_id: str) -> tuple[str, str]:
        if patient_id not in self._patient_rooms:
            return "left", "left"
        area = self._patient_rooms[patient_id]
        room = self.c2 if area == "c2" else self.general
        return area, room.status(patient_id)

    def chair_index(self, patient_id: str) -> int | None:
        area = self._patient_rooms.get(patient_id)
        if area is None or area == "resuscitation":
            return None
        room = self.c2 if area == "c2" else self.general
        return room.chair_index(patient_id)


if __name__ == "__main__":
    print("PriorIA V2: sala general (72) y sala C2 (30), con asignación FIFO independiente.")
