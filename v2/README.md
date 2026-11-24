# PriorIA V2 — simulación sintética de flujo

V2 simula una jornada de urgencias separada del benchmark de asignación V1. La
unidad genera 100 perfiles, procesa el ingreso a PriorIA o triage humano, asigna
pacientes a las salas de espera, atiende consultas y modela etapas opcionales de
procedimiento y observación.

## Reglas implementadas

- Rangos: adulto joven 18–39, adulto medio 40–64 y adulto mayor desde 65 años.
- Llegadas distribuidas durante 24 horas mediante una curva configurable cuyo
  máximo esperado está a las 16:00. La hora máxima observada en una muestra de
  100 pacientes puede variar por azar.
- Cuotas diarias configurables de llegada propia, ambulancia y policía; por
  defecto se conserva 80/5/15. Las preferencias por edad ajustan la asignación
  individual sin cambiar esas cuotas.
- Uso sintético de PriorIA solo para llegada por medios propios y categorías
  distintas de C1; la probabilidad depende del grupo etario.
- Recursos del plano: 5 cabinas PriorIA, 8 boxes, 2 camas de reanimación, 12
  médicos (4 por especialidad), 8 enfermeras/os de box, 3 de triage, 36 sillas
  generales y 24 sillas en sala C2.
- C2/C3 esperan en la sala C2 y C4/C5 en la sala general. Cada sala asigna sillas
  por FIFO; quienes exceden su capacidad esperan de pie. C1 se dirige a
  reanimación.
- Quien espera un procedimiento o una observación vuelve a la cola FIFO de C2.
  Las sillas de ambas salas se administran por separado.
- La probabilidad de abandono aumenta con la espera acumulada, la menor gravedad
  y la condición de estar de pie. Se evalúa cada minuto frente a un umbral
  individual configurable. El tiempo de espera acumulado se conserva al volver
  desde una consulta o una etapa clínica.
- La duración de consulta depende de C1–C5; causa y especialidad determinan la
  ruta profesional. Una evolución desfavorable puede generar un segundo
  procedimiento; si ese tampoco es favorable, el paciente se hospitaliza.

## Ejecutar

Desde la raíz del repositorio:

```bash
python v2/simulate_day.py --config v2/configs/smoke_test.json --output-dir outputs/v2_smoke
python -m unittest discover -s v2/tests -v
```

La corrida produce `summary.json`, `trace.jsonl` y `patients.csv` con perfiles,
motivos de consulta, signos vitales sintéticos, tiempos, eventos y desenlaces.

## Estado de calibración

`configs/smoke_test.json` es un fixture de prueba mecánica. Sus distribuciones de
edad, categorías y causas, signos vitales, uso de PriorIA, duraciones clínicas y
desenlaces son supuestos sintéticos; **no son datos observados ni una calibración
DEIS**. Por eso el fixture declara `deis_values_verified: false`. El generador
solo debe usarse para una corrida calibrada cuando se ingresen distribuciones
DEIS verificadas y se actualicen las fuentes, el año, territorio y denominador
en la metadata.

La estructura incluye recursos configurables de procedimientos y observación.
Los valores del fixture (2 puestos de procedimiento y 4 camas de observación)
son parámetros mecánicos provisionales porque la capacidad de esos espacios aún
no se ha fijado. Las probabilidades de evolución y hospitalización también
requieren fuentes o calibración local. El simulador no categoriza ni recomienda
tratamientos clínicos.

La dotación de personal se mantiene constante durante las 24 horas en este
prototipo; no se han modelado turnos, colaciones ni ausentismo. Esta simplificación
debe reemplazarse por calendarios de turno antes de interpretar tiempos o carga
de trabajo como una estimación operacional.
