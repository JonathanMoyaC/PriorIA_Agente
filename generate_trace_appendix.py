"""Build the commented LaTeX trace annex from the committed simulator logs."""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def tex(value):
    value = str(value)
    return (value.replace("\\", r"\textbackslash{}").replace("&", r"\&")
            .replace("%", r"\%").replace("$", r"\$").replace("#", r"\#")
            .replace("_", r"\_").replace("{", r"\{").replace("}", r"\}")
            .replace("~", r"\textasciitilde{}").replace("^", r"\textasciicircum{}"))


def read_trace(path, episode_id, policy="prioria_beam", id_field="episode_id"):
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if (row.get(id_field) == episode_id and row.get("policy") == policy
                    and row.get("observation") is not None and row.get("decision") is not None):
                rows.append(row)
    return sorted(rows, key=lambda x: (x.get("time", 0),
                                       x.get("decision", {}).get("kind", "")))


def read_event_log(path, episode_id):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("episode_id") == episode_id and row.get("event_log"):
                return row["event_log"]
    return []


def observation_summary(row):
    obs = row.get("observation", {})
    queue = obs.get("waiting", [])
    patients = ", ".join(
        f"{p['patient_id']}(P{p['priority']}, espera {p['wait_minutes']}m)"
        for p in queue
    ) or "vacía"
    free = [b["box_id"] for b in obs.get("boxes", []) if not b.get("busy")]
    pairs = ", ".join(f"{p['patient_id']}/{p['box_id']}"
                       for p in row.get("feasible_pairs", [])) or "ninguno"
    return patients, ", ".join(free) or "ninguno", pairs


def make_table(episode, label, rows, comment, event_log):
    lines = [
        f"\\subsection{{{tex(episode)}: {tex(label)}}}",
        tex(comment),
        "\\begin{longtable}{r p{3.0cm} p{1.25cm} p{2.7cm} p{2.0cm} p{2.4cm}}",
        "\\caption{" + tex(f"Traza de decisiones de {episode}") + "}\\label{tab:trace_" + episode.lower() + "}\\\\",
        "\\toprule t & Cola elegible (prioridad, espera) & Boxes libres & Pares factibles & Acción & Comentario / evento " + r"\\",
        "\\midrule\\endfirsthead",
        "\\toprule t & Cola elegible (prioridad, espera) & Boxes libres & Pares factibles & Acción & Comentario / evento " + r"\\",
        "\\midrule\\endhead",
    ]
    for row in rows:
        queue, free, pairs = observation_summary(row)
        dec = row.get("decision", {})
        kind = dec.get("kind", "?")
        action = kind
        if kind == "assign":
            action += f" {dec.get('patient_id')} a {dec.get('box_id')}"
        rationale = dec.get("rationale", "")
        events = ", ".join(row.get("events", [])) or "sin transición"
        latency = row.get("decision_latency_ms")
        latency_text = f"; {latency:.3f} ms" if isinstance(latency, (int, float)) else ""
        lines.append(f"{row.get('time', 0)} & {tex(queue)} & {tex(free)} & {tex(pairs)} & {tex(action)} & {tex(rationale)}; {tex(events)}{latency_text} \\\\")
    lines += ["\\bottomrule", "\\end{longtable}", ""]
    if event_log:
        lines += ["\\paragraph{Secuencia completa de eventos}",
                  "\\begin{longtable}{r p{2.2cm} p{4.2cm}}",
                  "\\toprule t & Evento & Entidad / transición " + r"\\",
                  "\\midrule\\endfirsthead",
                  "\\toprule t & Evento & Entidad / transición " + r"\\",
                  "\\midrule\\endhead"]
        for event in event_log:
            entity = ", ".join(f"{k}={event[k]}" for k in ("patient_id", "box_id", "to") if k in event)
            lines.append(f"{event.get('time', 0)} & {tex(event.get('event', ''))} & {tex(entity)} " + r"\\")
        lines += ["\\bottomrule", "\\end{longtable}", ""]
    return "\n".join(lines)


eval_path = ROOT / "outputs/experiment/evaluation/traces.jsonl"
safety_path = ROOT / "outputs/safety/safety_traces.jsonl"
metrics_path = ROOT / "outputs/experiment/evaluation/scenario_metrics.csv"
metrics = {}
with metrics_path.open(encoding="utf-8", newline="") as handle:
    for row in csv.DictReader(handle):
        if row["policy"] == "prioria_beam":
            metrics[row["episode_id"]] = row

specs = [
    ("T111", "mayor mejora observada", eval_path,
     "Ejemplo ilustrativo seleccionado por tener la mayor reducción de espera primaria observada: 90,056 a 71,889 min (−18,167 min)."),
    ("T114", "caso desfavorable", eval_path,
     "Ejemplo ilustrativo seleccionado por el mayor aumento de espera primaria observado: 68,611 a 82,833 min (+14,222 min)."),
    ("S11_search_timeout_fallback", "fallback por límite de búsqueda", safety_path,
     "Escenario diseñado para forzar timeout con límite configurado en cero; cada elección compatible usa la política reactiva segura."),
]
parts = []
for eid, label, path, comment in specs:
    rows = read_trace(path, eid, id_field="safety_scenario_id" if eid.startswith("S11") else "episode_id")
    if eid.startswith("S11"):
        # The scenario contains multiple policies; retain the PriorIA rows only.
        assert rows and all(r["policy"] == "prioria_beam" for r in rows)
    else:
        assert rows, f"No hay registros de {eid}"
        m = metrics[eid]
        assert len(rows) >= 1
    parts.append(make_table(eid, label, rows, comment,
                            read_event_log(path, eid) if not eid.startswith("S11") else []))

(ROOT / "trace_appendix.tex").write_text("\n".join(parts), encoding="utf-8")
print("Anexo generado:", ", ".join(f"{eid}: {len(read_trace(path, eid, id_field='safety_scenario_id' if eid.startswith('S11') else 'episode_id'))} decisiones" for eid, _, path, _ in specs))
