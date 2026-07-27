# Predicción Fútbol

Aplicación Flutter con un backend FastAPI que obtiene próximos partidos desde
API-Football, ejecuta modelos predictivos y publica el resultado en Supabase.
La app consulta esos resultados por HTTP al backend con reintentos controlados.

## Dónde vive cada cosa

```text
Flutter (prediccion_futbol/)
  └─ URL pública del backend (sin secretos)
                 │
                 ▼
FastAPI (backend/)
  ├─ clave secreta de API-Football
  ├─ clave secreta de Supabase y token administrativo
  ├─ modelos backend/models/<liga>/model_bundle.joblib
  └─ perfiles backend/data/team_profiles/<liga>.json
                 │
                 ▼
Supabase
  ├─ leagues y fixtures
  ├─ predictions publicadas
  ├─ prediction_versions (historial)
  └─ prediction_evaluations y prediction_market_results (aciertos)
```

Supabase no ejecuta los modelos. La base de datos guarda datos operativos y
resultados; FastAPI calcula y publica la predicción. Los cinco modelos europeos
(`D1`, `E0`, `F1`, `I1` y `SP1`) viven en `backend/models/`. Las cinco
competiciones sudamericanas usan inicialmente un baseline Poisson/
Empirical-Bayes calculado con el historial almacenado. Ningún modelo se mueve a
Supabase.

Los pronósticos de mercado también se calculan en FastAPI, sin IA generativa:
líneas Más/Menos para goles, córners, tarjetas, remates y remates al arco. Cada
versión queda congelada antes del inicio y el ciclo postpartido consulta solo
los fixtures pronosticados, guarda sus estadísticas finales y liquida cada
selección como acertada, fallada, nula o pendiente.

## Probar la interfaz sin claves

```powershell
cd prediccion_futbol
flutter pub get
flutter run -d chrome --web-hostname localhost --web-port 5173 --dart-define-from-file=config/demo.json
```

El modo demo usa datos locales y permite recorrer Home → detalle de predicción.

## Configurar el sistema real

### 1. Secretos del backend

Desde `backend/`, copia `.env.example` como `.env` y completa **el archivo
`.env`**. `.env.example` debe conservar únicamente valores ficticios porque sí
puede versionarse:

```dotenv
API_FOOTBALL_KEY=tu_clave_del_proveedor
SUPABASE_URL=https://yrdtvgegicjvobaioxof.supabase.co
SUPABASE_SECRET_KEY=sb_secret_...
ADMIN_TOKEN=un_token_largo_y_aleatorio
```

`API_FOOTBALL_KEY`, `SUPABASE_SECRET_KEY` y `ADMIN_TOKEN` son secretos. Nunca
deben colocarse en Flutter, Git ni un archivo `--dart-define`, porque una app
compilada puede revelar esos valores.

Inicia la API:

```powershell
cd backend
.\run_backend.bat
```

La comprobación de disponibilidad queda en
`http://127.0.0.1:8000/health/ready`. Un `503` con `not_ready` indica exactamente
qué configuración o artefacto falta y no impide que el proceso arranque.

### 2. Configuración de Flutter

Los archivos preparados son:

- `prediccion_futbol/config/dev.web.json`: backend en `127.0.0.1`.
- `prediccion_futbol/config/dev.android.json`: backend en `10.0.2.2`, dirección
  especial del emulador Android.
- `prediccion_futbol/config/production.json`: backend HTTPS desplegado en Railway.
- `prediccion_futbol/config/production.example.json`: plantilla para HTTPS.

Solo contienen `BACKEND_URL`; Flutter ya no abre una conexión directa a
Supabase. `SUPABASE_SECRET_KEY` permanece exclusivamente en `backend/.env`.
El valor predeterminado apunta al backend HTTPS de Railway, por lo que el botón
**Run** también funciona en un teléfono físico. Para trabajar contra FastAPI
local desde el emulador Android se debe seleccionar explícitamente
`config/dev.android.json`.

