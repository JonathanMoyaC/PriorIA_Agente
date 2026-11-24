# PriorIA — resumen de resultados para el informe

**Estado:** evaluación final ejecutada con el código y parámetros registrados en `outputs/experiment/evaluation/run_config.json`.

## Diseño

- 120 jornadas sintéticas pareadas: 80 de desarrollo y 40 de evaluación reservada.
- Mismas llegadas, pacientes, capacidades y semillas para `fixed_pipeline`, `reactive` y `prioria_beam`.
- PriorIA: búsqueda en haz (`B=20`, `H=5`), prioridad estricta entre pacientes con box compatible disponible y fallback reactivo con límite de 50 ms.
- Métrica primaria: espera restringida media hasta ocupar un box compatible; pacientes no asignados reciben el límite del horizonte (240 min).
- Diferencias e IC del 95 % calculados por bootstrap pareado de escenarios (5000 remuestras).

## Comparación principal

| Conjunto | Política | Espera restringida media (min) | Abandono | Espera P1 (min) | Asignados/jornada | Intentos inválidos | Incompatibles ejecutadas |
|---|---|---:|---:|---:|---:|---:|---:|
| Desarrollo (n=80) | Pipeline fijo | 85,35 | 37,15 % | 24,92 | 11,31 | 0 | 0 |
| Desarrollo (n=80) | Reactiva | 85,35 | 37,15 % | 24,92 | 11,31 | 0 | 0 |
| Desarrollo (n=80) | PriorIA | 83,01 | 35,83 % | 25,32 | 11,55 | 0 | 0 |
| Evaluación (n=40) | Pipeline fijo | 82,69 | 35,42 % | 27,94 | 11,63 | 0 | 0 |
| Evaluación (n=40) | Reactiva | 82,69 | 35,42 % | 27,94 | 11,63 | 0 | 0 |
| Evaluación (n=40) | PriorIA | 80,61 | 34,58 % | 29,10 | 11,78 | 0 | 0 |

## Diferencias pareadas en evaluación

| Comparación | Diferencia PriorIA − fija | IC 95 % | Lectura |
|---|---:|---:|---|
| Espera restringida media | −2,083 min | [−3,925; −0,343] | Reducción media de 2,52 %; el intervalo excluye cero. |
| Tasa de abandono | −0,833 pp | [−2,083; +0,417] pp | Estimación favorable, pero el intervalo incluye cero. Su límite superior queda bajo el margen de +1 pp. |

El objetivo preespecificado de reducir la espera al menos 10 % **no se alcanza** en evaluación: la reducción observada es 2,52 %. La evidencia sí muestra una diferencia media pequeña, pero medible, frente al pipeline fijo. La hipótesis de magnitud no queda respaldada.

## Espera por prioridad en evaluación

Las diferencias PriorIA − pipeline fijo fueron: P1 **+1,156 min** (IC 95 % [−1,643; +4,403]); P2 **−3,438 min** ([−7,691; +0,276]); P3 **−4,840 min** ([−10,652; +0,025]); P4 **−0,413 min** ([−5,028; +4,155]); P5 **−2,571 min** ([−5,988; +0,432]). Ningún intervalo excluye cero. Por tanto, la evaluación no permite afirmar una diferencia por categoría; P1 requiere discusión como posible costo de la estrategia.

## Agencia, seguridad y ejecución

- La política reactiva produjo las mismas asignaciones y resultados agregados que el pipeline fijo en ambos conjuntos; con esta configuración funciona como una baseline agéntica simple, aunque no se distingue empíricamente de la fija.
- PriorIA redujo la espera media en ambos conjuntos y reaccionó a eventos de llegada y liberación de boxes mediante replanificación. En evaluación, el bootstrap pareado de la métrica primaria excluye cero, lo que respalda una contribución medible del ciclo deliberativo frente a la política fija.
- Se ejecutaron **11 escenarios de límites × 3 políticas = 33 corridas**. Las 33 pasaron, incluidos prioridad estricta, compatibilidad, liberación, umbral de abandono, cierre de horizonte y timeout con fallback trazado.
- No hubo intentos de asignación inválidos ni incompatibilidades ejecutadas en las jornadas principales.
- Tiempo de decisión de PriorIA en evaluación: media 0,050 ms y p95 medio 0,128 ms; el máximo observado fue 0,546 ms. No se activó el fallback por timeout.

## Interpretación y límites

El resultado respalda una mejora pequeña de espera media con prioridad estricta, no la mejora de 10 % planteada en la hipótesis. El abandono no aumentó y no hubo fallas de seguridad observadas en los escenarios ejecutados. La política reactiva no se separó de la fija, por lo que conviene describir esta limitación con claridad.

El modelo cubre solo la asignación posterior al triage, con pacientes y capacidades sintéticas, tres boxes abstractos, 18 pacientes por jornada y un horizonte de 240 minutos. No representa una unidad hospitalaria validada ni permite inferir efectos clínicos reales. Las 11 pruebas de límites cubren fallas diseñadas, pero no agotan el espacio de escenarios.

## Archivos fuente

- Métricas por escenario y política: `outputs/experiment/{development,evaluation}/scenario_metrics.csv`.
- Resúmenes e intervalos: `outputs/experiment/{development,evaluation}/summary.json`.
- Configuración y hashes de código: `outputs/experiment/{development,evaluation}/run_config.json`.
- Escenarios, verdad sintética y trazas: directorios de cada partición.
- Resultados y trazas de seguridad: `outputs/safety/`.
