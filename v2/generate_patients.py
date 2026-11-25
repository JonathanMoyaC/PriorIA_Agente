#!/usr/bin/env python3
"""Generate a synthetic 24-hour patient cohort for PriorIA simulator V2.

DEIS inputs are intentionally external: fill the category and cause distributions
from the selected official DEIS tables before generating a calibrated cohort.
The hourly arrival curve and age-specific mode preferences are scenario
assumptions, not DEIS observations.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from pathlib import Path
from typing import Any

AGE_RANGES = {
    "adult_young": (18, 39),
    "adult_middle": (40, 64),
    "adult_older": (65, 95),
}
CATEGORIES = ("C1", "C2", "C3", "C4", "C5")
ARRIVAL_MODES = ("own_means", "ambulance", "police")

COMPLAINTS_BY_CAUSE = {
    "thoracic_pain": ("dolor torácico", "opresión en el pecho", "dolor precordial"),
    "respiratory_difficulty": ("dificultad respiratoria", "tos y falta de aire", "sensación de ahogo"),
    "abdominal_pain": ("dolor abdominal", "dolor en fosa iliaca", "dolor abdominal agudo"),
    "trauma_fall": ("caída", "traumatismo", "dolor tras accidente"),
    "wound_bleeding": ("herida", "sangrado", "corte"),
    "neurological_dizziness_syncope": ("mareo", "desmayo", "debilidad o alteración neurológica"),
    "fever_infection": ("fiebre", "malestar general", "síntomas de infección"),
    "gastrointestinal_urinary": ("náuseas o vómitos", "dolor urinario", "síntomas gastrointestinales"),
}

DEFAULT_VITALS_BY_CATEGORY = {
    "C1": {"heart_rate": {"mean": 125, "sd": 22}, "respiratory_rate": {"mean": 30, "sd": 7}, "temperature_c": {"mean": 37.6, "sd": 1.5}, "spo2_pct": {"mean": 89, "sd": 7}, "systolic_bp": {"mean": 95, "sd": 22}, "diastolic_bp": {"mean": 60, "sd": 15}},
    "C2": {"heart_rate": {"mean": 110, "sd": 20}, "respiratory_rate": {"mean": 24, "sd": 6}, "temperature_c": {"mean": 37.3, "sd": 1.4}, "spo2_pct": {"mean": 93, "sd": 5}, "systolic_bp": {"mean": 115, "sd": 25}, "diastolic_bp": {"mean": 70, "sd": 15}},
    "C3": {"heart_rate": {"mean": 94, "sd": 18}, "respiratory_rate": {"mean": 20, "sd": 5}, "temperature_c": {"mean": 37.1, "sd": 1.2}, "spo2_pct": {"mean": 96, "sd": 3}, "systolic_bp": {"mean": 125, "sd": 22}, "diastolic_bp": {"mean": 78, "sd": 14}},
    "C4": {"heart_rate": {"mean": 84, "sd": 14}, "respiratory_rate": {"mean": 18, "sd": 4}, "temperature_c": {"mean": 37.0, "sd": 1.0}, "spo2_pct": {"mean": 97, "sd": 2}, "systolic_bp": {"mean": 128, "sd": 20}, "diastolic_bp": {"mean": 80, "sd": 12}},
    "C5": {"heart_rate": {"mean": 78, "sd": 12}, "respiratory_rate": {"mean": 16, "sd": 3}, "temperature_c": {"mean": 36.8, "sd": 0.7}, "spo2_pct": {"mean": 98, "sd": 1.5}, "systolic_bp": {"mean": 130, "sd": 18}, "diastolic_bp": {"mean": 82, "sd": 10}},
}

CAUSE_VITAL_ADJUSTMENTS = {
    "thoracic_pain": {"heart_rate": 5},
    "respiratory_difficulty": {"respiratory_rate": 4, "spo2_pct": -4},
    "abdominal_pain": {"heart_rate": 3},
    "trauma_fall": {"heart_rate": 4},
    "wound_bleeding": {"heart_rate": 8, "systolic_bp": -8},
    "neurological_dizziness_syncope": {"systolic_bp": -3},
    "fever_infection": {"temperature_c": 0.8, "heart_rate": 5},
    "gastrointestinal_urinary": {"temperature_c": 0.2},
}


def config_template() -> dict[str, Any]:
    """Return an editable template; DEIS-derived values must be filled in."""
    return {
        "scenario_id": "v2_base_100_daily",
        "seed": 20261124,
        "daily_patients": 100,
        "age_group_probabilities": None,
        "triage_category_probabilities": None,
        "deis_cause_probabilities_by_age": None,
        "arrival": {
            "profile": "gaussian_peak",
            "peak_hour": 16,
            "sigma_hours": 4.0,
            "baseline": 0.15,
            "arrival_mode_shares": {
                "own_means": 0.80,
                "ambulance": 0.05,
                "police": 0.15
            },
            "mode_preference_weights_by_age": {
                "adult_young": {"own_means": 8, "ambulance": 1, "police": 2},
                "adult_middle": {"own_means": 5, "ambulance": 1, "police": 2},
                "adult_older": {"own_means": 2, "ambulance": 5, "police": 2}
            },
            "prioria_adoption_rate": 0.60,
            "prioria_adoption_adjustment_by_age": {
                "adult_young": 0.20,
                "adult_middle": 0.0,
                "adult_older": -0.25
            }
        },
        "resources": {
            "physicians_by_specialty": {
                "internal_medicine": 3,
                "traumatology": 3,
                "surgery": 2
            },
            "box_nurses": 8,
            "consultation_boxes": 8,
            "prioria_cabins": 5,
            "general_waiting_chairs": 36,
            "c2_waiting_chairs": 24,
            "resuscitation_beds": 2,
            "admission_desks": 2,
            "triage_stations": 2,
            "triage_nurses": 3
        },
        "admission": {"duration_minutes": {"mean": 2, "sd": 0.5}},
        "triage": {
            "prioria_validation_minutes": {"minimum": 1, "maximum": 5},
            "human_categorization_minutes": {"minimum": 10, "maximum": 15}
        },
        "care": {
            "consultation_minutes_by_category": {
                "C1": {"mean": 60, "sd": 7.5},
                "C2": {"mean": 45, "sd": 7.5},
                "C3": {"mean": 30, "sd": 7.5},
                "C4": {"mean": 20, "sd": 5.0},
                "C5": {"mean": 15, "sd": 5.0}
            },
            "treatment_probability_by_category": {
                "C1": 0.90, "C2": 0.80, "C3": 0.55, "C4": 0.0, "C5": 0.0
            },
            "treatment_duration_minutes": {"minimum": 30, "maximum": 120},
            "treatment_nurse_minutes": {"minimum": 5, "maximum": 10},
            "reevaluation_discharge_probability": 0.70,
            "max_treatment_failures_before_hospitalization": 2,
            "hospitalization_probability_without_treatment_by_category": {
                "C1": 0.60, "C2": 0.20, "C3": 0.10, "C4": 0.0, "C5": 0.0
            }
        },
        "synthetic_profile": {
            "vitals_by_category": DEFAULT_VITALS_BY_CATEGORY,
            "cause_vital_adjustments": CAUSE_VITAL_ADJUSTMENTS,
            "note": "Distribuciones de prueba configurables; no son signos vitales reales ni calibración DEIS."
        },
        "specialty_by_cause": {
            "thoracic_pain": "internal_medicine",
            "respiratory_difficulty": "internal_medicine",
            "abdominal_pain": "surgery",
            "trauma_fall": "traumatology",
            "wound_bleeding": "surgery",
            "neurological_dizziness_syncope": "internal_medicine",
            "fever_infection": "internal_medicine",
            "gastrointestinal_urinary": "internal_medicine"
        },
        "abandonment": {
            "risk_intercept": -3.0,
            "beta_wait": 1.25,
            "wait_scale_minutes": 60.0,
            "beta_low_acuity": 1.0,
            "beta_standing": 0.8,
            "age_effect": {
                "adult_young": 0.35,
                "adult_middle": 0.0,
                "adult_older": -0.3
            },
            "threshold_range": [0.55, 0.75]
        },
        "metadata": {
            "deis_category_source": "PENDIENTE: completar fuente, año, territorio y denominador",
            "deis_cause_source": "PENDIENTE: completar fuente, año, territorio y denominador",
            "hourly_arrival_source": "Supuesto del escenario: perfil unimodal con máximo a las 16:00",
            "deis_values_verified": False,
            "test_fixture": False,
            "assumption_notes": [
                "La tasa de adopción y sus ajustes por edad, las rutas de tratamiento y las probabilidades de desenlace son supuestos configurables pendientes de calibración local."
            ]
        }
    }


def normalized(values: dict[str, float], label: str) -> dict[str, float]:
    if not values or any(v is None or not isinstance(v, (int, float)) or v < 0
                          for v in values.values()):
        raise ValueError(f"{label}: se requieren pesos numéricos no negativos.")
    total = sum(values.values())
    if total <= 0:
        raise ValueError(f"{label}: la suma debe ser positiva.")
    return {key: value / total for key, value in values.items()}


def exact_daily_counts(n: int, probabilities: dict[str, float]) -> dict[str, int]:
    """Largest-remainder apportionment; integer counts sum exactly to n."""
    probs = normalized(probabilities, "Distribución")
    quotas = {key: n * value for key, value in probs.items()}
    counts = {key: math.floor(value) for key, value in quotas.items()}
    remainder = n - sum(counts.values())
    order = sorted(quotas, key=lambda key: (quotas[key] - counts[key], key),
                   reverse=True)
    for key in order[:remainder]:
        counts[key] += 1
    return counts


def gaussian_hour_weights(peak_hour: int, sigma_hours: float,
                          baseline: float) -> list[float]:
    if not 0 <= peak_hour <= 23 or sigma_hours <= 0 or baseline < 0:
        raise ValueError("Perfil horario inválido.")
    raw = [baseline + math.exp(-0.5 * ((hour - peak_hour) / sigma_hours) ** 2)
           for hour in range(24)]
    total = sum(raw)
    return [value / total for value in raw]


def abandonment_risk(patient: dict[str, Any], wait_minutes: float,
                     standing: bool, params: dict[str, Any]) -> float:
    """Logistic abandonment-risk index in [0, 1], recalculated while waiting."""
    category_number = int(patient["triage_category"][1])
    low_acuity = (category_number - 1) / 4.0  # C5 is highest on this scale.
    scale = max(1.0, float(params["wait_scale_minutes"]))
    z = (float(params["risk_intercept"])
         + float(params["beta_wait"]) * math.log1p(max(0.0, wait_minutes) / scale)
         + float(params["beta_low_acuity"]) * low_acuity
         + float(params["beta_standing"]) * float(standing)
         + float(params["age_effect"][patient["age_group"]]))
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))


def constrained_arrival_modes(age_groups: list[str], shares: dict[str, float],
                              preference: dict[str, dict[str, float]],
                              rng: random.Random,
                              required_ambulance_indices: set[int] | None = None) -> list[str]:
    """Honor global mode totals and required ambulance arrivals."""
    quotas = exact_daily_counts(len(age_groups), shares)
    modes: list[str | None] = [None] * len(age_groups)
    remaining = dict(quotas)
    required = required_ambulance_indices or set()
    if required and "ambulance" not in remaining:
        raise ValueError("Hay pacientes que requieren ambulancia, pero la cuota de ambulancia es cero.")
    ambulance_quota = remaining.get("ambulance", 0)
    if len(required) > ambulance_quota:
        raise ValueError(
            f"Hay {len(required)} pacientes C1 pero solo {ambulance_quota} "
            "plazas de ambulancia según las cuotas de llegada. Ajusta las "
            "proporciones de C1 o de ambulancia."
        )
    for index in required:
        if index < 0 or index >= len(age_groups):
            raise ValueError("Índice de llegada obligatoria fuera del rango de pacientes.")
        modes[index] = "ambulance"
        remaining["ambulance"] -= 1
    order = [index for index in range(len(age_groups)) if index not in required]
    rng.shuffle(order)
    for index in order:
        age = age_groups[index]
        candidates = [mode for mode in ARRIVAL_MODES if remaining.get(mode, 0) > 0]
        weights = [max(0.0, float(preference[age][mode])) * remaining[mode]
                   for mode in candidates]
        if not candidates or sum(weights) <= 0:
            raise ValueError("No es posible asignar los medios de llegada configurados.")
        chosen = rng.choices(candidates, weights=weights, k=1)[0]
        modes[index] = chosen
        remaining[chosen] -= 1
    return [str(mode) for mode in modes]


def sample_vitals(category: str, cause_group: str, rng: random.Random,
                  profile_config: dict[str, Any]) -> dict[str, float | int]:
    """Sample illustrative vital signs; parameters must be locally calibrated."""
    distributions = profile_config.get("vitals_by_category", DEFAULT_VITALS_BY_CATEGORY)
    adjustments = profile_config.get("cause_vital_adjustments", CAUSE_VITAL_ADJUSTMENTS)
    limits = {
        "heart_rate": (35, 220), "respiratory_rate": (6, 60),
        "temperature_c": (32, 43), "spo2_pct": (50, 100),
        "systolic_bp": (50, 250), "diastolic_bp": (20, 150),
    }
    result: dict[str, float | int] = {}
    for name, spec in distributions[category].items():
        shift = float(adjustments.get(cause_group, {}).get(name, 0.0))
        value = rng.gauss(float(spec["mean"]) + shift, float(spec["sd"]))
        low, high = limits[name]
        value = max(low, min(high, value))
        result[name] = round(value, 1) if name == "temperature_c" else round(value)
    return result


def generate_patients(config: dict[str, Any]) -> list[dict[str, Any]]:
    n = int(config["daily_patients"])
    rng = random.Random(int(config["seed"]))
    age_probs = normalized(config["age_group_probabilities"], "Edad")
    category_probs = normalized(config["triage_category_probabilities"], "C1-C5")
    if set(age_probs) != set(AGE_RANGES):
        raise ValueError(f"Los grupos de edad deben ser: {', '.join(AGE_RANGES)}")
    if set(category_probs) != set(CATEGORIES):
        raise ValueError(f"Las categorías deben ser: {', '.join(CATEGORIES)}")

    cause_by_age = config["deis_cause_probabilities_by_age"]
    if not isinstance(cause_by_age, dict):
        raise ValueError("Falta deis_cause_probabilities_by_age.")
    for age in AGE_RANGES:
        if age not in cause_by_age:
            raise ValueError(f"Falta distribución DEIS de causas para {age}.")
        normalized(cause_by_age[age], f"Causas DEIS ({age})")
    specialty_by_cause = config["specialty_by_cause"]

    age_counts = exact_daily_counts(n, age_probs)
    age_groups = [age for age, count in age_counts.items() for _ in range(count)]
    rng.shuffle(age_groups)
    category_counts = exact_daily_counts(n, category_probs)
    categories = [category for category, count in category_counts.items()
                  for _ in range(count)]
    rng.shuffle(categories)

    arrival_cfg = config["arrival"]
    modes = constrained_arrival_modes(
        age_groups, arrival_cfg["arrival_mode_shares"],
        arrival_cfg["mode_preference_weights_by_age"], rng,
        required_ambulance_indices={i for i, category in enumerate(categories)
                                    if category == "C1"})
    hour_weights = gaussian_hour_weights(
        int(arrival_cfg.get("peak_hour", 16)),
        float(arrival_cfg.get("sigma_hours", 4.0)),
        float(arrival_cfg.get("baseline", 0.15)))
    abandon_cfg = config["abandonment"]
    threshold_low, threshold_high = map(float, abandon_cfg["threshold_range"])
    if not 0 <= threshold_low <= threshold_high <= 1:
        raise ValueError("threshold_range debe estar entre 0 y 1.")

    patients = []
    for i, (age_group, category, mode) in enumerate(
            zip(age_groups, categories, modes), start=1):
        low_age, high_age = AGE_RANGES[age_group]
        age = rng.randint(low_age, high_age)
        cause_probs = normalized(cause_by_age[age_group], f"Causas ({age_group})")
        cause_group = rng.choices(list(cause_probs), weights=list(cause_probs.values()), k=1)[0]
        required_specialty = specialty_by_cause.get(cause_group)
        if required_specialty is None:
            raise ValueError(
                f"No se definió especialidad médica para el grupo de diagnóstico "
                f"sintético '{cause_group}'."
            )
        complaints = COMPLAINTS_BY_CAUSE.get(cause_group, (cause_group.replace("_", " "),))
        complaint_text = rng.choice(complaints)
        vitals = sample_vitals(category, cause_group, rng,
                               config.get("synthetic_profile", {}))
        hour = rng.choices(range(24), weights=hour_weights, k=1)[0]
        arrival_minute = hour * 60 + rng.randrange(60)
        patient_threshold = rng.uniform(threshold_low, threshold_high)
        adoption_rate = float(arrival_cfg.get("prioria_adoption_rate", 0.60))
        age_adjustment = float(arrival_cfg.get(
            "prioria_adoption_adjustment_by_age", {}).get(age_group, 0.0))
        if not 0.0 <= adoption_rate <= 1.0:
            raise ValueError("prioria_adoption_rate debe estar entre 0 y 1.")
        prioria_prob = max(0.0, min(1.0, adoption_rate + age_adjustment))
        uses_prioria = (
            mode == "own_means" and category != "C1"
            and rng.random() < prioria_prob
        )
        patients.append({
            "patient_id": f"V2-{i:03d}",
            "arrival_minute": arrival_minute,
            "arrival_hour": hour,
            "age": age,
            "age_group": age_group,
            "arrival_mode": mode,
            "triage_category": category,
            "deis_cause_group": cause_group,
            "probable_diagnosis_group": cause_group,
            "required_specialty": required_specialty,
            "chief_complaint": complaint_text,
            **vitals,
            "prioria_adoption_probability": round(prioria_prob, 4),
            "uses_prioria": uses_prioria,
            "abandonment_threshold": round(patient_threshold, 4)
        })
    return sorted(patients, key=lambda patient: (patient["arrival_minute"],
                                                  patient["patient_id"]))


def write_template(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config_template(), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path, default=Path("outputs/v2_patient_profiles.csv"))
    parser.add_argument("--write-template", type=Path,
                        help="Escribe una plantilla con distribuciones DEIS pendientes.")
    args = parser.parse_args()

    if args.write_template:
        write_template(args.write_template)
        print(f"Plantilla escrita en {args.write_template}")
        return
    if not args.config:
        parser.error("Indica --config o usa --write-template.")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    metadata = config.get("metadata", {})
    if metadata.get("deis_values_verified") is not True and metadata.get("test_fixture") is not True:
        raise SystemExit(
            "Completa las distribuciones DEIS verificadas o marca explícitamente una configuración de prueba sintética."
        )
    patients = generate_patients(config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=patients[0].keys())
        writer.writeheader()
        writer.writerows(patients)
    print(f"Generados {len(patients)} perfiles en {args.output}")


if __name__ == "__main__":
    main()
