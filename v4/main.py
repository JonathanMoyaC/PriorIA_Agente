"""PriorIA V4 API and standalone browser animation."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field, model_validator

ROOT = Path(__file__).resolve().parents[1]
WEB = Path(__file__).resolve().parent / "web"
ASSETS = Path(__file__).resolve().parent / "assets"
sys.path.insert(0, str(ROOT))

from v4.simulation import BASE_PATIENTS_PER_DAY, dashboard_data, simulate_period  # noqa: E402
from v4.visualization import build_period_frames, build_route_test_cases  # noqa: E402

app = FastAPI(title="PriorIA V4 Simulator", version="4.1.0")
app.add_middleware(GZipMiddleware, minimum_size=1000)
BASE_CONFIG: dict[str, Any] = json.loads(
    (ROOT / "v2" / "configs" / "smoke_test.json").read_text(encoding="utf-8"))
LAYOUT: dict[str, Any] = json.loads(
    (ROOT / "v2" / "configs" / "layout_coordinates.json").read_text(encoding="utf-8"))


class RunRequest(BaseModel):
    seed: int = Field(default=20261124, ge=0, le=2_000_000_000)
    simulation_days: int = Field(default=1, ge=1, le=7)
    warmup_days: int = Field(default=1, ge=0, le=3)
    demand_multiplier: float = Field(default=1.0, ge=0.5, le=2.0)
    prioria_adoption_rate: float = Field(default=0.6, ge=0.0, le=1.0)
    admission_desks: int = Field(default=2, ge=1, le=3)
    prioria_cabins: int = Field(default=5, ge=1, le=5)
    triage_stations: int = Field(default=2, ge=1, le=2)
    triage_nurses: int = Field(default=3, ge=1, le=3)
    attention_nurses: int = Field(default=3, ge=1, le=3)
    internists: int = Field(default=3, ge=0, le=8)
    traumatologists: int = Field(default=3, ge=0, le=8)
    surgeons: int = Field(default=2, ge=0, le=8)
    general_chairs: int = Field(default=72, ge=0, le=72)
    c2_chairs: int = Field(default=30, ge=0, le=30)
    resuscitation_beds: int = Field(default=2, ge=1, le=2)

    @model_validator(mode="after")
    def validate_staffing(self) -> "RunRequest":
        physicians = self.internists + self.traumatologists + self.surgeons
        if not 1 <= physicians <= 8:
            raise ValueError("La suma de médicos debe estar entre 1 y 8.")
        return self


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/assets/floorplan.jpg")
def floorplan() -> FileResponse:
    return FileResponse(ASSETS / "Plano_Unidad_Urgencias.jpg", media_type="image/jpeg")


@app.get("/api/route-test")
def route_test() -> dict[str, Any]:
    """Return the canonical route suite for the synthetic P-TEST marker."""
    return {"grid": LAYOUT["grid"], "routes": build_route_test_cases(LAYOUT)}


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": "4.1.0"}


@app.post("/api/simulate")
def run_simulation(request: RunRequest) -> dict[str, Any]:
    config = json.loads(json.dumps(BASE_CONFIG))
    config["seed"] = request.seed
    config["daily_patients"] = BASE_PATIENTS_PER_DAY
    config["arrival"]["demand_multiplier"] = request.demand_multiplier
    config["arrival"]["prioria_adoption_rate"] = request.prioria_adoption_rate
    config["resources"].update({
        "admission_desks": request.admission_desks,
        "prioria_cabins": request.prioria_cabins,
        "triage_stations": request.triage_stations,
        "triage_nurses": request.triage_nurses,
        "box_nurses": request.attention_nurses,
        "consultation_boxes": request.internists + request.traumatologists + request.surgeons,
        "physicians_by_specialty": {
            "internal_medicine": request.internists,
            "traumatology": request.traumatologists,
            "surgery": request.surgeons,
        },
        "general_waiting_chairs": request.general_chairs,
        "c2_waiting_chairs": request.c2_chairs,
        "resuscitation_beds": request.resuscitation_beds,
    })
    try:
        summary, trace = simulate_period(config, request.simulation_days, request.warmup_days)
        frames = build_period_frames(
            LAYOUT, trace, config,
            int(summary["observation_start_minute"]),
            int(summary["observation_end_minute"]),
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"summary": summary, "charts": dashboard_data(trace, summary, config),
            "trace": trace, "frames": frames,
            "layout": LAYOUT, "patients_per_day": BASE_PATIENTS_PER_DAY}
