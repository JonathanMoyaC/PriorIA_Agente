from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from v2.generate_patients import abandonment_risk, gaussian_hour_weights, generate_patients
from v2.simulate_day import simulate_day
from v2.waiting_room import WaitingAreas, WaitingPatient


ROOT = Path(__file__).resolve().parents[1]


def load_fixture() -> dict:
    return json.loads((ROOT / "configs" / "smoke_test.json").read_text(encoding="utf-8"))


class PatientGenerationTests(unittest.TestCase):
    def test_seeded_profile_has_exact_daily_and_mode_totals(self) -> None:
        config = load_fixture()
        patients = generate_patients(config)
        self.assertEqual(len(patients), 100)
        self.assertEqual({mode: sum(p["arrival_mode"] == mode for p in patients)
                          for mode in ("own_means", "ambulance", "police")},
                         {"own_means": 80, "ambulance": 5, "police": 15})
        self.assertEqual({f"C{i}": sum(p["triage_category"] == f"C{i}" for p in patients)
                          for i in range(1, 6)},
                         {"C1": 2, "C2": 12, "C3": 36, "C4": 33, "C5": 17})
        self.assertTrue(all(not p["uses_prioria"] or
                            (p["arrival_mode"] == "own_means" and p["triage_category"] != "C1")
                            for p in patients))
        self.assertTrue(all(p["chief_complaint"] for p in patients))
        self.assertTrue(all(35 <= p["heart_rate"] <= 220 and
                            6 <= p["respiratory_rate"] <= 60 and
                            50 <= p["spo2_pct"] <= 100 and
                            50 <= p["systolic_bp"] <= 250 and
                            20 <= p["diastolic_bp"] <= 150
                            for p in patients))

    def test_arrivals_cover_full_day_and_have_late_afternoon_peak(self) -> None:
        patients = generate_patients(load_fixture())
        counts = [sum(p["arrival_hour"] == hour for p in patients) for hour in range(24)]
        self.assertEqual(sum(counts), 100)
        weights = gaussian_hour_weights(peak_hour=16, sigma_hours=4.0, baseline=0.15)
        self.assertEqual(max(range(24), key=weights.__getitem__), 16)
        self.assertGreater(sum(counts[13:20]), sum(counts[0:7]))


class WaitingAreaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.areas = WaitingAreas(general_chairs=1, c2_chairs=1)

    @staticmethod
    def patient(pid: str, category: str, minute: int = 0) -> WaitingPatient:
        return WaitingPatient(pid, minute, category, "adult_middle", 0.99)

    def test_separate_rooms_and_fifo_seat_promotion_on_return(self) -> None:
        c2_first = self.patient("c2-first", "C2", 0)
        general = self.patient("general", "C4", 1)
        c2_second = self.patient("c2-second", "C3", 2)
        self.assertEqual(self.areas.arrive(c2_first), ("c2", True))
        self.assertEqual(self.areas.arrive(general), ("general", True))
        self.assertEqual(self.areas.arrive(c2_second), ("c2", False))
        self.assertEqual(self.areas.status("c2-second"), ("c2", "standing"))

        self.areas.leave("c2-first", now_minute=8)
        self.assertEqual(self.areas.status("c2-second"), ("c2", "seated"))
        area, seated = self.areas.return_for_procedure_or_observation(
            c2_first, now_minute=9, waiting_stage="procedure")
        self.assertEqual((area, seated), ("c2", False))
        self.assertEqual(self.areas.status("c2-first"), ("c2", "standing"))
        self.assertEqual(self.areas.c2.cumulative_waiting_minutes("c2-first", 10), 9)

    def test_abandonment_risk_rises_with_wait_low_acuity_and_standing(self) -> None:
        params = load_fixture()["abandonment"]
        patient = {"triage_category": "C5", "age_group": "adult_young"}
        immediate_seated = abandonment_risk(patient, 0, False, params)
        long_seated = abandonment_risk(patient, 180, False, params)
        long_standing = abandonment_risk(patient, 180, True, params)
        c2 = abandonment_risk({"triage_category": "C2", "age_group": "adult_young"},
                              180, False, params)
        self.assertLess(immediate_seated, long_seated)
        self.assertLess(long_seated, long_standing)
        self.assertLess(c2, long_seated)


class EndToEndSimulationTests(unittest.TestCase):
    def test_one_hundred_patient_day_completes_and_respects_room_capacities(self) -> None:
        summary, trace = simulate_day(load_fixture())
        self.assertEqual(summary["generated"], 100)
        self.assertEqual(summary["unresolved"], 0)
        self.assertEqual(summary["discharged"] + summary["hospitalized"] +
                         summary["abandoned"], 100)
        self.assertLessEqual(summary["max_general_waiting_occupancy"], 36)
        self.assertLessEqual(summary["max_c2_waiting_occupancy"], 24)
        self.assertTrue(any(row["event"] == "returned_to_c2" for row in trace))
        self.assertEqual(summary["metadata"]["deis_values_verified"], False)

    def test_congestion_scenario_exercises_standing_and_abandonment(self) -> None:
        config = copy.deepcopy(load_fixture())
        config["scenario_id"] = "v2_congestion_mechanics"
        config["resources"].update({
            "physicians_by_specialty": {
                "internal_medicine": 1,
                "traumatology": 1,
                "surgery": 1
            },
            "consultation_boxes": 1,
            "general_waiting_chairs": 1,
            "c2_waiting_chairs": 1,
            "procedure_slots": 1,
            "observation_beds": 1
        })
        config["abandonment"].update({
            "risk_intercept": -2.5,
            "beta_wait": 2.5,
            "wait_scale_minutes": 30.0,
            "beta_low_acuity": 1.5,
            "beta_standing": 1.5,
            "threshold_range": [0.90, 0.99]
        })
        summary, trace = simulate_day(config)
        self.assertGreater(summary["abandoned"], 0)
        self.assertGreater(summary["standing_patient_minutes"], 0)
        self.assertTrue(any(row.get("event") == "abandoned" and row.get("standing")
                            for row in trace))
        self.assertLessEqual(summary["max_general_waiting_occupancy"], 1)
        self.assertLessEqual(summary["max_c2_waiting_occupancy"], 1)


if __name__ == "__main__":
    unittest.main()
