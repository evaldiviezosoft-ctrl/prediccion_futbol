# Importación API-Football → Supabase

Este módulo descarga datos reales desde API-Football v3, los normaliza y los
guarda en Supabase. Flutter nunca llama al proveedor ni recibe sus secretos.

## Competiciones

El archivo `backend/app/config/competitions.yaml` contiene exclusivamente:

- Premier League, La Liga, Serie A (Italia), Bundesliga y Ligue 1.
- Primera División/Liga 1 (Perú), Serie A (Brasil) y Liga Profesional Argentina.
- CONMEBOL Libertadores y CONMEBOL Sudamericana.

Los IDs no están fijados como fuente de verdad en el YAML. El resolver consulta
el catálogo del proveedor y valida nombre, país y tipo antes de persistirlos. En
el proyecto actual se verificaron: Perú `281`, Brasil `71`, Argentina `128`,
Libertadores `13` y Sudamericana `11`.

## Arquitectura y secretos

```text
API-Football ──(API_FOOTBALL_KEY)──> FastAPI/importadores
                                             │
                              (SUPABASE_SECRET_KEY)
                                             │
                                             ▼
                                          Supabase
                                             ▲
                                             │
Flutter ────────(backend HTTP, sin secretos)──────┘
```

`SUPABASE_SECRET_KEY` es correcta para el backend y equivale al acceso
privilegiado moderno. Nunca debe colocarse en Flutter. La app actual lee
fixtures y predicciones mediante FastAPI, por lo que tampoco necesita una clave
publicable de Supabase.

Crear `backend/.env` a partir de `.env.example`:

```dotenv
API_FOOTBALL_KEY=tu_clave_api_football
SUPABASE_URL=https://yrdtvgegicjvobaioxof.supabase.co
SUPABASE_SECRET_KEY=sb_secret_...
ADMIN_TOKEN=un_token_largo_y_aleatorio

API_DAILY_SAFETY_RESERVE=15
API_MAX_REQUESTS_PER_RUN=80
API_DELAY_SECONDS=7
API_TIMEZONE=America/Lima
UPCOMING_DAYS=30
```

La variable heredada `SUPABASE_SERVICE_ROLE_KEY` se admite como fallback, pero
no es necesaria cuando existe `SUPABASE_SECRET_KEY`.

## Migración

Las migraciones principales son:

- `supabase/migrations/20260722213126_api_football_sync.sql`: esquema de
  importación normalizado.
- `supabase/migrations/20260722221731_track_active_fixture_injuries.sql`:
  conserva el historial y distingue las lesiones vigentes.
- `supabase/migrations/20260722222011_backfill_cup_aggregates.sql`: calcula los
  marcadores agregados cuando están disponibles ambos partidos de una llave.

Son aditivas: no borran las tablas previas, conservan `fixtures.id`, los códigos
de los cinco modelos europeos y las relaciones de predicciones.

Ya fue aplicada al proyecto `yrdtvgegicjvobaioxof`. Para otro entorno:

```powershell
npx --yes supabase@latest login
npx --yes supabase@latest link --project-ref TU_PROJECT_REF
npx --yes supabase@latest db push
```

Las tablas de ingesta tienen RLS y no otorgan acceso a `anon` ni
`authenticated`. Los endpoints públicos leen a través de FastAPI y devuelven
columnas seleccionadas; no exponen `raw_json` ni claves.

## Comandos

Ejecutar desde la raíz del repositorio con el entorno virtual activado:

```powershell
python -m backend.scripts.resolve_competitions
python -m backend.scripts.sync_upcoming --days 30
python -m backend.scripts.sync_historical --from-season 2021 --to-season 2026
python -m backend.scripts.sync_fixture_details --resume
python -m backend.scripts.show_sync_progress
```

Sin activar el entorno virtual en Windows:

```powershell
.\backend\.venv\Scripts\python.exe -m backend.scripts.resolve_competitions
```

