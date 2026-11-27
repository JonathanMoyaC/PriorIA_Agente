# PriorIA V4 · prueba de rutas y grilla

Paquete ejecutable basado en la carpeta `v4` del repositorio
[`JonathanMoyaC/PriorIA_Agente`](https://github.com/JonathanMoyaC/PriorIA_Agente),
commit `1766cc07ecb9db96b5b89233cbae4c657c39f56f` (V4.1). Conserva FastAPI,
Docker Compose y la visualización Canvas; incorpora una prueba visual de 35
rutas, la grilla de coordenadas y una regla de prioridad corregida para C1.

Los pacientes sintéticos C1 ingresan directamente a la cola de reanimación,
no pasan por las salas de espera ni abandonan el servicio, reciben atención en
un box de reanimación y luego son hospitalizados. La categoría está definida
en el perfil sintético antes de la llegada.

## Ejecutar en Windows PowerShell

1. Descomprime este ZIP en la carpeta local que prefieras.
2. En PowerShell, entra a la carpeta `v4` dentro de la carpeta descomprimida.
3. Levanta la aplicación:

```powershell
Set-Location "C:\ruta\local\PriorIA_V4_Rutas\v4"
docker compose up --build
```

4. Abre `http://localhost:8503` en el navegador.
5. Pulsa **Probar todas las rutas** para iniciar el recorrido de `P-TEST`.
6. Para cerrar, presiona `Ctrl+C` en PowerShell y ejecuta:

```powershell
docker compose down
```

Si el puerto `8503` está ocupado, en PowerShell define otro antes de iniciar:

```powershell
$env:PRIORIA_V4_PORT = "8504"
docker compose up --build
```

En ese caso, abre `http://localhost:8504`.
