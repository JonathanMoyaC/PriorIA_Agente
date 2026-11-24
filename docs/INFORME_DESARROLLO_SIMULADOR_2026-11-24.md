# Informe de desarrollo del simulador PriorIA

**Fecha:** 24 de noviembre de 2026  
**Estado:** Diseño conceptual v0.1  
**Fecha de entrega del proyecto:** 30 de noviembre de 2026

## 1. Propósito y alcance

Se documenta el diseño de una versión ampliada del simulador para representar el recorrido de pacientes desde su llegada a urgencias hasta su alta u hospitalización. El simulador permitirá explorar la interacción entre categorización, asignación de profesionales, uso de recursos, observación y desenlaces.

Este alcance es una extensión exploratoria del prototipo vigente en el repositorio. El experimento de asignación ya ejecutado —que comienza con pacientes categorizados y compara políticas de asignación a boxes— se mantiene congelado. Los cambios del simulador integral deben desarrollarse como una versión separada y no modificar sus parámetros ni sus resultados reservados.

El modelo es sintético y académico. No constituye un protocolo clínico ni entrega recomendaciones para la atención de pacientes reales.

## 2. Cohorte y perfil inicial

Se fija como escenario base una demanda de **100 pacientes nuevos por día**. La distribución diaria por categorías C1–C5 se configurará con las proporciones DEIS previamente identificadas. Antes de incorporarlas al código, se verificarán nuevamente la tabla de origen, año, territorio, denominador y porcentajes exactos. El redondeo de los cupos diarios deberá conservar un total de 100 pacientes.

Los perfiles de edad serán:

| Perfil | Edad |
|---|---:|
| Adulto joven | 18–39 años |
| Adulto medio | 40–64 años |
| Adulto mayor | 65 años o más |

El corte de 65 años coincide con el grupo de adulto mayor utilizado por DEIS. La separación entre adulto joven y adulto medio es una subdivisión sintética del tramo adulto; las tablas públicas DEIS no entregan necesariamente ese detalle.

Cada paciente sintético podrá contener: edad y perfil etario, medio de llegada, motivo expresado, síntomas, signos vitales, nivel de gravedad de referencia, categoría de triage asignada, especialidades compatibles y ruta de atención. El escenario clínico subyacente se mantendrá separado de los datos observados por el agente, para evitar asignar directamente la categoría como respuesta del triage.

Los medios de llegada propuestos como distribución global son **80 % por sus propios medios, 5 % en ambulancia y 15 % con llegada asociada a policía**. Las tendencias por edad —uso de PriorIA, forma de llegada y abandono— son hipótesis parametrizables, no porcentajes atribuidos al DEIS. Las probabilidades condicionales por edad deberán conciliarse con las proporciones globales configuradas.

## 3. Abandono

El abandono se evaluará mientras el paciente permanezca en espera. Se calculará un índice de riesgo creciente que considere:

- tiempo de espera acumulado;
- categoría de gravedad, con mayor riesgo relativo en categorías menos graves;
- incomodidad asociada a la postura o al lugar de espera;
- perfil etario del paciente.

El paciente abandona cuando el índice supera un umbral configurable. Para representar diferencias individuales, se podrá variar el umbral por paciente alrededor de un valor central. Los coeficientes y umbrales quedan pendientes de calibración y análisis de sensibilidad.

## 4. Duraciones de atención

Como valores iniciales de tiempo de atención clínica activa se proponen:

| Categoría | Media inicial |
|---|---:|
| C1 | 60 min |
| C2 | 45 min |
| C3 | 30 min |
| C4 | 20 min |
| C5 | 15 min |

La tendencia esperada es que la atención clínica activa sea más larga para las categorías de mayor gravedad. Como distribución estocástica se propone Gamma, que mantiene valores positivos y permite variación entre casos.

Se propusieron desviaciones estándar iniciales de 10, 9, 8, 6 y 5 minutos para C1–C5, respectivamente. Esta parametrización queda como propuesta por confirmar. La duración de atención profesional se registrará separada de los tiempos de espera por exámenes, procedimientos, observación y permanencia total del paciente.

## 5. Profesionales y compatibilidad

Los perfiles profesionales definidos para el simulador son **traumatología, medicina interna, cirugía y enfermería**. La especialidad compatible se determinará a partir de la familia del motivo de consulta. La categoría de triage establecerá la prioridad y podrá activar una ruta inmediata para los casos críticos; no determinará por sí sola la especialidad.

El paciente se incorporará a una cola de atención compatible. El simulador asignará al profesional disponible entre los habilitados para esa ruta. Las capacidades de personal y estaciones definidas en versiones visuales previas deberán revisarse antes de trasladarlas al modelo integral.

## 6. Matriz inicial de escenarios

La siguiente matriz constituye el catálogo v0.1. Los exámenes y procedimientos son opciones del escenario, no intervenciones obligatorias para todos los pacientes de una familia.