Una competición:

```powershell
python -m backend.scripts.sync_historical `
  --competition peru_liga_1 `
  --from-season 2022 `
  --to-season 2024
```

Solo copas CONMEBOL:

```powershell
python -m backend.scripts.sync_historical `
  --competition copa_libertadores `
  --competition copa_sudamericana `
  --from-season 2022 `
  --to-season 2024
```

Para guardar primero todos los resultados y diferir los detalles:

```powershell
python -m backend.scripts.sync_historical `
  --from-season 2022 --to-season 2024 --basic-only
python -m backend.scripts.sync_fixture_details --resume --limit 100
```

El proveedor actual rechaza lotes `ids=...` aunque admite `id=...`. El cliente
lo detecta una vez y cambia a solicitudes singulares. Cada partido se confirma
en Supabase antes de continuar, por lo que alcanzar la cuota no pierde progreso.

### Backfill dirigido de estadísticas de mercado

Para completar córners, remates y remates al arco de los fixtures históricos
ya guardados de Brasil o Argentina, usar el comando dirigido. Sin `--execute`
solo consulta Supabase y muestra el plan; no construye el cliente API ni escribe:

```powershell
.\backend\.venv\Scripts\python.exe -m backend.scripts.backfill_market_statistics `
  --competition brazil `
  --max-requests 10
```

Después de revisar los IDs seleccionados, la ejecución real exige autorización
explícita:

```powershell
.\backend\.venv\Scripts\python.exe -m backend.scripts.backfill_market_statistics `
  --competition brazil `
  --max-requests 10 `
  --execute
```

También se acepta `--competition argentina`; sin `--competition` se consideran
ambas ligas. Las garantías del comando son:

- las solicitudes son singulares por defecto porque el plan probado rechaza
  `ids=...`; por ello `--max-requests N` selecciona como máximo `N` fixtures;
- `--allow-batches` existe únicamente como opt-in para otro plan que confirme
  soporte de lotes y no debe usarse con la cuenta actual;
- solo selecciona partidos `FT`, `AET` o `PEN` con
  `statistics_downloaded=false`;
- prioriza equipos vistos durante los últimos 370 días y luego ordena por
  temporada, kickoff e ID del fixture en orden descendente;
- guarda cada respuesta inmediatamente y excluye automáticamente el fixture
  cuando sus estadísticas fueron normalizadas;
- una clave `statistics: []` no cuenta como detalle completo: se exige al menos
  una fila normalizada en `fixture_team_statistics`, se restablece
  `statistics_downloaded=false` y el fixture conserva su estado reanudable;
- después de tres respuestas fallidas o vacías, el fixture deja de seleccionarse
  salvo que se aumente conscientemente `--max-attempts`;
- el límite local también cuenta reintentos HTTP y conserva la reserva diaria
  configurada.

### Backfill dirigido por equipo

Cuando un amistoso incluye un club sin perfil, no es necesario descargar su
liga completa. El importador dirigido usa una sola consulta acotada
`/fixtures?team=<id>&last=<N>` por club. En el plan gratuito, que no permite
`last`, se usa `--season 2024` y la consulta sigue acotada por `team=<id>`;
después se conservan localmente los N partidos competitivos más recientes en
`FT/AET/PEN`, y luego se solicitan
detalles únicamente para los fixtures elegibles que todavía no tengan filas en
`fixture_team_statistics`.

Los IDs identificados en el calendario actual son `69`, `76`, `104`, `235`,
`331`, `593`, `645`, `744`, `865`, `1066` y `4665`. Primero se revisa el plan
local; este comando no construye el cliente API ni escribe en Supabase:

```powershell
.\backend\.venv\Scripts\python.exe -m backend.scripts.backfill_team_history `
  --team-id 69 --team-id 76 --team-id 104 --team-id 235 `
  --team-id 331 --team-id 593 --team-id 645 --team-id 744 `
  --team-id 865 --team-id 1066 --team-id 4665 `
  --season 2024 `
  --max-fixtures-per-team 20 `
  --max-requests 25