Web:

```powershell
cd prediccion_futbol
flutter run -d chrome --web-hostname localhost --web-port 5173 --dart-define-from-file=config/dev.web.json
```

Emulador Android:

```powershell
cd prediccion_futbol
flutter run --dart-define-from-file=config/dev.android.json
```

Para conectar un teléfono físico a FastAPI local, crea una configuración local
con la IP LAN del equipo. `10.0.2.2` solo existe dentro del emulador Android.

APK de producción:

```powershell
cd prediccion_futbol
flutter build apk --release --dart-define-from-file=config/production.json
```

### 3. Sincronizar datos y calcular predicciones

Supabase ya contiene el esquema normalizado y una primera carga histórica de
Perú, Brasil, Argentina, Libertadores y Sudamericana. Para resolver nuevamente
el catálogo o continuar la descarga reanudable:

```powershell
python -m backend.scripts.resolve_competitions
python -m backend.scripts.sync_upcoming --days 30
python -m backend.scripts.sync_historical --from-season 2021 --to-season 2026
python -m backend.scripts.sync_fixture_details --resume
python -m backend.scripts.show_sync_progress
```

La guía completa, incluidos los límites del plan, datos opcionales, rutas HTTP
y preparación para entrenamiento, está en
[docs/API_FOOTBALL_IMPORT.md](docs/API_FOOTBALL_IMPORT.md).

Con FastAPI activo también se puede ejecutar el ciclo protegido de los cinco
modelos europeos existentes:

```powershell
$env:FOOTBALL_ADMIN_TOKEN = 'el_mismo_valor_de_ADMIN_TOKEN'
Invoke-RestMethod `
  -Method Post `
  -Uri 'http://127.0.0.1:8000/admin/jobs/sync-and-predict?horizon_days=7&max_matches=5' `
  -Headers @{ 'X-Admin-Token' = $env:FOOTBALL_ADMIN_TOKEN }
```

Para activar la sincronización diaria del backend:

```dotenv
ENABLE_SCHEDULER=true
SCHEDULER_RUN_ON_STARTUP=false
SCHEDULER_HORIZON_DAYS=7
SCHEDULER_PREDICTION_HORIZON_DAYS=14
POSTMATCH_LOOKBACK_DAYS=7
POSTMATCH_MAX_MATCHES=100
POSTMATCH_POLL_INTERVAL_MINUTES=30
RETENTION_ENABLED=true
RETENTION_DRY_RUN=false
RETENTION_RAW_PAYLOAD_DAYS=1825
RETENTION_API_LOG_DAYS=90
RETENTION_FIXTURE_BATCH_SIZE=500
RETENTION_API_LOG_BATCH_SIZE=5000
RETENTION_MAX_BATCHES=10
RETENTION_WEEKDAY=6
RETENTION_HOUR=3
RETENTION_MINUTE=30
SCHEDULER_DAILY_HOUR=0
SCHEDULER_DAILY_MINUTE=5
DEFAULT_TIMEZONE=America/Lima
```

De forma predeterminada, el ciclo de calendario y predicciones espera hasta las
`00:05` de Lima y luego se ejecuta una vez al día.
`SCHEDULER_RUN_ON_STARTUP=true` habilita una ejecución inmediata de ese ciclo.
El cierre pospartido se inicia con el backend y se repite cada
`POSTMATCH_POLL_INTERVAL_MINUTES`; solo consulta si encuentra partidos
pronosticados cuyo inicio ya pasó. El programador reutiliza una sola instancia
y cada trabajo admite como máximo una ejecución simultánea. Los límites
`API_DAILY_SAFETY_RESERVE` y `API_MAX_REQUESTS_PER_RUN` siguen protegiendo la
cuota gratuita.

`SCHEDULER_HORIZON_DAYS` controla la sincronización con API-Football.
`SCHEDULER_PREDICTION_HORIZON_DAYS` controla por separado el catch-up de
predicciones desde partidos ya guardados; esta segunda fase no consume cuota
del proveedor y procesa hasta 100 partidos por ciclo.

El plan Free de API-Football permite consultar fechas futuras; la app móvil no
consulta directamente al proveedor y su botón **Actualizar** solo vuelve a leer
Supabase. Si se cambia la clave o se despliega después de las `00:05`, el
scheduler no recupera automáticamente esa ejecución perdida. Se puede llenar
el calendario de los dos días siguientes y publicar sus predicciones con dos
consultas de calendario:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri 'https://api-production-1d96.up.railway.app/admin/jobs/sync-and-predict?horizon_days=2&max_matches=25' `
  -Headers @{ 'X-Admin-Token' = $env:FOOTBALL_ADMIN_TOKEN }
