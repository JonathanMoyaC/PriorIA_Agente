# PriorIA V2 — flujo sintético de pacientes

V2 ejecuta una jornada sintética completa desde el ingreso hasta el alta,
hospitalización o abandono. Se mantiene separada del benchmark V1 de asignación
a boxes.

## Comportamiento implementado

- Cohorte configurable de 100 pacientes, grupos etarios 18–39, 40–64 y 65+,
  categorías C1–C5 y grupos de causas. Los valores del fixture son sintéticos.
- `daily_patients` fija el volumen base y `arrival.demand_multiplier` lo escala:
  con base 100, 0,8 genera 80 pacientes y 1,2 genera 120. El multiplicador
  cambia el volumen, no la forma temporal: conserva las mismas probabilidades
  relativas por hora y el máximo esperado a las 16:00. Los conteos por hora
  varían aleatoriamente entre corridas.
- Las cuotas de llegada se mantienen en 80% por medios propios, 5% ambulancia y
  15% policía. Todo paciente C1 se asigna a ambulancia; la configuración falla
  explícitamente si la cuota C1 supera las plazas de ambulancia disponibles.
- Tras admisión, los pacientes esperan en la sala general para PriorIA o triage
  humano y ocupan una silla FIFO si hay disponibilidad. Al ser llamados a
  categorización, liberan la silla.
- Las enfermeras/os de triage son un recurso parametrizable compartido por la
  validación de PriorIA (1–5 min) y la categorización humana (10–15 min). Los
  intervalos se sortean uniformemente y pueden cambiarse en la configuración.
- La adopción de PriorIA se calcula como tasa base más un ajuste por edad, acotado
  entre 0 y 1. Solo es elegible la llegada por medios propios y C1 pasa a triage
  humano. La probabilidad base y los ajustes son configurables.
- Después de categorizar, C1 va a reanimación, C2 a la sala C2 y C3–C5 a la sala
  general. Cada sala tiene cola FIFO independiente, con 24 y 36 sillas. Si no hay
  silla, el paciente espera de pie.
- Cada médico activo queda asociado de forma fija a un box asignado en orden. En
  consultas y reevaluaciones, el paciente se traslada al box de un médico libre
  de la especialidad requerida, registrada en su perfil inicial a partir del
  grupo de diagnóstico sintético. El médico permanece en su box durante la
  atención.
- Cuando un C1 ingresa a reanimación, se reserva el primer médico disponible,
  cualquiera sea su especialidad, junto con una enfermera. El médico deja
  temporalmente su box y vuelve a él al terminar la emergencia. El simulador
  exige un médico y una enfermera para iniciar esa atención.
- Ser llamado a consulta o reevaluación libera la silla. C4/C5 sin tratamiento
  se dan de alta por la puerta principal.
- Si se indica tratamiento, el paciente vuelve a la sala C2 y espera una
  enfermera de atención. La enfermera realiza el procedimiento asociado durante
  5–10 min y vuelve disponible a la estación; luego el tratamiento/observación
  dura 30–120 min por sorteo uniforme, sin retener a esa enfermera. El paciente
  conserva su silla durante ambas etapas y, al terminar, espera allí la
  reevaluación médica.
- Al ingresar un paciente C1 a reanimación se le asigna de inmediato una
  enfermera de atención disponible. Si todas están ocupadas, se usa una
  enfermera de triage como último recurso. La enfermera queda ocupada hasta que
  termina la atención C1; si también están ocupadas, el C1 espera esa asignación.
- Una reevaluación favorable da el alta. Si falla, se indica otro tratamiento;
  al segundo fallo, el paciente se hospitaliza.
- La ecuación de abandono se evalúa cada minuto mientras el paciente espera. Usa
  espera acumulada, categoría, edad y postura. El tiempo de tratamiento no se
  suma como espera y no genera abandono mientras la enfermera está atendiendo.
- Configuración inicial de recursos: 2 puestos de admisión, 5 cabinas PriorIA,
  2 estaciones de triage, 3 enfermeras/os de triage, 8 médicos activos (3
  internistas, 3 traumatólogos y 2 cirujanos), 8 boxes con 8 enfermeras/os,
  2 camas de reanimación, 36 sillas
  generales, 24 sillas C2. Las enfermeras/os de atención corresponden al pool
  configurable de 8 enfermeras/os asociado a los boxes; triage tiene 3 en el
  fixture. Estas dotaciones son supuestos configurables.

## Ejecutar

Desde la raíz del repositorio:

```bash
python v2/simulate_day.py --config v2/configs/smoke_test.json --output-dir outputs/v2_smoke
python -m unittest discover -s v2/tests -v
python v2/run_smoke_replications.py --replications 10
```

La simulación produce `summary.json`, `trace.jsonl` y `patients.csv`, con eventos,
tiempos, motivos de consulta, signos vitales sintéticos y desenlaces.

## Calibración y límites

`configs/smoke_test.json` valida el funcionamiento mecánico; sus distribuciones
de edad, categorías, causas, signos vitales, adopción, tratamientos y desenlaces
**no son datos DEIS ni prevalencias observadas**. El generador exige distribuciones
verificadas o una configuración declarada como fixture de prueba.

La dotación se mantiene constante durante las 24 horas. Las tasas de adopción,
tratamiento, reevaluación y hospitalización aún requieren datos o decisiones
operacionales. El simulador no categoriza ni recomienda tratamientos clínicos.