```

La ejecución real requiere `--execute`. El tope HTTP cuenta las consultas de
equipos, detalles y reintentos:

```powershell
.\backend\.venv\Scripts\python.exe -m backend.scripts.backfill_team_history `
  --team-id 69 --team-id 76 --team-id 104 --team-id 235 `
  --team-id 331 --team-id 593 --team-id 645 --team-id 744 `
  --team-id 865 --team-id 1066 --team-id 4665 `
  --season 2024 `
  --max-fixtures-per-team 20 `
  --max-detail-fixtures 100 `
  --max-requests 25 `
  --execute
```

Si además se necesita garantizar que el flujo intente completar país, código,
fundación y estadio de cada club nuevo, se añade la opción explícita
`--with-team-metadata`. Esta reserva una llamada `/teams?id=...` adicional por
equipo, la ejecuta antes de descargar el historial y descuenta esas llamadas
del presupuesto disponible para detalles:

```powershell
.\backend\.venv\Scripts\python.exe -m backend.scripts.backfill_team_history `
  --team-id 69 --team-id 76 `
  --season 2024 `
  --max-fixtures-per-team 20 `
  --max-requests 6 `
  --with-team-metadata

# Agregar --execute solo después de revisar el plan.
```

Sin `--with-team-metadata`, el comando conserva el comportamiento económico:
los fixtures aportan nombre, ID y escudo, pero no se afirma que aporten el país.
Con la opción activa se exigen al menos dos solicitudes nominales por equipo
(metadata + historial). Los reintentos también consumen el tope estricto y
API-Football podría no informar algún campo opcional; esos casos quedan
reportados y el comando puede reanudarse sin duplicar filas.

Garantías del flujo:

- acepta ligas domésticas fuera del catálogo principal; crea una competencia
  mínima `api_<league_id>` deshabilitada, suficiente para las relaciones
  normalizadas pero incapaz de activar por accidente una descarga de liga
  completa;
- guarda únicamente resultados `FT`, `AET` o `PEN` con marcador válido y
  participación real del `team_id`; omite competiciones amistosas;
- deduplica equipos y fixtures compartidos antes de solicitar detalles;
- comprueba filas normalizadas, no solo la presencia de `statistics: []`, para
  decidir si un detalle ya está completo;
- prueba detalles de hasta 20 fixtures por lote y cambia automáticamente a
  solicitudes singulares si el plan rechaza `ids`; `--force-singular-details`
  permite forzar el modo individual desde el inicio;
- el endpoint agregado de fixtures puede traer jugadores sin costo adicional,
  pero este backfill nunca llama `/fixtures/players` ni realiza consultas por
  jugador;
- guarda progreso después de cada equipo y cada detalle, y se detiene de forma
  reanudable cuando alcanza la reserva diaria o el tope local.

Nombre, ID y escudo ya vienen en los fixtures. País, código, fundación,
indicador de selección nacional y estadio son opcionales. También se pueden
consultar por separado, una petición `/teams?id=...` por club:

```powershell
.\backend\.venv\Scripts\python.exe -m backend.scripts.backfill_team_metadata `
  --team-id 69 --team-id 76 `
  --max-requests 2

# Agregar --execute solo después de revisar el plan.
```

El historial queda disponible para predicción en las tablas normales, no en un
almacén paralelo. `SupabaseRepository.team_by_api_id()` resuelve el ID interno;
`historical_finished_fixtures_for_team()` lee partidos de cualquier liga antes
del kickoff; `team_statistics_for_fixtures()` aporta córners, remates y remates
al arco. Con esas filas,
`team_history_profile.build_team_history_profile()` genera un perfil con corte
temporal estricto y exige al menos cinco partidos. Se debe pasar
`team_ref_id=int(team_row["id"])` para no confundir el ID de API-Football con la
clave interna de `public.teams`.

