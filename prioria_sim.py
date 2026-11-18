"""Minimal event-driven simulator and policies for the PriorIA box-allocation study.

All values in the demo are synthetic operational assumptions. They are not a
clinical protocol or a representation of a named hospital.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable
import json
import random
from time import perf_counter

import simpy


@dataclass(frozen=True)
class Patient:
    patient_id: str
    eligible_at: int
    priority: int  # Lower integer means higher operational priority.
    required_capabilities: frozenset[str]
    service_class: str
    actual_service_minutes: int  # Hidden from policies; environment truth only.
    patience_minutes: int = 60


@dataclass(frozen=True)
class Box:
    box_id: str
    capabilities: frozenset[str]


@dataclass(frozen=True)
class PatientPercept:
    patient_id: str
    eligible_at: int
    priority: int
    required_capabilities: frozenset[str]
    service_class: str
    wait_minutes: int
    patience_minutes: int


@dataclass(frozen=True)
class BoxPercept:
    box_id: str
    capabilities: frozenset[str]
    busy: bool
    expected_available_at: float


@dataclass(frozen=True)
class Observation:
    episode_id: str
    now: int
    horizon: int
    waiting: tuple[PatientPercept, ...]
    boxes: tuple[BoxPercept, ...]
    expected_service_minutes: dict[str, float]


@dataclass
class Decision:
    kind: str
    patient_id: str | None = None
    box_id: str | None = None
    rationale: str = ""
    deliberation: dict[str, Any] = field(default_factory=dict)


class AllocationPolicy:
    name = "abstract"

    def select_action(self, observation: Observation) -> Decision:
        raise NotImplementedError


def _compatible(patient: PatientPercept, box: BoxPercept) -> bool:
    return patient.required_capabilities.issubset(box.capabilities)


class FixedPipeline(AllocationPolicy):
    """Priority, FIFO, then first compatible free box in fixed ID order."""

    name = "fixed_pipeline"

    def select_action(self, observation: Observation) -> Decision:
        waiting = sorted(observation.waiting,
                         key=lambda p: (p.priority, p.eligible_at, p.patient_id))
        boxes = sorted((b for b in observation.boxes if not b.busy),
                       key=lambda b: b.box_id)
        for patient in waiting:
            for box in boxes:
                if _compatible(patient, box):
                    return Decision("assign", patient.patient_id, box.box_id,
                                    "Mayor prioridad; FIFO; primer box compatible libre.")
        return Decision("wait", rationale="No existe una pareja paciente-box factible ahora.")


class ReactivePolicy(AllocationPolicy):
    """Current-state rule: priority, longest wait, earliest expected box release."""

    name = "reactive"

    def select_action(self, observation: Observation) -> Decision:
        patients = sorted(observation.waiting,
                          key=lambda p: (p.priority, -p.wait_minutes,
                                         p.eligible_at, p.patient_id))
        boxes = sorted(observation.boxes,
                       key=lambda b: (b.expected_available_at, b.box_id))
        for patient in patients:
            for box in boxes:
                if not box.busy and _compatible(patient, box):
                    return Decision("assign", patient.patient_id, box.box_id,
                                    "Mayor prioridad; mayor espera; box compatible disponible antes.")
        return Decision("wait", rationale="No existe una pareja paciente-box factible ahora.")


@dataclass
class _BeamNode:
    now: float
    remaining: tuple[str, ...]
    release_at: tuple[float, ...]
    cost: float
    path: tuple[tuple[str, str, str], ...]
    waits: int = 0


class BeamSearchPolicy(AllocationPolicy):
    """Receding-horizon beam search with expected service times (determinization)."""

    name = "prioria_beam"

    def __init__(self, width: int = 20, horizon: int = 5):
        self.width = width
        self.search_horizon = horizon

    @staticmethod
    def _weight(priority: int) -> float:
        return float(max(1, 6 - priority))

    def _score(self, node: _BeamNode, patients: dict[str, PatientPercept]) -> float:
        # Penalize queue exposure and especially predicted patience violations.
        residual = 0.0
        for patient_id in node.remaining:
            patient = patients[patient_id]
            elapsed = max(0.0, node.now - patient.eligible_at)
            residual += self._weight(patient.priority) * (elapsed + 10.0)
            if elapsed >= patient.patience_minutes:
                residual += 1000.0
        return node.cost + residual

    def select_action(self, observation: Observation) -> Decision:
        if not observation.waiting:
            return Decision("wait", rationale="La cola elegible está vacía.")

        patients = {p.patient_id: p for p in observation.waiting}
        boxes = tuple(sorted(observation.boxes, key=lambda b: b.box_id))
        root = _BeamNode(float(observation.now), tuple(patients),
                         tuple(float(b.expected_available_at) for b in boxes),
                         0.0, ())
        beam = [root]
        explored: list[dict[str, Any]] = []

        for depth in range(self.search_horizon):
            children: list[_BeamNode] = []
            for node in beam:
                if not node.remaining:
                    children.append(node)
                    continue
                free_indices = [i for i, release in enumerate(node.release_at)
                                if release <= node.now]
                feasible = [(patient_id, box_i)
                            for patient_id in node.remaining
                            for box_i in free_indices
                            if _compatible(patients[patient_id], boxes[box_i])]

                if feasible:
                    for patient_id, box_i in feasible:
                        patient = patients[patient_id]
                        wait = max(0.0, node.now - patient.eligible_at)
                        expected_service = observation.expected_service_minutes.get(
                            patient.service_class, 30.0)
                        release_at = list(node.release_at)
                        release_at[box_i] = node.now + max(1.0, expected_service)
                        children.append(_BeamNode(
                            node.now,
                            tuple(pid for pid in node.remaining if pid != patient_id),
                            tuple(release_at),
                            node.cost + self._weight(patient.priority) * wait,
                            node.path + (("assign", patient_id, boxes[box_i].box_id),),
                            node.waits))
                else:
                    future_releases = [release for release in node.release_at
                                       if release > node.now]
                    if future_releases:
                        next_time = min(future_releases)
                        delta = next_time - node.now
                        accrued = sum(self._weight(patients[pid].priority) * delta
                                      for pid in node.remaining)
                        children.append(_BeamNode(next_time, node.remaining,
                                                  node.release_at, node.cost + accrued,
                                                  node.path + (("wait", "", ""),), node.waits + 1))
                    else:
                        children.append(node)

            if not children:
                break
            children.sort(key=lambda node: (self._score(node, patients),
                                             node.path, node.now))
            beam = children[:self.width]
            explored.extend({"depth": depth + 1, "score": round(self._score(n, patients), 4),
                             "path": list(n.path), "simulated_time": n.now}
                            for n in beam)

        best = min(beam, key=lambda node: (self._score(node, patients), node.path))
        if best.path and best.path[0][0] == "assign":
            _, patient_id, box_id = best.path[0]
            return Decision("assign", patient_id, box_id,
                            "Primera asignación del plan de menor costo estimado.",
                            {"beam_width": self.width,
                             "search_horizon": self.search_horizon,
                             "selected_plan": [{"kind": kind, "patient_id": patient_id,
                                                "box_id": box_id}
                                               for kind, patient_id, box_id in best.path],
                             "selected_score": round(self._score(best, patients), 4),
                             "retained_nodes": explored})

        return Decision("wait", rationale="La búsqueda recomienda esperar al siguiente evento.",
                        deliberation={"beam_width": self.width,
                                      "search_horizon": self.search_horizon,
                                      "retained_nodes": explored})


class EventSimulator:
    def __init__(self, boxes: Iterable[Box], expected_service_minutes: dict[str, float],
                 horizon_minutes: int = 240):
        self.boxes = tuple(sorted(boxes, key=lambda b: b.box_id))
        self.expected_service_minutes = dict(expected_service_minutes)
        self.horizon_minutes = horizon_minutes

    def run(self, episode_id: str, seed: int, patients: Iterable[Patient],
            policy: AllocationPolicy) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Run the existing allocation model on a SimPy event environment.

        The current research prototype begins after eligibility/triage and models
        waiting patients, compatible box assignment, service, abandonment, and
        box release. The broader admission/cabin/triage/observation workflow is a
        separate process-model extension, not implied by this class.
        """
        truth = {p.patient_id: p for p in patients}
        arrival_order = sorted(truth.values(), key=lambda p: (p.eligible_at, p.patient_id))
        env = simpy.Environment()
        waiting: dict[str, Patient] = {}
        box_until: dict[str, int] = {b.box_id: 0 for b in self.boxes}
        box_started_at: dict[str, int] = {b.box_id: 0 for b in self.boxes}
        box_service_class: dict[str, str | None] = {b.box_id: None for b in self.boxes}
        box_resources = {b.box_id: simpy.Resource(env, capacity=1) for b in self.boxes}
        assigned: dict[str, int] = {}
        abandoned: dict[str, int] = {}
        invalid_assignment_attempts = 0
        trace: list[dict[str, Any]] = []
        event_log: list[dict[str, Any]] = []
        decision_latencies_ms: list[float] = []

        def patient_arrival(patient: Patient):
            yield env.timeout(patient.eligible_at)
            if patient.eligible_at <= self.horizon_minutes:
                waiting[patient.patient_id] = patient
                event_log.append({"time": env.now, "event": "eligible",
                                  "patient_id": patient.patient_id})

        def patient_abandonment(patient: Patient):
            abandon_at = patient.eligible_at + patient.patience_minutes
            if abandon_at > self.horizon_minutes:
                return
            yield env.timeout(abandon_at)
            if patient.patient_id in waiting:
                waiting.pop(patient.patient_id)
                abandoned[patient.patient_id] = int(env.now)
                event_log.append({"time": env.now, "event": "abandoned",
                                  "patient_id": patient.patient_id})

        def box_service(patient: Patient, box_id: str):
            with box_resources[box_id].request() as request:
                yield request
                yield env.timeout(patient.actual_service_minutes)
                if box_service_class[box_id] is not None:
                    box_service_class[box_id] = None
                    event_log.append({"time": env.now, "event": "box_released",
                                      "box_id": box_id, "patient_id": patient.patient_id})

        for patient in arrival_order:
            if patient.eligible_at <= self.horizon_minutes:
                env.process(patient_arrival(patient))
                env.process(patient_abandonment(patient))
        # Ensures the environment reaches the horizon even when no patient event remains.
        env.timeout(self.horizon_minutes)

        previous_time = 0
        while True:
            next_time = env.peek()
            if next_time == float("inf") or next_time > self.horizon_minutes:
                break
            now = int(next_time)
            if now > previous_time:
                event_log.append({"time": previous_time, "event": "clock_advanced", "to": now})
                previous_time = now

            # Process every SimPy event at this timestamp before making a decision.
            while env.peek() == next_time:
                env.step()

            if now >= self.horizon_minutes:
                break

            # Dispatch repeatedly at the same simulated minute while a feasible pair exists.
            while True:
                observation = self._observe(episode_id, now, waiting, box_until,
                                            box_started_at, box_service_class)
                decision_started = perf_counter()
                decision = policy.select_action(observation)
                decision_latency_ms = (perf_counter() - decision_started) * 1000.0
                decision_latencies_ms.append(decision_latency_ms)
                feasible = self._feasible(observation)
                decision_record = {"episode_id": episode_id, "seed": seed, "time": now,
                    "policy": policy.name, "observation": self._observation_json(observation),
                    "internal_state": ({"waiting_ids": [p.patient_id for p in observation.waiting],
                                        "box_states": [{**asdict(b),
                                                        "capabilities": sorted(b.capabilities)}
                                                       for b in observation.boxes]}
                                       if isinstance(policy, BeamSearchPolicy) else None),
                    "feasible_pairs": feasible, "decision": asdict(decision),
                    "decision_latency_ms": round(decision_latency_ms, 4), "events": []}
                trace.append(decision_record)

                if decision.kind != "assign":
                    break
                patient = waiting.get(decision.patient_id or "")
                box = next((b for b in self.boxes if b.box_id == decision.box_id), None)
                resource_free = (box is not None and not box_resources[box.box_id].users
                                 and box_service_class[box.box_id] is None)
                if (patient is not None and box is not None and resource_free
                        and patient.required_capabilities.issubset(box.capabilities)):
                    waiting.pop(patient.patient_id)
                    assigned[patient.patient_id] = now
                    box_until[box.box_id] = now + patient.actual_service_minutes
                    box_started_at[box.box_id] = now
                    box_service_class[box.box_id] = patient.service_class
                    event_log.append({"time": now, "event": "assigned",
                                      "patient_id": patient.patient_id, "box_id": box.box_id})
                    decision_record["events"].append("assignment_executed")
                    env.process(box_service(patient, box.box_id))
                    # Acquire the SimPy resource before considering another dispatch.
                    while env.peek() == now:
                        env.step()
                    continue

                decision_record["events"].append("invalid_assignment_rejected")
                invalid_assignment_attempts += 1
                break

        # For the primary restricted wait, every patient without an assignment receives
        # the horizon cap, including patients who abandoned earlier. This treats
        # abandonment as an unsuccessful assignment outcome rather than rewarding it.
        censored = {pid: min(self.horizon_minutes, max(0, self.horizon_minutes - p.eligible_at))
                    for pid, p in truth.items()
                    if pid not in assigned and p.eligible_at <= self.horizon_minutes}
        assigned_waits = [assigned[pid] - truth[pid].eligible_at for pid in assigned]
        primary_waits = list(assigned_waits) + list(censored.values())
        observed_waits = [assigned[pid] - truth[pid].eligible_at for pid in assigned]
        observed_waits += [abandoned[pid] - truth[pid].eligible_at for pid in abandoned]
        observed_waits += list(censored.values())
        ordered_latencies = sorted(decision_latencies_ms)
        p95_index = max(0, int(0.95 * len(ordered_latencies) + 0.999999) - 1)
        metrics = {
            "episode_id": episode_id,
            "seed": seed,
            "policy": policy.name,
            "patients_eligible": sum(p.eligible_at <= self.horizon_minutes for p in truth.values()),
            "assigned": len(assigned),
            "abandoned": len(abandoned),
            "not_assigned_at_horizon": len(censored),
            "primary_mean_restricted_wait_to_occupancy_min": round(sum(primary_waits) / len(primary_waits), 4)
                if primary_waits else None,
            "mean_wait_to_occupancy_assigned_only_min": round(sum(assigned_waits) / len(assigned_waits), 4)
                if assigned_waits else None,
            "mean_observed_wait_to_terminal_min": round(sum(observed_waits) / len(observed_waits), 4)
                if observed_waits else None,
            "abandonment_rate": round(len(abandoned) / max(1, sum(
                p.eligible_at <= self.horizon_minutes for p in truth.values())), 6),
            "invalid_assignment_attempts_rejected": invalid_assignment_attempts,
            "executed_incompatible_assignments": 0,
            "decision_count": len(decision_latencies_ms),
            "mean_decision_time_ms": round(sum(decision_latencies_ms) / len(decision_latencies_ms), 4)
                if decision_latencies_ms else None,
            "p95_decision_time_ms": round(ordered_latencies[p95_index], 4)
                if ordered_latencies else None,
            "horizon_minutes": self.horizon_minutes,
        }
        event_log.sort(key=lambda e: e["time"])
        return metrics, trace + [{"episode_id": episode_id, "seed": seed,
                                  "policy": policy.name, "event_log": event_log}]

    def _observe(self, episode_id: str, now: int, waiting: dict[str, Patient],
                 box_until: dict[str, int], box_started_at: dict[str, int],
                 box_service_class: dict[str, str | None]) -> Observation:
        patients = tuple(PatientPercept(p.patient_id, p.eligible_at, p.priority,
                                        p.required_capabilities, p.service_class,
                                        now - p.eligible_at, p.patience_minutes)
                         for p in sorted(waiting.values(),
                                         key=lambda p: (p.priority, p.eligible_at, p.patient_id)))
        boxes: list[BoxPercept] = []
        for box in self.boxes:
            busy = box_until[box.box_id] > now
            if busy:
                service_class = box_service_class[box.box_id]
                expected = self.expected_service_minutes.get(service_class or "", 30.0)
                # Approximate remaining service from the scheduled expected duration minus elapsed time.
                elapsed = max(0, now - box_started_at[box.box_id])
                expected_available = now + max(0.0, expected - elapsed)
            else:
                expected_available = now
            boxes.append(BoxPercept(box.box_id, box.capabilities, busy, expected_available))
        return Observation(episode_id, now, self.horizon_minutes, patients, tuple(boxes),
                           self.expected_service_minutes)

    @staticmethod
    def _feasible(observation: Observation) -> list[dict[str, str]]:
        return [{"patient_id": p.patient_id, "box_id": b.box_id}
                for p in observation.waiting for b in observation.boxes
                if not b.busy and _compatible(p, b)]

    @staticmethod
    def _observation_json(observation: Observation) -> dict[str, Any]:
        return {"episode_id": observation.episode_id, "now": observation.now,
                "horizon": observation.horizon,
                "waiting": [{**asdict(p), "required_capabilities": sorted(p.required_capabilities)}
                            for p in observation.waiting],
                "boxes": [{**asdict(b), "capabilities": sorted(b.capabilities)}
                          for b in observation.boxes],
                "expected_service_minutes": observation.expected_service_minutes}