```

`POSTMATCH_LOOKBACK_DAYS` y `POSTMATCH_MAX_MATCHES` controlan el cierre de
resultados. Esta fase revisa como máximo 100 partidos ya pronosticados,
actualiza primero el marcador mediante el calendario de la fecha UTC vigente e
intenta descargar sus estadísticas detalladas en lotes de 20, sin consultar
una liga completa. Cada fixture detallado se intenta como máximo una vez por
día UTC para proteger la cuota.
Si el plan de API-Football bloquea córners, tarjetas o remates, esos mercados
quedan `pending`: los goles sí se califican con el marcador y una estadística
ausente nunca se registra como fallo.

La retención se ejecuta los domingos a las `03:30` de Lima. No borra partidos,
estadísticas normalizadas, snapshots de pronósticos ni evaluaciones. En lotes
acotados elimina logs técnicos de API con más de 90 días y vacía únicamente
los dos JSON idénticos de fixtures finalizados con más de cinco años. Se puede
previsualizar sin cambiar datos:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri 'https://api-production-1d96.up.railway.app/admin/jobs/data-retention?dry_run=true' `
  -Headers @{ 'X-Admin-Token' = $env:FOOTBALL_ADMIN_TOKEN }
```

El programador vive dentro del proceso FastAPI: si el equipo o servidor está
apagado, no puede sincronizar. En un despliegue con varias réplicas se debe
habilitar solo en una de ellas o mantenerlo desactivado y llamar el endpoint
desde un cron externo único, evitando ciclos duplicados.

El plan probado permite `season=2022`, `2023` y `2024`; las temporadas 2021,
2025 y 2026 están registradas como no disponibles. `config/demo.json` sigue
disponible para recorrer la interfaz sin depender del proveedor.

## Actualizar el modelo

El reentrenamiento automático todavía no está implementado. Supabase aporta
datos normalizados y ahora mide los aciertos por mercado y línea, pero no
modifica un modelo por sí solo. Sudamérica ya puede publicar una
estimación estadística inicial desde los resultados históricos guardados, sin
consumir API-Football:

```powershell
.\backend\.venv\Scripts\python.exe -m backend.scripts.predict_stored_baselines --days 30 --limit 25
```

Este baseline no sustituye un modelo ML validado. Para promover cada liga a un
bundle hay que entrenar fuera de la app con división cronológica, medirlo y
desplegar el artefacto confiable. La metodología actual está documentada en
[docs/STATISTICAL_BASELINE.md](docs/STATISTICAL_BASELINE.md).

No se deben descargar ni cargar archivos `joblib` de fuentes no confiables.

## Comprobaciones

```powershell
cd prediccion_futbol
flutter analyze
flutter test
flutter build web --dart-define-from-file=config/demo.json

cd ..\backend
python -m pytest
```

El proyecto Flutter canónico es la carpeta anidada `prediccion_futbol/`; los
archivos de la raíz se conservan únicamente por compatibilidad con el paquete
inicial.