## Datos prepartido opcionales

No se consumen automáticamente. Se habilitan explícitamente:

```powershell
python -m backend.scripts.sync_upcoming --days 7 `
  --with-injuries `
  --with-odds `
  --with-external-predictions `
  --with-lineups
```

- Lesiones: como máximo una consulta cada cuatro horas por partido.
- Cuotas: snapshots append-only, deduplicados por hash.
- Predicción externa: se guarda separada del modelo propio.
- Alineaciones: solo durante los 90 minutos previos; reintento cada 15 minutos
  hasta confirmar ambas.

## Límites, reintentos y reanudación

- Se leen los límites diarios y por minuto de cada respuesta.
- La ejecución bloquea nuevas solicitudes cuando el saldo diario informado es
  15 o menos.
- Un proceso nuevo restaura el último saldo diario registrado; un saldo por
  minuto igual a cero bloquea la siguiente llamada y solo se restaura si el
  registro tiene menos de un minuto.
- Hay como máximo tres intentos para 429, 499 y errores 5xx.
- Se respeta `Retry-After` y se aplica backoff exponencial.
- `api_sync_status` evita repetir detalles ya completos.
- `api_request_logs` registra endpoint, parámetros sin secretos, duración y
  cuota; nunca guarda `x-apisports-key`.

El plan probado el 22 de julio de 2026 permite las temporadas `2022`, `2023` y
`2024`. `2021`, `2025` y `2026` quedaron registradas como `unavailable`; esto es
una limitación de la cuenta, no una ausencia en el catálogo de la competición.

## Sincronización automática diaria

El programador interno está desactivado de forma predeterminada. Se configura
solo en `backend/.env`:

```dotenv
ENABLE_SCHEDULER=true
SCHEDULER_RUN_ON_STARTUP=false
SCHEDULER_DAILY_HOUR=0
SCHEDULER_DAILY_MINUTE=5
DEFAULT_TIMEZONE=America/Lima
```

Por defecto, iniciar o reiniciar FastAPI espera al siguiente cron diario fijo y
no consume cuota inmediatamente. `SCHEDULER_RUN_ON_STARTUP=true` permite
ejecutar `sync_and_predict` al arrancar cuando se necesita explícitamente. No
repite el ciclo cada hora. El trabajo usa `coalesce`, `max_instances=1` y una
única instancia del programador por proceso para impedir solapamientos locales.

La sincronización conserva la reserva diaria y el máximo de solicitudes por
ejecución. El programador solo funciona mientras FastAPI está activo. Si se
despliegan varias réplicas, se debe habilitar en una sola; para alta
disponibilidad conviene un cron externo único que invoque la ruta
administrativa.

## API FastAPI

Rutas administrativas, todas con `X-Admin-Token`:

- `POST /admin/sync/resolve-competitions`
- `POST /admin/sync/historical`
- `POST /admin/sync/upcoming`
- `POST /admin/sync/resume`
- `GET /admin/sync/progress`
- `GET /admin/sync/logs`

Rutas públicas; leen Supabase y no llaman a API-Football:

- `GET /competitions`
- `GET /fixtures/today`
- `GET /fixtures/upcoming?days=7`
- `GET /fixtures/{fixture_id}`
- `GET /fixtures/{fixture_id}/statistics`
- `GET /fixtures/{fixture_id}/lineups`
- `GET /fixtures/{fixture_id}/players`
- `GET /predictions/{fixture_id}`

### Visibilidad del calendario

`GET /fixtures/upcoming` no oculta partidos de las ligas con modelo. Para las
competiciones que por ahora son solo calendario (Europa League y amistosos de
clubes), devuelve un partido únicamente cuando al menos uno de los dos equipos:

- tiene un perfil local en `backend/data/team_profiles` (nombre exacto,
  designador seguro `FC`/`SC`/`VfL` o alias explícito); o
- tiene al menos cinco partidos competitivos terminados antes del kickoff
  (`FT`, `AET` o `PEN`) y con marcador registrado en Supabase. Pueden proceder
  de cualquier liga almacenada; los amistosos no cuentan como historial.

La comprobación histórica usa únicamente datos ya guardados; no consume
peticiones de API-Football. El mismo resultado habilita la guía predictiva en
la respuesta, sin repetir la consulta paginada.

Solo se permite una sincronización administrativa simultánea por proceso
FastAPI; una segunda solicitud recibe HTTP 409.

## Preparación para aprendizaje automático

Supabase almacena los datos de entrenamiento; no ejecuta ni sustituye el modelo.
Los cinco bundles europeos permanecen en `backend/models/`. Todavía no existen
bundles entrenados y validados para Perú, Brasil, Argentina, Libertadores o
Sudamericana. Mientras se construyen y validan, esas competiciones publican un
baseline Poisson/Empirical-Bayes claramente identificado y calculado solo con
partidos históricos anteriores al kickoff. Consulta
[STATISTICAL_BASELINE.md](STATISTICAL_BASELINE.md).

Para entrenar un modelo nuevo:

1. Completar suficientes detalles con `sync_fixture_details --resume`.
2. Construir features usando solo información conocida antes del kickoff.
3. Separar entrenamiento/validación/prueba por fecha, nunca aleatoriamente.
4. Entrenar perfiles distintos para ligas nacionales y copas.
5. Validar calibración y métricas fuera de muestra.
6. Publicar un bundle confiable en `backend/models/<codigo>/` y actualizar el
   manifiesto del backend.

`backend/scripts/train_models.py` sigue siendo el punto reservado para ese
pipeline; esta entrega prepara y llena la fuente normalizada, pero no afirma que
haya entrenado modelos sudamericanos.

## Estado verificado de esta instalación

Después del backfill dirigido de Brasil del 23 de julio de 2026:

- 12 competiciones resueltas. UEFA Europa League y Amistosos de clubes se
  sincronizan como calendario visible, sin afirmar que tengan un modelo
  predictivo validado.
- 4,409 fixtures reales de 2022–2024 para las cinco competiciones
  sudamericanas/CONMEBOL.
- 77 fixtures incorporados mediante consultas por fecha: los 6 del corte
  anterior y 71 del 24 de julio de 2026. Hay 4,486 fixtures almacenados en
  total; para el 24 de julio, 68 siguen activos y 3 constan como cancelados.
- 41 fixtures peruanos y 99 brasileños con detalle utilizable: 140 en total.
- Brasil aporta 198 filas de estadísticas de equipo, repartidas en 99 muestras
  como local y 99 como visitante, con 20 clubes y cobertura completa de
  córners, remates y remates al arco. No se detectaron filas donde los remates
  al arco superen los remates totales.
- 2,476 eventos, 280 estadísticas de equipo, 6,136 estadísticas de jugador y
  282 alineaciones normalizadas en total.
- El fixture brasileño `1180535` devolvió `statistics: []`; quedó marcado como
  pendiente y no se cuenta entre las 99 muestras utilizables.
- 322 filas de partidos CONMEBOL con marcador agregado e ida/vuelta inferidos
  a partir de 161 llaves completas.
- El progreso reanudable contabiliza 4,486 fixtures básicos, 140 detalles
  completos y 4,346 pendientes sin truncarse en el límite de 1,000 filas de
  PostgREST.
- El backfill original consumió sus 100 solicitudes. Tras el reinicio diario,
  la sincronización del 24 de julio y el historial dirigido de 11 clubes dejó
  `22/100` solicitudes disponibles, respetando una reserva operativa de 15.

Estos contadores son una fotografía y aumentarán con las siguientes ejecuciones
de reanudación.
