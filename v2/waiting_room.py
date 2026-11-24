#!/usr/bin/env python3
"""FIFO seating and standing status for the PriorIA V2 general waiting room.

The room is modeled separately from the triage waiting area (C2), PriorIA cabins,
consultation boxes, and resuscitation beds. Seats are allocated in order of entry
to the general waiting queue. A released seat is assigned immediately to the
oldest active standing patient.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class WaitingPatient:
    patient_id: str
    queue_entered_minute: int
    triage_category: str
    age_group: str
    abandonment_threshold: float


class GeneralWaitingRoom:
    """Manage a fixed number of general-room chairs with strict FIFO allocation."""

    def __init__(self, chair_capacity: int = 36) -> None:
        if chair_capacity < 0:
            raise ValueError("La capacidad de sillas no puede ser negativa.")
        self.chair_capacity = chair_capacity
        self._sequence = 0
        self._active: dict[str, tuple[int, WaitingPatient]] = {}
        self._seated: set[str] = set()

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
        promoted = self._allocate_free_chairs() if was_seated else None
        return promoted

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
            if promoted is None:
                promoted = next_patient.patient_id
        return promoted

    def status(self, patient_id: str) -> str:
        if patient_id not in self._active:
            return "left"
        return "seated" if patient_id in self._seated else "standing"

    def waiting_minutes(self, patient_id: str, now_minute: int) -> int:
        if patient_id not in self._active:
            raise KeyError(f"Paciente no activo en sala: {patient_id}")
        entered = self._active[patient_id][1].queue_entered_minute
        return max(0, now_minute - entered)

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
            wait = max(0, now_minute - patient.queue_entered_minute)
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


if __name__ == "__main__":
    print("Sala general PriorIA V2: capacidad configurable (por defecto 36 sillas), FIFO.")
