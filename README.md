# PriorIA V4.6 · Simulador e indicadores

Paquete ejecutable con FastAPI, Docker Compose, Canvas y el motor de simulación de
V2. Incluye el desglose de categorización por ruta, gráficos de indicadores y demora de 2–4 horas para la asignación de cama. También conserva la regla corregida para pacientes C1, prioridad de reevaluaciones C4/C5,
asignación rotativa de cabinas y una pestaña de indicadores para revisar utilización,
esperas, LOS y estados de pacientes. El plano no muestra grilla ni una batería visual de rutas.

## Ejecutar en Windows PowerShell

1. Descomprime este ZIP en la carpeta que prefieras.
2. Entra en `PriorIA_V4_Indicadores\v4` dentro de la carpeta de destino.
3. Ejecuta:

```powershell
$dest = "$env:USERPROFILE\PriorIA_V4_Indicadores"
Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\PriorIA_V4_Indicadores.zip" -DestinationPath $dest -Force
Set-Location "$dest\PriorIA_V4_Indicadores\v4"
docker compose up --build
```

4. Abre `http://localhost:8503`.
5. Ejecuta una simulación. La pestaña **Simulación** muestra la animación; la
   pestaña **Indicadores** muestra los resultados y permite descargar CSV/JSONL.
6. Para detener: `Ctrl+C` y luego `docker compose down`.

Las admisionistas permanecen estáticas en sus posiciones. El panel de PriorIA
muestra la utilización global y de cada cabina; esta puede ser baja porque la
validación dura 1–5 minutos y el cálculo usa toda la ventana de simulación.

Si el puerto 8503 está ocupado:

```powershell
$env:PRIORIA_V4_PORT = "8504"
docker compose up --build
```

Luego abre `http://localhost:8504`.