def generate_scenario(seed: int = 20261113, patient_count: int = 18,
                      arrival_interval_minutes: int = 15,
                      arrivals_per_batch: int = 3,
                      patience_minutes: int = 60,
                      service_means: dict[str, float] | None = None,
                      service_sd_minutes: float = 5.0,
                      priority_probabilities: list[float] | None = None,
                      boxes: list[Box] | None = None,
                      requirement_catalog: list[frozenset[str]] | None = None
                      ) -> tuple[list[Patient], list[Box]]:
    """Generate one editable synthetic turn; all capabilities are abstract tags."""
    rng = random.Random(seed)
    boxes = boxes or [Box("B01", frozenset({"basic"})),
                      Box("B02", frozenset({"basic", "monitoring"})),
                      Box("B03", frozenset({"basic", "procedure"}))]
    requirements = requirement_catalog or [frozenset({"basic"}),
                                            frozenset({"basic", "monitoring"}),
                                            frozenset({"basic", "procedure"})]
    service_classes = service_means or {"short": 20, "medium": 40, "long": 65}
    class_names = list(service_classes)
    class_probabilities = ([0.4, 0.4, 0.2] if class_names == ["short", "medium", "long"]
                           else [1.0 / len(class_names)] * len(class_names))
    priority_probabilities = priority_probabilities or [0.2] * 5
    patients: list[Patient] = []
    for i in range(patient_count):
        service_class = rng.choices(class_names, class_probabilities)[0]
        mean = service_classes[service_class]
        priority = (rng.randint(1, 5) if all(abs(p - 0.2) < 1e-9
                                             for p in priority_probabilities)
                    else rng.choices([1, 2, 3, 4, 5], priority_probabilities)[0])
        requirement = requirements[rng.randrange(len(requirements))]
        patients.append(Patient(
            patient_id=f"P{i + 1:03d}",
            eligible_at=(i // arrivals_per_batch) * arrival_interval_minutes,
            priority=priority,
            required_capabilities=requirement,
            service_class=service_class,
            actual_service_minutes=max(1, int(rng.gauss(mean, service_sd_minutes))),
            patience_minutes=patience_minutes))
    return patients, boxes


def demo_scenario(seed: int = 20261113) -> tuple[list[Patient], list[Box]]:
    """Backward-compatible alias for the original one-turn demo."""
    return generate_scenario(seed=seed)


def run_demo(seed: int = 20261113) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    patients, boxes = demo_scenario(seed)
    simulator = EventSimulator(boxes, {"short": 20, "medium": 40, "long": 65},
                               horizon_minutes=240)
    policies: list[AllocationPolicy] = [FixedPipeline(), ReactivePolicy(), BeamSearchPolicy(20, 5)]
    results: list[dict[str, Any]] = []
    traces: list[dict[str, Any]] = []
    for policy in policies:
        metrics, policy_trace = simulator.run("demo-001", seed, patients, policy)
        results.append(metrics)
        traces.extend(policy_trace)
    return results, traces


if __name__ == "__main__":
    results, traces = run_demo()
    print(json.dumps({"results": results, "trace_records": len(traces)}, indent=2))
