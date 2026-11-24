"""Create a review PDF from the report data and complete logged episodes."""
import csv
import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, KeepTogether, NextPageTemplate, PageBreak,
    PageTemplate, Paragraph, Spacer, Table, TableStyle, Flowable,
)

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "informe_prioria.pdf"
PAGE_W, PAGE_H = letter
MARGIN = 0.62 * inch
GUTTER = 0.23 * inch
COL_W = (PAGE_W - 2 * MARGIN - GUTTER) / 2

pdfmetrics.registerFont(TTFont("DejaVu", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DejaVu-Bold", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
pdfmetrics.registerFont(TTFont("DejaVu-Serif", "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"))

styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name="TitleX", parent=styles["Title"], fontName="DejaVu-Bold", fontSize=17, leading=21, alignment=TA_CENTER, spaceAfter=9))
styles.add(ParagraphStyle(name="AuthorX", parent=styles["Normal"], fontName="DejaVu", fontSize=10, leading=13, alignment=TA_CENTER, spaceAfter=18))
styles.add(ParagraphStyle(name="AbstractX", parent=styles["Normal"], fontName="DejaVu", fontSize=8.8, leading=11.5, alignment=TA_JUSTIFY, leftIndent=22, rightIndent=22, spaceAfter=8))
styles.add(ParagraphStyle(name="SectionX", parent=styles["Heading1"], fontName="DejaVu-Bold", fontSize=11, leading=13, spaceBefore=8, spaceAfter=4, keepWithNext=True))
styles.add(ParagraphStyle(name="SubsectionX", parent=styles["Heading2"], fontName="DejaVu-Bold", fontSize=9.5, leading=11, spaceBefore=6, spaceAfter=3, keepWithNext=True))
styles.add(ParagraphStyle(name="BodyX", parent=styles["BodyText"], fontName="DejaVu-Serif", fontSize=8.5, leading=10.3, alignment=TA_JUSTIFY, spaceAfter=5))
styles.add(ParagraphStyle(name="SmallX", parent=styles["BodyText"], fontName="DejaVu", fontSize=7.2, leading=8.5, alignment=TA_LEFT, spaceAfter=3))
styles.add(ParagraphStyle(name="TinyX", parent=styles["BodyText"], fontName="DejaVu", fontSize=5.4, leading=6.5, alignment=TA_LEFT, splitLongWords=1))
styles.add(ParagraphStyle(name="CenterX", parent=styles["BodyText"], fontName="DejaVu", fontSize=8, leading=10, alignment=TA_CENTER))


class DecisionCycle(Flowable):
    def __init__(self):
        super().__init__()
        self.width = COL_W
        self.height = 0.86 * inch

    def draw(self):
        c = self.canv
        labels = ["Observación\ncola y boxes", "Estado\n+ restricciones", "Búsqueda\nB=20, H=5", "Acción\ny transición"]
        box_w, box_h = 0.73*inch, 0.42*inch
        gap = 0.12*inch
        x0, y0 = 0.03*inch, 0.27*inch
        c.setStrokeColor(colors.HexColor("#54738A"))
        c.setFillColor(colors.HexColor("#E8EEF3"))
        for i, label in enumerate(labels):
            x = x0 + i*(box_w+gap)
            c.roundRect(x, y0, box_w, box_h, 4, stroke=1, fill=1)
            c.setFillColor(colors.HexColor("#152A3A"))
            c.setFont("Helvetica", 6.5)
            c.drawCentredString(x+box_w/2, y0+0.25*inch, label.split("\n")[0])
            c.drawCentredString(x+box_w/2, y0+0.13*inch, label.split("\n")[1])
            c.setFillColor(colors.HexColor("#E8EEF3"))
            if i < len(labels)-1:
                ax = x+box_w
                ay = y0+box_h/2
                c.line(ax, ay, ax+gap-0.025*inch, ay)
                c.line(ax+gap-0.025*inch, ay, ax+gap-0.07*inch, ay+0.035*inch)
                c.line(ax+gap-0.025*inch, ay, ax+gap-0.07*inch, ay-0.035*inch)
        # Feedback path: transition changes the next observation.
        right = x0+3*(box_w+gap)+box_w/2
        left = x0+box_w/2
        c.line(right, y0-0.02*inch, right, 0.08*inch)
        c.line(right, 0.08*inch, left, 0.08*inch)
        c.line(left, 0.08*inch, left, y0-0.02*inch)
        c.line(left, y0-0.02*inch, left-0.035*inch, y0+0.04*inch)
        c.line(left, y0-0.02*inch, left+0.035*inch, y0+0.04*inch)


