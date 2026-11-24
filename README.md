# PriorIA — simulador de asignación a boxes

Prototipo académico y sintético. El simulador comienza cuando los pacientes ya
fueron categorizados y quedaron elegibles para asignación. Compara tres
políticas sobre el mismo flujo de pacientes y los mismos recursos:

1. `fixed_pipeline`: prioridad, FIFO y primer box compatible libre.
2. `reactive`: prioridad, espera acumulada y disponibilidad esperada.
3. `prioria_beam`: búsqueda en haz de horizonte limitado con replanificación y
   prioridad estricta entre pacientes con box compatible disponible.

No representa un hospital real ni realiza triage, diagnóstico o recomendaciones
clínicas. Las capacidades de los boxes y los tiempos de atención son etiquetas y
distribuciones sintéticas editables.

## Preparación

Requiere Python 3.12. Desde esta carpeta:

```bash
python -m venv .venv
```

En Windows:

```powershell
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

En macOS o Linux:

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Ejecutar el episodio demostrativo

```bash
python run_demo.py
```

Escribe `outputs/demo_metrics.csv` y `outputs/demo_traces.jsonl`.

## Ejecutar el experimento pareado

El manifiesto `configs/experiment_manifest.json` fija 120 semillas: 80 para
desarrollo y 40 para evaluación reservada. Cada escenario se ejecuta con las
tres políticas y las mismas condiciones. El comando predeterminado ejecuta solo
desarrollo:

```bash
python run_experiment.py --split development
```

Cuando la versión, los parámetros y el protocolo estén congelados, ejecutar una
sola vez la evaluación reservada:

```bash
python run_experiment.py --split evaluation --confirm-evaluation
```

Para reconstruir ambos conjuntos desde cero, sin cambiar nada entre ejecuciones:

```bash
python run_experiment.py --split all --confirm-evaluation
```

Los resultados quedan separados por partición en `outputs/experiment/`:

- `scenario_metrics.csv`: métricas por jornada y política.
- `summary.json`: medias, desviaciones y diferencias pareadas con IC bootstrap.
- `traces.jsonl`: observación, estado interno, decisión, justificación y eventos.
- `scenario_truth.jsonl`: verdad sintética del escenario, separada de las trazas
  visibles para las políticas.
- `run_config.json`: parámetros usados en la corrida.
- `main_table.csv` y `paired_comparisons.json`: tabla principal e inferencia de
  la evaluación reservada cuando se ejecuta `--split all`.

El desenlace principal es la espera restringida hasta ocupar un box compatible;
los pacientes no asignados reciben el límite del horizonte. Se informa además
abandono y se comprueba que no haya asignaciones incompatibles ejecutadas.
El bootstrap es por escenario pareado. La partición `evaluation` no debe
utilizarse para elegir reglas o parámetros.

## API local

```bash
uvicorn main:app --reload
```

- `GET /health`
- `GET /defaults`
- `POST /simulate`

La API permite explorar configuraciones sintéticas; las corridas confirmatorias
deben usar el manifiesto versionado y el ejecutor experimental.

## Escenarios de límites de seguridad

Ejecuta los diez escenarios de borde, por separado de las jornadas estadísticas:

```bash
python run_safety_scenarios.py
```

Guarda los resultados y las trazas en `outputs/safety/`. Estos casos cubren
prioridad, compatibilidad, abandono, liberación de boxes, tiempos de espera y
cierre del horizonte.
