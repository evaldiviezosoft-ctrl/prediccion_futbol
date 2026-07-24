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
  └─ prediction_versions (historial)
```

Supabase no ejecuta los modelos. La base de datos guarda datos operativos y
resultados; FastAPI calcula y publica la predicción. Los cinco modelos europeos
(`D1`, `E0`, `F1`, `I1` y `SP1`) viven en `backend/models/`. Las cinco
competiciones sudamericanas usan inicialmente un baseline Poisson/
Empirical-Bayes calculado con el historial almacenado. Ningún modelo se mueve a
Supabase.

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
El valor predeterminado `http://10.0.2.2:8000` permite usar el botón **Run** en
el emulador Android cuando FastAPI está activo.

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

Para un teléfono físico, reemplaza `10.0.2.2` por la IP local del equipo que
ejecuta FastAPI. En producción, `BACKEND_URL` debe usar HTTPS.

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
SCHEDULER_DAILY_HOUR=0
SCHEDULER_DAILY_MINUTE=5
DEFAULT_TIMEZONE=America/Lima
```

De forma predeterminada, arrancar o reiniciar FastAPI no consume cuota: el
primer ciclo espera hasta las `00:05` de Lima y luego se ejecuta una vez al día.
`SCHEDULER_RUN_ON_STARTUP=true` habilita una ejecución inmediata de manera
explícita. El programador reutiliza una sola instancia y cada trabajo admite
como máximo una ejecución simultánea. Los límites
`API_DAILY_SAFETY_RESERVE` y `API_MAX_REQUESTS_PER_RUN` siguen protegiendo la
cuota gratuita.

El programador vive dentro del proceso FastAPI: si el equipo o servidor está
apagado, no puede sincronizar. En un despliegue con varias réplicas se debe
habilitar solo en una de ellas o mantenerlo desactivado y llamar el endpoint
desde un cron externo único, evitando ciclos duplicados.

El plan probado permite `season=2022`, `2023` y `2024`; las temporadas 2021,
2025 y 2026 están registradas como no disponibles. `config/demo.json` sigue
disponible para recorrer la interfaz sin depender del proveedor.

## Actualizar el modelo

El aprendizaje continuo todavía no está implementado. Supabase aporta datos
normalizados, pero no entrena modelos. Sudamérica ya puede publicar una
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
