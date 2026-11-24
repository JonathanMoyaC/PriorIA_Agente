# PriorIA V2 — flujo sintético de pacientes

V2 ejecuta una jornada sintética completa desde el ingreso hasta el alta,
hospitalización o abandono. Se mantiene separada del benchmark V1 de asignación
a boxes.

## Comportamiento implementado

- Cohorte configurable de 100 pacientes, grupos etarios 18–39, 40–64 y 65+,
  categorías C1–C5 y grupos de causas. Los valores del fixture son sintéticos.
- Llegadas a lo largo de 24 horas con máximo esperado a las 16:00; cuotas diarias
  80% por medios propios, 5% ambulancia y 15% policía.
- Tras admisión, los pacientes esperan en la sala general para PriorIA o triage
  humano y ocupan una silla FIFO si hay disponibilidad. Al ser llamados a
  categorización, liberan la silla.
- La adopción de PriorIA se calcula como tasa base más un ajuste por edad, acotado
  entre 0 y 1. Solo es elegible la llegada por medios propios y C1 pasa a triage
  humano. La probabilidad base y los ajustes son configurables.
- Después de categorizar, C1 va a reanimación, C2 a la sala C2 y C3–C5 a la sala
  general. Cada sala tiene cola FIFO independiente, con 24 y 36 sillas. Si no hay
  silla, el paciente espera de pie.
- Ser llamado a consulta o reevaluación libera la silla. C4/C5 sin tratamiento
  se dan de alta por la puerta principal.
- Si se indica tratamiento, el paciente vuelve a la sala C2 y espera una
  enfermera. El tratamiento dura entre 30 y 120 minutos por sorteo uniforme. La
  silla sigue ocupada durante el tratamiento; al terminar, el paciente espera
  allí la reevaluación médica.
- Una reevaluación favorable da el alta. Si falla, se indica otro tratamiento;
  al segundo fallo, el paciente se hospitaliza.
- La ecuación de abandono se evalúa cada minuto mientras el paciente espera. Usa
  espera acumulada, categoría, edad y postura. El tiempo de tratamiento no se
  suma como espera y no genera abandono mientras la enfermera está atendiendo.
- Configuración inicial de recursos: 2 puestos de admisión, 5 cabinas PriorIA,
  2 estaciones de triage, 3 enfermeras/os de triage, 12 médicos (4 por
  especialidad), 8 boxes con 8 enfermeras/os, 2 camas de reanimación, 36 sillas
  generales, 24 sillas C2 y 1 enfermera/o para tratamientos en C2. Lo último es
  un supuesto configurable a confirmar.

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
tratamiento, reevaluación, hospitalización y número de enfermeras disponibles en
C2 aún requieren datos o decisiones operacionales. El simulador no categoriza ni
recomienda tratamientos clínicos.