| Familia de consulta | Motivos de ejemplo | Especialidad compatible | Exámenes o recursos posibles | Procedimientos y observación posibles |
|---|---|---|---|---|
| Dolor torácico | Dolor o presión en el pecho | Medicina interna | ECG, monitor, laboratorio; imagen según escenario | Tratamiento inicial y observación |
| Dificultad respiratoria | Falta de aire, tos o fiebre respiratoria | Medicina interna | Monitor, oxígeno, rayos X, laboratorio | Tratamiento y observación |
| Dolor abdominal | Dolor abdominal, náuseas | Medicina interna; cirugía si la ruta lo requiere | Laboratorio, ecografía o scanner según escenario | Procedimiento eventual y observación |
| Traumatismo o caída | Caída, golpe, dolor de extremidad | Traumatología | Rayos X; scanner en escenarios seleccionados | Inmovilización o sala de yeso; observación si corresponde |
| Herida o sangrado | Corte o sangrado persistente | Cirugía o traumatología según el escenario | Evaluación; rayos X si el caso lo requiere | Sala de procedimientos y posible observación |
| Síntomas neurológicos, mareo o síncope | Desmayo, debilidad, cefalea intensa | Medicina interna | Glicemia, ECG o scanner según escenario | Tratamiento y observación |
| Fiebre o posible infección | Fiebre o decaimiento | Medicina interna | Laboratorio e imágenes según síntomas asociados | Tratamiento y observación |
| Síntomas digestivos o urinarios | Vómitos, diarrea, molestias urinarias | Medicina interna; cirugía si la ruta lo requiere | Laboratorio, análisis de orina o ecografía según escenario | Tratamiento y observación |

Recursos físicos candidatos para parametrizar: boxes, reanimación, rayos X, scanner, laboratorio/ECG, sala de yeso o procedimientos y plazas de observación. Pabellón se incorporará si el catálogo incluye escenarios de cirugía de urgencia; de otro modo, la hospitalización será un desenlace del simulador sin modelar la cirugía completa.

## 7. Observación y desenlaces

Los pacientes que requieran observación ocuparán una plaza durante un tiempo estocástico específico de su ruta; los profesionales podrán liberarse si ya no participan activamente en el cuidado.

La secuencia propuesta es:

1. Evaluación inicial y, cuando corresponda, exámenes o procedimiento inicial.
2. Observación y sorteo de evolución favorable o desfavorable.
3. Evolución favorable: alta, salvo que la ruta parametrizada indique otro desenlace.
4. Evolución desfavorable: segundo procedimiento y nueva observación.
5. Segunda evolución desfavorable: indicación de hospitalización.

Las probabilidades de evolución favorable, los tiempos de observación y los segundos procedimientos permanecen pendientes. El escenario inicial considera **20 % de hospitalización y 80 % de alta como proporción objetivo**. Se deberá definir si esa proporción se aplica exactamente a cada jornada de 100 pacientes o como promedio esperado entre jornadas. Luego podrá contrastarse con estadísticas DEIS comparables; no se presenta todavía como una tasa DEIS validada ni se asignará independientemente de la categoría y la ruta clínica.

## 8. Fuentes, límites y decisiones pendientes

DEIS publica series de atenciones de urgencia y registros REM agrupados; las variables y secciones pueden cambiar según el año. Las publicaciones permiten calibrar algunas distribuciones agregadas, pero no reemplazan datos individuales de signos vitales, narrativas de motivos, tiempos por etapa ni probabilidades de éxito de procedimientos.

Fuentes oficiales de referencia:

- [DEIS: preguntas frecuentes sobre datos abiertos](https://deis.minsal.cl/faqs/)
- [DEIS/MINSAL: Manual de Registro del Sistema de Atención Diaria de Urgencia (SADU)](https://repositoriodeis.minsal.cl/ContenidoSitioWeb2020/Atenciones%20Urgencia/Manual%20Registro%20Atenciones%20Diarias%20de%20Urgencia%2017122025.pdf)
- [DEIS/MINSAL: Manual REM 2025–2026, Serie A](https://repositoriodeis.minsal.cl/ContenidoSitioWeb2020/REM/2025/SERIE/Manual_Series_REM_2025_2026_SERIE_A_BS_BM_D_V1.3.pdf)

Pendientes prioritarios antes de implementar el flujo integral:

1. Revalidar los porcentajes C1–C5 del DEIS y especificar su año, ámbito geográfico y denominador.
2. Cerrar las frases y variantes de motivos de consulta y su distribución condicionada por categoría.
3. Especificar qué signos vitales se generan por escenario y con qué distribuciones sintéticas.
4. Fijar compatibilidad entre motivo, especialidad y recursos.
5. Definir capacidades, tiempos y probabilidades de exámenes, procedimientos y observación.
6. Acordar cómo se manejarán las evoluciones desfavorables, las rutas críticas y la hospitalización directa.
7. Calibrar la tasa global de hospitalización y documentar cada parámetro como DEIS, otra fuente empírica o supuesto del escenario.

