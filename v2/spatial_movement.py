"""Estimate travel distances and times from PriorIA floorplan grid routes.

The grid is a visual coordinate system, not a measured floor plan. Distances use
orthogonal movement between cell centers; no diagonal shortcut is allowed.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Iterable

COORDINATE = re.compile(r"^([A-Z]+)(\d{2})$")


def _column_index(label: str) -> int:
    value = 0
    for char in label:
        value = value * 26 + ord(char) - ord("A") + 1
    return value


class SpatialMovementModel:
    """Convert coordinate sequences into provisional pixel travel times."""

    def __init__(self, layout: dict[str, Any]) -> None:
        self.layout = layout
        self.grid = layout["grid"]
        movement = layout["movement_model"]
        self.speed_px_per_second = float(movement["initial_speed_px_per_second"])
        self.cell_width_px = float(self.grid["image_width_px"]) / int(self.grid["columns"])
        self.cell_height_px = float(self.grid["image_height_px"]) / int(self.grid["rows"])
        if self.speed_px_per_second <= 0:
            raise ValueError("La velocidad de desplazamiento debe ser positiva.")

    @classmethod
    def from_file(cls, path: str | Path) -> "SpatialMovementModel":
        layout = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(layout)

    def point_px(self, coordinate: str) -> tuple[float, float]:
        match = COORDINATE.fullmatch(coordinate)
        if not match:
            raise ValueError(f"Coordenada de grilla inválida: {coordinate}")
        column = _column_index(match.group(1))
        row = int(match.group(2))
        if not (1 <= column <= int(self.grid["columns"])):
            raise ValueError(f"Columna fuera de la grilla: {coordinate}")
        if not (1 <= row <= int(self.grid["rows"])):
            raise ValueError(f"Fila fuera de la grilla: {coordinate}")
        x = (column - 0.5) * self.cell_width_px
        y = (row - 0.5) * self.cell_height_px
        return x, y

    def estimate(self, coordinates: Iterable[str]) -> dict[str, Any]:
        points = list(coordinates)
        if len(points) < 2:
            raise ValueError("Una ruta requiere al menos dos coordenadas.")
        distance = 0.0
        segments = []
        for start, end in zip(points, points[1:]):
            x1, y1 = self.point_px(start)
            x2, y2 = self.point_px(end)
            segment = abs(x2 - x1) + abs(y2 - y1)
            distance += segment
            segments.append({
                "from": start,
                "to": end,
                "distance_px": round(segment, 2),
            })
        seconds = distance / self.speed_px_per_second
        return {
            "coordinates": points,
            "segments": segments,
            "distance_px": round(distance, 2),
            "speed_px_per_second": self.speed_px_per_second,
            "travel_seconds": round(seconds, 2),
            "travel_minutes": round(seconds / 60, 2),
            # Avoid rounding an exact minute up because of binary float noise.
            "ceil_minutes_for_minute_step_simulator": math.ceil(seconds / 60 - 1e-12),
        }

    def calibration_summary(self) -> dict[str, Any] | None:
        """Return the configured route and target used to calibrate speed."""
        calibration = self.layout["movement_model"].get("calibration")
        if not calibration:
            return None
        estimated = self.estimate(calibration["route_coordinates"])
        return {
            **calibration,
            "recalculated_distance_px": estimated["distance_px"],
            "resulting_travel_seconds": estimated["travel_seconds"],
        }


def route_trial_cases() -> list[dict[str, Any]]:
    """Entrance-to-desk combinations plus representative downstream routes."""
    entrances = ["AB01", "AC01", "AD01", "AE01", "AF01"]
    desks = {"admission_1": "AM12", "admission_2": "AM14", "admission_3": "AM10"}
    entry_routes = [
        {"route": f"{entrance}_to_{desk}",
         "coordinates": [entrance, "AF08", destination]}
        for entrance in entrances
        for desk, destination in desks.items()
    ]
    return entry_routes + [
        {"route": "admission_1_to_general_chair", "coordinates": ["AM12", "AA11", "C02"]},
        {"route": "prioria_cabin_1_to_c2_chair", "coordinates": ["D16", "N15", "BD30"]},
        {"route": "triage_c3_c4_c5_to_general_chair", "coordinates": ["BC16", "AW18", "AL18", "AA11", "C02"]},
        {"route": "triage_c2_to_c2_chair", "coordinates": ["BC16", "BA21", "B33", "BD30"]},
        {"route": "general_wait_to_box_1_bed", "coordinates": ["AA14", "AF15", "AG20", "AI37", "AO37"]},
        {"route": "c2_wait_to_box_8_bed", "coordinates": ["BD30", "AY29", "H29", "R29", "B29", "B28", "G26"]},
        {"route": "box_4_to_c2_chair", "coordinates": ["AE26", "AG29", "AY29", "BA33", "BD30"]},
        {"route": "ambulance_to_resuscitation_bed_1", "coordinates": ["BC03", "AZ11", "BC16", "BI19", "BG27"]},
        {"route": "resuscitation_to_hospitalization", "coordinates": ["BG27", "BC26", "BA38"]},
        {"route": "attention_nurse_to_procedure", "coordinates": ["AU35", "AU30", "BA33", "BA34"]},
    ]