def para(text, style="BodyX"):
    return Paragraph(text, styles[style])


def footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#9AA7B2"))
    canvas.line(MARGIN, 0.45 * inch, PAGE_W - MARGIN, 0.45 * inch)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#53616D"))
    canvas.drawString(MARGIN, 0.29 * inch, "PriorIA | borrador para revisión del autor")
    canvas.drawRightString(PAGE_W - MARGIN, 0.29 * inch, str(doc.page))
    canvas.restoreState()


full_frame = Frame(MARGIN, 0.62 * inch, PAGE_W - 2 * MARGIN, PAGE_H - 1.22 * inch, id="full", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
left_frame = Frame(MARGIN, 0.62 * inch, COL_W, PAGE_H - 1.22 * inch, id="left", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
right_frame = Frame(MARGIN + COL_W + GUTTER, 0.62 * inch, COL_W, PAGE_H - 1.22 * inch, id="right", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
doc = BaseDocTemplate(str(OUTPUT), pagesize=letter, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=0.58 * inch, bottomMargin=0.62 * inch, title="PriorIA: asignación de pacientes elegibles a boxes compatibles", author="Jonathan Moya C.")
doc.addPageTemplates([
    PageTemplate(id="TitlePage", frames=[full_frame], onPage=footer),
    PageTemplate(id="TwoCol", frames=[left_frame, right_frame], onPage=footer),
    PageTemplate(id="Appendix", frames=[full_frame], onPage=footer),
])

story = []
story += [para("PriorIA: asignación de pacientes elegibles a boxes compatibles mediante búsqueda en haz", "TitleX"), para("Jonathan Moya C.", "AuthorX")]
abstract = ("Este trabajo presenta PriorIA, un agente que recomienda la asignación de pacientes ya triageados a boxes compatibles en un entorno de urgencia sintético y discreto. El agente replanifica ante llegadas y liberaciones, usa búsqueda en haz de ancho 20 y horizonte 5, y aplica prioridad estricta entre pacientes actualmente factibles. Se comparó con un pipeline fijo y una política reactiva en 120 jornadas pareadas: 80 para desarrollo y 40 reservadas para evaluación. La métrica primaria fue la espera media restringida hasta ocupar un box compatible, con 240 minutos imputados a quienes no ocuparon uno. En evaluación, PriorIA redujo esta espera en 2,08 minutos por jornada frente al pipeline fijo (IC bootstrap pareado del 95%: [-3,93; -0,34]), una reducción relativa de 2,52%, inferior al objetivo preespecificado de 10%. La diferencia estimada de abandono fue -0,83 puntos porcentuales (IC 95%: [-2,08; +0,42]); su límite superior quedó bajo el margen de +1 pp. No se observaron asignaciones incompatibles ejecutadas ni intentos inválidos en los experimentos principales y las 33 corridas de escenarios de límites pasaron. El resultado es evidencia de una mejora pequeña en el simulador, no de efectividad clínica ni preparación para uso hospitalario.")
story += [para("Resumen", "SectionX"), para(abstract, "AbstractX"), para("Palabras clave: agentes inteligentes, búsqueda en haz, simulación de eventos discretos, asignación, urgencias.", "SmallX"), NextPageTemplate("TwoCol"), PageBreak()]

sections = [
    ("1. Introducción", [
        "La asignación de pacientes a espacios de atención debe considerar prioridad clínica y compatibilidad entre las necesidades del paciente y las capacidades disponibles. Este proyecto delimita la intervención a pacientes que ya completaron triage y fueron declarados elegibles; no realiza triage, diagnóstico, admisión ni asignación de cabina. La salida es una recomendación simulada.",
        "Pregunta: ¿un agente que replanifica y busca secuencias cortas de asignación reduce la espera restringida frente a un pipeline fijo sin exceder un aumento de abandono de un punto porcentual ni ejecutar asignaciones incompatibles? La hipótesis registrada antes de abrir la evaluación reservada fue una reducción de al menos 10% en espera media restringida, con aumento de abandono no mayor a 1 pp. La evaluación se ejecutó una vez con código y parámetros identificados por hash.",
    ]),
    ("2. Fundamentos y trabajo relacionado", [
        "Un agente basado en modelo mantiene una representación del estado y usa un modelo de transición para seleccionar acciones. La búsqueda en haz conserva un conjunto acotado de estados prometedores en cada nivel de expansión [1]. PriorIA compara planes cortos de asignación; no busca una política óptima global ni aprende de datos clínicos.",
        "La simulación de eventos discretos y los modelos de colas se usan para estudiar espera, ocupación y abandono en urgencias. Hurwitz et al. presentan una plataforma configurable que representa prioridades y tolerancia a la espera, y destacan que las intervenciones dependen del contexto [2]. Van de Vrugt y Boucherie estudian políticas de asignación de salas compartidas mediante teoría de colas y simulación [3]. PriorIA comparte el dominio de asignación, pero su contribución del curso es el ciclo estado-búsqueda-acción y la comparación explícita con un pipeline fijo. Este simulador no está calibrado con datos reales.",
    ]),
    ("3. PEAS y entorno", [
        "Desempeño: espera restringida hasta ocupar box compatible, abandono, asignaciones, utilización y seguridad. Entorno: cola de pacientes elegibles con prioridad, capacidades requeridas y paciencia; tres boxes abstractos con capacidades. Actuadores: asignar a un box compatible o esperar. Sensores: cola, estado de boxes, reloj y eventos de llegada y liberación.",
        "El entorno es completamente observable por el simulador, dinámico, discreto, secuencial y estocástico en llegadas y duraciones. Es de un solo agente decisor con pacientes y recursos pasivos. Cada jornada contiene 18 pacientes, 240 minutos y tiempos esperados de servicio de 20, 40 o 65 minutos. La unidad temporal es un minuto.",
        "Ciclo de decisión: observación de cola y boxes -> estado interno y restricciones -> búsqueda en haz -> asignar o esperar -> transición del entorno -> nueva observación. La política reactiva se basa en modelo y reglas locales; PriorIA es un agente basado en modelo con búsqueda acotada.",
    ]),
    ("4. Diseño del agente", [
        "Pipeline fijo ordena por prioridad, FIFO y primer box compatible libre. Reactiva decide ante eventos con la misma regla local. PriorIA usa búsqueda en haz B=20 y horizonte H=5, duraciones esperadas deterministas y replanifica ante eventos. La prioridad es estricta entre pacientes con algún box compatible libre; un paciente de mayor prioridad sin recurso compatible no bloquea a otro factible. La compatibilidad es una restricción dura. Un límite de búsqueda de 50 ms activa una acción reactiva segura.",
        "Quien supera su paciencia abandona según la regla temporal discreta. Si no hay asignación al final del horizonte, se conserva como no asignado y se imputan 240 minutos. La espera restringida se mide desde elegibilidad hasta ocupación, truncada en el horizonte para abandonos y no asignados, promediando sobre todos los elegibles de la jornada.",
    ]),
    ("5. Protocolo experimental", [
        "Se generaron 120 escenarios de semillas predefinidas: 80 de desarrollo y 40 de evaluación reservada. Las tres políticas recibieron escenarios idénticos. El manifiesto, parámetros y hashes están registrados en el repositorio. Se compararon diferencias pareadas por jornada con bootstrap pareado de 5.000 remuestras para intervalos de 95%. La hipótesis de magnitud de 10% se fijó antes de evaluación.",
        "Once escenarios deterministas de límites cubrieron prioridad, compatibilidad, cola vacía, abandono, liberación, horizonte y fallback. Cada condición se ejecutó con las tres políticas: 33 corridas.",
    ]),
]
for heading, paragraphs in sections:
    story.append(para(heading, "SectionX"))
    story.extend(para(p) for p in paragraphs)
    if heading == "3. PEAS y entorno":
        story.append(DecisionCycle())

story.append(para("6. Resultados", "SectionX"))
story.append(para("Tabla 1. Promedios por política y partición", "SmallX"))
table_data = [[para(x, "TinyX") for x in ["Conjunto / política", "Espera", "Aband. %", "P1", "Asig."]]]
for values in [
    ["Desarrollo / fija", "85,35", "37,15", "24,92", "11,31"],
    ["Desarrollo / reactiva", "85,35", "37,15", "24,92", "11,31"],
    ["Desarrollo / PriorIA", "83,01", "35,83", "25,32", "11,55"],
    ["Evaluación / fija", "82,69", "35,42", "27,94", "11,63"],
    ["Evaluación / reactiva", "82,69", "35,42", "27,94", "11,63"],
    ["Evaluación / PriorIA", "80,61", "34,58", "29,10", "11,78"],
]:
    table_data.append([para(x, "TinyX") for x in values])
t = Table(table_data, colWidths=[1.25*inch, .72*inch, .55*inch, .55*inch, .38*inch], repeatRows=1)
t.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#E8EEF3")), ("GRID", (0,0), (-1,-1), .35, colors.HexColor("#99A6B2")), ("VALIGN", (0,0), (-1,-1), "MIDDLE"), ("LEFTPADDING", (0,0), (-1,-1), 3), ("RIGHTPADDING", (0,0), (-1,-1), 3), ("TOPPADDING", (0,0), (-1,-1), 3), ("BOTTOMPADDING", (0,0), (-1,-1), 3)]))
story.append(t)
story.extend([
    para("En evaluación, PriorIA obtuvo 80,61 minutos de espera frente a 82,69 con el pipeline fijo. La diferencia pareada fue -2,083 minutos (IC 95% [-3,925; -0,343]), equivalente a -2,52%. El intervalo excluye cero, pero el efecto queda por debajo del objetivo preespecificado de 10%; por ello la hipótesis de magnitud no queda respaldada.", "BodyX"),
    para("El cambio de abandono fue -0,833 pp (IC 95% [-2,083; +0,417]); incluye cero, así que no se afirma una disminución concluyente. El límite superior quedó bajo el margen de +1 pp. En P1, PriorIA presentó +1,156 min (IC 95% [-1,643; +4,403]), un posible costo que merece atención. Ninguna diferencia por prioridad tuvo intervalo que excluyera cero.", "BodyX"),
    para("Pipeline fijo y reactiva produjeron exactamente las mismas asignaciones y resultados en ambas particiones. Esta equivalencia limita la comparación de agencia. En jornadas principales hubo cero intentos inválidos y cero asignaciones incompatibles ejecutadas. Las 33 corridas de límites pasaron. La decisión de PriorIA promedió 0,050 ms; el promedio del p95 por jornada fue 0,128 ms y el máximo individual 0,546 ms. No hubo timeout en evaluación.", "BodyX"),
    para("7. Análisis de fallas y limitaciones", "SectionX"),
    para("La mejora promedio fue pequeña y varió entre jornadas. El anexo muestra T111, con la mayor reducción observada, T114, con el mayor aumento, y S11, diseñado para forzar el timeout. Son casos ilustrativos, no representativos. Las llegadas, prioridades, capacidades y duraciones son sintéticas; hay tres boxes abstractos y 18 pacientes por jornada. No se modelan personal, traslados, severidad cambiante, equidad, protocolos clínicos ni juicio profesional. No hubo calibración ni validación hospitalaria. Las pruebas de límites cubren condiciones diseñadas, no todo el espacio de secuencias. No se infieren efectos clínicos ni generalización a una unidad real.", "BodyX"),
    para("8. Conclusiones y trabajo futuro", "SectionX"),
    para("En 40 escenarios reservados, PriorIA redujo en 2,08 minutos (2,52%) la espera frente al pipeline fijo; el IC excluyó cero, pero no se alcanzó el objetivo de 10%. El intervalo de abandono quedó bajo el margen superior de +1 pp y no hubo incompatibilidades ejecutadas. Deben mantenerse explícitos el posible costo en P1 y la equivalencia de las baselines fija y reactiva. El siguiente paso es investigar diferencias por prioridad, ampliar escenarios independientes, enriquecer la dinámica y validar supuestos con expertos antes de cualquier uso fuera del simulador.", "BodyX"),
    para("Referencias", "SectionX"),
    para("[1] S. Russell y P. Norvig, Artificial Intelligence: A Modern Approach, 4.a ed. Pearson, 2021.", "SmallX"),
    para("[2] J. E. Hurwitz et al., “A flexible simulation platform to quantify and manage emergency department crowding,” BMC Medical Informatics and Decision Making, vol. 14, no. 50, 2014, doi: 10.1186/1472-6947-14-50.", "SmallX"),
    para("[3] M. van de Vrugt y R. J. Boucherie, “Assigning treatment rooms at the Emergency Department,” Operations Research for Health Care, vol. 8, pp. 62-70, 2016, doi: 10.1016/j.orhc.2015.06.004.", "SmallX"),
    NextPageTemplate("Appendix"), PageBreak(),
])

story.append(para("Anexo A. Declaración de uso de IA generativa", "SectionX"))
story.append(para("Se utilizó ChatGPT/Codex (modelo GPT-6) como apoyo para discutir el diseño del agente, elaborar y revisar código del simulador y del análisis, organizar resultados y preparar este borrador. La decisión de usar prioridad estricta y la interpretación final corresponden al autor. Jonathan Moya debe revisar, corregir y aprobar cada afirmación, código y referencia antes de entregar; ajustar esta declaración a la herramienta/versión efectivamente usadas y a las reglas del curso.", "BodyX"))
story.append(para("Anexo B. Trazas completas comentadas", "SectionX"))
story.append(para("Las tablas enumeran todas las decisiones registradas para PriorIA en cada episodio. Cada renglón resume la cola elegible, boxes libres, pares factibles, acción, justificación, eventos y latencia; la deliberación registrada incluye plan/score o marca de fallback. El JSONL fuente completo conserva la observación, estado interno, deliberación y transición serializados para auditoría.", "BodyX"))


def trace_rows(path, eid, id_field="episode_id"):
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            d = json.loads(line)
            if d.get(id_field) == eid and d.get("policy") == "prioria_beam" and d.get("observation") is not None and d.get("decision") is not None:
                rows.append(d)
    return sorted(rows, key=lambda d: (d.get("time", 0), d.get("decision", {}).get("kind", "")))


def event_log(path, eid):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            d = json.loads(line)
            if d.get("episode_id") == eid and d.get("event_log"):
                return d["event_log"]
    return []


def mk_cell(s):
    return para(s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), "TinyX")


specs = [
    ("T111", "Éxito ilustrativo: mayor mejora observada. Espera primaria fija 90,056 min; PriorIA 71,889 min; diferencia -18,167 min.", ROOT/"outputs/experiment/evaluation/traces.jsonl", "episode_id"),
    ("T114", "Falla ilustrativa: mayor aumento observado. Espera primaria fija 68,611 min; PriorIA 82,833 min; diferencia +14,222 min.", ROOT/"outputs/experiment/evaluation/traces.jsonl", "episode_id"),
    ("S11_search_timeout_fallback", "Límite de seguridad: escenario construido con timeout de búsqueda igual a cero; el agente debe escoger fallback reactivo compatible.", ROOT/"outputs/safety/safety_traces.jsonl", "safety_scenario_id"),
]
for eid, comment, path, id_field in specs:
    story.append(para(f"{eid}: comentario de la traza", "SubsectionX"))
    story.append(para(comment, "SmallX"))
    records = trace_rows(path, eid, id_field)
    if not records:
        raise RuntimeError(f"Falta la traza {eid}")
    data = [[mk_cell(x) for x in ["t", "Cola elegible (prioridad/espera/requisito)", "Boxes libres", "Pares factibles", "Acción y deliberación", "Justificación / evento"]]]
    for r in records:
        obs = r.get("observation", {})
        queue = obs.get("waiting", [])
        qtext = "; ".join(f"{p['patient_id']} (P{p['priority']}, {p['wait_minutes']}m, {','.join(p.get('required_capabilities', []))})" for p in queue) or "vacía"
        free = ", ".join(b["box_id"] for b in obs.get("boxes", []) if not b.get("busy")) or "ninguno"
        feasible = ", ".join(f"{p['patient_id']}/{p['box_id']}" for p in r.get("feasible_pairs", [])) or "ninguno"
        d = r.get("decision", {})
        act = d.get("kind", "?")
        if d.get("kind") == "assign":
            act += f" {d.get('patient_id')} -> {d.get('box_id')}"
        deliberation = d.get("deliberation", {})
        if deliberation.get("timeout_fallback"):
            act += " (fallback timeout)"
        elif deliberation.get("selected_plan"):
            chosen = deliberation["selected_plan"][0]
            act += f"; plan siguiente={chosen.get('kind')} {chosen.get('patient_id','')} {chosen.get('box_id','')}; score={deliberation.get('selected_score')}"
        elif deliberation.get("selected_score") is not None:
            act += f"; score={deliberation.get('selected_score')}"
        rationale = d.get("rationale", "")
        events = ", ".join(r.get("events", [])) or "sin transición"
        lat = r.get("decision_latency_ms")
        if isinstance(lat, (int, float)):
            events += f"; {lat:.3f} ms"
        data.append([mk_cell(str(r.get("time", 0))), mk_cell(qtext), mk_cell(free), mk_cell(feasible), mk_cell(act), mk_cell(rationale + "; " + events)])
    tab = Table(data, colWidths=[0.30*inch, 1.50*inch, .55*inch, 1.35*inch, 1.40*inch, 2.0*inch], repeatRows=1, splitByRow=1)
    tab.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#DCE7EF")),("GRID",(0,0),(-1,-1),.3,colors.HexColor("#9AA7B2")),("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),2),("RIGHTPADDING",(0,0),(-1,-1),2),("TOPPADDING",(0,0),(-1,-1),2),("BOTTOMPADDING",(0,0),(-1,-1),2)]))
    story.append(tab)
    events = event_log(path, eid)
    if events:
        story.append(para("Cronología completa de eventos del entorno", "SmallX"))
        event_data = [[mk_cell(x) for x in ["t", "Evento", "Entidad / transición"]]]
        for event in events:
            entity = "; ".join(f"{k}={event[k]}" for k in ("patient_id", "box_id", "to") if k in event)
            event_data.append([mk_cell(str(event.get("time", 0))), mk_cell(event.get("event", "")), mk_cell(entity)])
        event_table = Table(event_data, colWidths=[.4*inch, 1.6*inch, 5.1*inch], repeatRows=1, splitByRow=1)
        event_table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#E8EEF3")),("GRID",(0,0),(-1,-1),.3,colors.HexColor("#9AA7B2")),("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),2),("RIGHTPADDING",(0,0),(-1,-1),2),("TOPPADDING",(0,0),(-1,-1),2),("BOTTOMPADDING",(0,0),(-1,-1),2)]))
        story.append(event_table)
    story.append(Spacer(1, 8))

story.append(para("Anexo C. Declaración de originalidad", "SectionX"))
story.append(para("Declaro que revisé el trabajo y que la versión final representa fielmente mis decisiones, análisis y aportes. Declaro también el uso de herramientas de inteligencia artificial en el alcance indicado en el anexo A.", "BodyX"))
story.append(Spacer(1, 24))
story.append(para("Nombre: Jonathan Moya C. &nbsp;&nbsp;&nbsp;&nbsp;&nbsp; Firma: __________________________________", "BodyX"))
story.append(Spacer(1, 12))
story.append(para("Fecha: __________________________", "BodyX"))

doc.build(story)
print(OUTPUT)
