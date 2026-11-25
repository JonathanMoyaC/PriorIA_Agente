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
    def test_cohort_counts_complaints_vitals_and_adoption_rule(self) -> None:
        config = load_fixture()
        patients = generate_patients(config)
        self.assertEqual(len(patients), 100)
        self.assertEqual({mode: sum(p["arrival_mode"] == mode for p in patients)
                          for mode in ("own_means", "ambulance", "police")},
                         {"own_means": 80, "ambulance": 5, "police": 15})
        self.assertEqual({f"C{i}": sum(p["triage_category"] == f"C{i}" for p in patients)
                          for i in range(1, 6)},
                         {"C1": 2, "C2": 12, "C3": 36, "C4": 33, "C5": 17})
        self.assertTrue(all(p["arrival_mode"] == "ambulance" for p in patients
                            if p["triage_category"] == "C1"))
        expected_adoption = {"adult_young": 0.8, "adult_middle": 0.6, "adult_older": 0.35}
        for patient in patients:
            self.assertEqual(patient["probable_diagnosis_group"],
                             patient["deis_cause_group"])
            self.assertEqual(patient["required_specialty"],
                             config["specialty_by_cause"][patient["deis_cause_group"]])
            self.assertAlmostEqual(patient["prioria_adoption_probability"],
                                   expected_adoption[patient["age_group"]])
            if patient["uses_prioria"]:
                self.assertEqual(patient["arrival_mode"], "own_means")
                self.assertNotEqual(patient["triage_category"], "C1")
        self.assertTrue(all(p["chief_complaint"] for p in patients))
        self.assertTrue(all(35 <= p["heart_rate"] <= 220 and
                            6 <= p["respiratory_rate"] <= 60 and
                            50 <= p["spo2_pct"] <= 100 and
                            50 <= p["systolic_bp"] <= 250 and
                            20 <= p["diastolic_bp"] <= 150
                            for p in patients))

    def test_c1_ambulance_requirement_rejects_infeasible_quotas(self) -> None:
        config = load_fixture()
        config["triage_category_probabilities"] = {
            "C1": 0.12, "C2": 0.02, "C3": 0.36, "C4": 0.33, "C5": 0.17}
        with self.assertRaisesRegex(ValueError, "pacientes C1"):
            generate_patients(config)

    def test_arrival_curve_expected_peak_is_sixteen(self) -> None:
        patients = generate_patients(load_fixture())
        self.assertEqual(len(patients), 100)
        weights = gaussian_hour_weights(peak_hour=16, sigma_hours=4.0, baseline=0.15)
        self.assertEqual(max(range(24), key=weights.__getitem__), 16)

    def test_demand_multiplier_scales_daily_cohort_and_mode_quotas(self) -> None:
        for multiplier, expected_n in ((0.8, 80), (1.2, 120)):
            config = load_fixture()
            config["arrival"]["demand_multiplier"] = multiplier
            patients = generate_patients(config)
            self.assertEqual(len(patients), expected_n)
            self.assertEqual(sum(p["triage_category"] == "C1" for p in patients),
                             round(expected_n * config["triage_category_probabilities"]["C1"]))
            self.assertEqual({mode: sum(p["arrival_mode"] == mode for p in patients)
                              for mode in ("own_means", "ambulance", "police")},
                             {"own_means": round(expected_n * 0.8),
                              "ambulance": round(expected_n * 0.05),
                              "police": expected_n - round(expected_n * 0.8)
                              - round(expected_n * 0.05)})

    def test_demand_multiplier_must_be_positive(self) -> None:
        config = load_fixture()
        config["arrival"]["demand_multiplier"] = 0
        with self.assertRaisesRegex(ValueError, "mayor que cero"):
            generate_patients(config)


class WaitingAreaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.areas = WaitingAreas(general_chairs=1, c2_chairs=1)

    @staticmethod
    def patient(pid: str, category: str, minute: int = 0) -> WaitingPatient:
        return WaitingPatient(pid, minute, category, "adult_middle", 0.99)

    def test_c2_alone_routes_to_c2_and_c3_plus_to_general(self) -> None:
        self.assertEqual(self.areas.route("C2"), "c2")
        for category in ("C3", "C4", "C5"):
            self.assertEqual(self.areas.route(category), "general")
        self.assertEqual(self.areas.route("C1"), "resuscitation")

    def test_pretriage_wait_can_use_general_chairs_before_category_known(self) -> None:
        c2_patient = self.patient("pretriage", "C2")
        self.assertEqual(self.areas.arrive(c2_patient, area_override="general"),
                         ("general", True))
        area, seat = self.areas.status("pretriage")
        self.assertEqual((area, seat), ("general", "seated"))
        self.areas.leave("pretriage", now_minute=4)
        self.assertEqual(self.areas.status("pretriage"), ("left", "left"))

    def test_fifo_return_to_c2_and_seat_promotion(self) -> None:
        first = self.patient("first", "C2", 0)
        general = self.patient("general", "C4", 1)
        second = self.patient("second", "C2", 2)
        self.assertEqual(self.areas.arrive(first), ("c2", True))
        self.assertEqual(self.areas.arrive(general), ("general", True))
        self.assertEqual(self.areas.arrive(second), ("c2", False))
        self.areas.leave("first", now_minute=8)
        self.assertEqual(self.areas.status("second"), ("c2", "seated"))
        area, seated = self.areas.return_to_c2(first, now_minute=9, waiting_stage="treatment")
        self.assertEqual((area, seated), ("c2", False))
        self.assertEqual(self.areas.status("first"), ("c2", "standing"))
        self.assertEqual(self.areas.c2.cumulative_waiting_minutes("first", 10), 9)

    def test_treatment_keeps_chair_and_pauses_abandonment_clock(self) -> None:
        first = self.patient("treating", "C2")
        self.areas.arrive(first)
        self.areas.pause_for_treatment("treating", now_minute=10)
        self.assertEqual(self.areas.status("treating"), ("c2", "treatment_seated"))
        self.assertEqual(self.areas.c2.occupied_chairs, 1)
        risk_params = load_fixture()["abandonment"]
        candidates = self.areas.c2.abandonment_candidates(
            500, abandonment_risk, risk_params)
        self.assertEqual(candidates, [])
        self.areas.resume_after_treatment("treating", now_minute=70)
        self.assertEqual(self.areas.status("treating"), ("c2", "seated"))
        self.assertEqual(self.areas.c2.cumulative_waiting_minutes("treating", 75), 15)

    def test_abandonment_risk_increases_with_wait_low_acuity_and_standing(self) -> None:
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
    def test_simulation_applies_demand_multiplier_to_daily_volume(self) -> None:
        config = load_fixture()
        config["arrival"]["demand_multiplier"] = 0.8
        summary, trace = simulate_day(config)
        self.assertEqual(summary["base_daily_patients"], 100)
        self.assertEqual(summary["demand_multiplier"], 0.8)
        self.assertEqual(summary["generated"], 80)
        self.assertEqual(summary["unresolved"], 0)
        arrivals = [row for row in trace if row["event"] == "arrived"]
        self.assertEqual(len(arrivals), 80)

    def test_one_hundred_patient_day_respects_routes_and_room_capacities(self) -> None:
        summary, trace = simulate_day(load_fixture())
        self.assertEqual(summary["generated"], 100)
        self.assertEqual(summary["unresolved"], 0)
        self.assertEqual(summary["discharged"] + summary["hospitalized"] +
                         summary["abandoned"], 100)
        self.assertLessEqual(summary["max_general_waiting_chair_occupancy"], 36)
        self.assertLessEqual(summary["max_c2_waiting_chair_occupancy"], 24)
        admissions = {r["patient_id"] for r in trace if r["event"] == "admission_completed"}
        self.assertEqual(len(admissions), 100)
        for row in (r for r in trace if r["event"] == "triage_completed"):
            expected = "c2" if row["category"] == "C2" else (
                "resuscitation" if row["category"] == "C1" else "general")
            self.assertEqual(row["destination"], expected)
        self.assertTrue(all(r["seat_released"] for r in trace
                            if r["event"] == "called_to_triage"))
        patient_by_id = {r["patient_id"]: r for r in trace
                         if r["event"] == "patient_summary"}
        c1_ids = {pid for pid, patient in patient_by_id.items()
                  if patient["triage_category"] == "C1"}
        self.assertEqual(summary["physicians_active"], 8)
        self.assertEqual([row["box_id"] for row in summary["physician_box_assignments"]],
                         [f"BOX-{i:02d}" for i in range(1, 9)])
        self.assertEqual({r["patient_id"] for r in trace
                          if r["event"] == "physician_attended_c1"}, c1_ids)
        self.assertEqual({r["patient_id"] for r in trace
                          if r["event"] == "physician_returned_to_box"}, c1_ids)
        self.assertEqual({r["patient_id"] for r in trace
                          if r["event"] == "nurse_attended_c1"}, c1_ids)
        physician_boxes = {}
        for row in (r for r in trace if r["event"] == "care_stage_started"):
            physician_boxes.setdefault(row["physician_id"], row["home_box"])
            self.assertEqual(physician_boxes[row["physician_id"]], row["home_box"])
            if row["resource"] == "box" and row["patient_id"] not in c1_ids:
                self.assertEqual(row["required_specialty"], row["specialty"])
        nursing_events = [r for r in trace
                          if r["event"] == "treatment_nurse_procedure_started"]
        observation_events = [r for r in trace
                              if r["event"] == "treatment_observation_started"]
        self.assertTrue(nursing_events)
        self.assertTrue(observation_events)
        self.assertTrue(all(r["room"] == "c2" and r["chair_state"] in
                            {"seated", "standing"} and
                            5 <= r["duration_minutes"] <= 10
                            for r in nursing_events))
        self.assertTrue(all(30 <= r["duration_minutes"] <= 120
                            for r in observation_events))
        triage_durations = {r["mode"]: [] for r in trace
                            if r["event"] == "triage_started"}
        for row in trace:
            if row["event"] == "triage_started":
                triage_durations[row["mode"]].append(row["duration_minutes"])
        self.assertTrue(all(1 <= duration <= 5
                            for duration in triage_durations.get("prioria", [])))
        self.assertTrue(all(10 <= duration <= 15
                            for duration in triage_durations.get("human", [])))
        self.assertTrue(any(row["event"] == "called_to_reevaluation" for row in trace))
        patient_rows = [r for r in trace if r["event"] == "patient_summary"]
        self.assertTrue(all(r["treatments_received"] == 0 for r in patient_rows
                            if r["triage_category"] in {"C4", "C5"}))
        self.assertEqual(summary["metadata"]["deis_values_verified"], False)

    def test_second_failed_treatment_requires_hospitalization(self) -> None:
        config = copy.deepcopy(load_fixture())
        config["daily_patients"] = 1
        config["age_group_probabilities"] = {
            "adult_young": 0.0, "adult_middle": 1.0, "adult_older": 0.0}
        config["triage_category_probabilities"] = {
            "C1": 0.0, "C2": 1.0, "C3": 0.0, "C4": 0.0, "C5": 0.0}
        config["deis_cause_probabilities_by_age"] = {
            key: {"thoracic_pain": 1.0} for key in
            ("adult_young", "adult_middle", "adult_older")}
        config["arrival"]["arrival_mode_shares"] = {
            "own_means": 1.0, "ambulance": 0.0, "police": 0.0}
        config["arrival"]["prioria_adoption_rate"] = 0.0
        config["arrival"]["prioria_adoption_adjustment_by_age"] = {
            "adult_young": 0.0, "adult_middle": 0.0, "adult_older": 0.0}
        config["care"]["treatment_probability_by_category"] = {
            "C1": 1.0, "C2": 1.0, "C3": 1.0, "C4": 0.0, "C5": 0.0}
        config["care"]["treatment_duration_minutes"] = {"minimum": 30, "maximum": 30}
        config["care"]["reevaluation_discharge_probability"] = 0.0
        summary, trace = simulate_day(config)
        self.assertEqual(summary["hospitalized"], 1)
        self.assertEqual(summary["discharged"], 0)
        self.assertEqual(summary["abandoned"], 0)
        self.assertEqual(sum(r["event"] == "treatment_nurse_procedure_started"
                             for r in trace), 2)
        self.assertEqual(sum(r["event"] == "treatment_observation_started"
                             for r in trace), 2)
        self.assertEqual(sum(r["event"] == "treatment_failed" for r in trace), 2)
        self.assertTrue(any(r.get("reason") == "dos_tratamientos_fallidos"
                            for r in trace if r["event"] == "hospitalized"))

    def test_c1_uses_triage_nurse_only_if_attention_pool_is_empty(self) -> None:
        config = copy.deepcopy(load_fixture())
        config["daily_patients"] = 1
        config["age_group_probabilities"] = {
            "adult_young": 0.0, "adult_middle": 1.0, "adult_older": 0.0}
        config["triage_category_probabilities"] = {
            "C1": 1.0, "C2": 0.0, "C3": 0.0, "C4": 0.0, "C5": 0.0}
        config["deis_cause_probabilities_by_age"] = {
            key: {"thoracic_pain": 1.0} for key in
            ("adult_young", "adult_middle", "adult_older")}
        config["arrival"]["arrival_mode_shares"] = {
            "own_means": 0.0, "ambulance": 1.0, "police": 0.0}
        config["arrival"]["prioria_adoption_rate"] = 0.0
        config["arrival"]["prioria_adoption_adjustment_by_age"] = {
            "adult_young": 0.0, "adult_middle": 0.0, "adult_older": 0.0}
        config["resources"]["box_nurses"] = 0
        summary, trace = simulate_day(config)
        c1_nurse_events = [r for r in trace if r["event"] == "nurse_attended_c1"]
        self.assertEqual(len(c1_nurse_events), 1)
        self.assertEqual(c1_nurse_events[0]["nurse_source"], "triage_fallback")
        self.assertEqual(summary["unresolved"], 0)

        config["resources"]["box_nurses"] = 1
        _, trace_with_attention_nurse = simulate_day(config)
        c1_nurse_events = [r for r in trace_with_attention_nurse
                           if r["event"] == "nurse_attended_c1"]
        self.assertEqual(len(c1_nurse_events), 1)
        self.assertEqual(c1_nurse_events[0]["nurse_source"], "attention")

    def test_active_physicians_require_one_exclusive_box_each(self) -> None:
        config = load_fixture()
        config["resources"]["consultation_boxes"] = 7
        with self.assertRaisesRegex(ValueError, "box exclusivo"):
            simulate_day(config)

    def test_congestion_exercises_standing_and_abandonment(self) -> None:
        config = copy.deepcopy(load_fixture())
        config["resources"].update({
            "physicians_by_specialty": {
                "internal_medicine": 1, "traumatology": 1, "surgery": 1},
            "consultation_boxes": 3,
            "box_nurses": 3,
            "general_waiting_chairs": 1,
            "c2_waiting_chairs": 1,
        })
        config["abandonment"].update({
            "risk_intercept": -2.5, "beta_wait": 2.5,
            "wait_scale_minutes": 30.0, "beta_low_acuity": 1.5,
            "beta_standing": 1.5, "threshold_range": [0.90, 0.99]
        })
        summary, trace = simulate_day(config)
        self.assertGreater(summary["abandoned"], 0)
        self.assertGreater(summary["standing_patient_minutes"], 0)
        self.assertTrue(any(r.get("event") == "abandonment_risk_crossed" and r.get("standing")
                            for r in trace))
        self.assertLessEqual(summary["max_general_waiting_chair_occupancy"], 1)
        self.assertLessEqual(summary["max_c2_waiting_chair_occupancy"], 1)


if __name__ == "__main__":
    unittest.main()
