# Baseline estadístico para Sudamérica y CONMEBOL

Las competiciones `11`, `13`, `71`, `128` y `281` usan un baseline
Poisson/Empirical-Bayes calculado en el backend. No se descarga una predicción
del proveedor y no se necesita un archivo `.joblib` para estas ligas.

## Datos y corte temporal

Para un partido con inicio `T`, el repositorio consulta solamente filas de la
misma liga que cumplan:

- `status_short in ('FT', 'AET', 'PEN')`;
- `home_goals` y `away_goals` válidos;
- `kickoff < T` (comparación estricta).

El servicio vuelve a verificar las mismas condiciones en memoria. De esta
manera una respuesta incorrecta o un mock que contenga partidos futuros no
puede introducir fuga temporal. Se requieren al menos 20 partidos válidos de
la liga.

## Método y mercados publicados

La media local y visitante se estima con todos los partidos elegibles de la
liga. Las tasas del local jugando en casa y del visitante jugando fuera se
contraen hacia esas medias con una fuerza previa de ocho partidos. Dos
distribuciones Poisson independientes producen 1X2, ambos marcan y las
probabilidades de total de goles para:

- más de 0.5;
- más de 1.5;
- más de 2.5;
- más de 3.5;
- más de 4.5.

La app no publica marcadores exactos del baseline: `likely_scores` se guarda
como una lista vacía. Las líneas de goles se guardan en
`model_metadata.goal_lines` y el endpoint público también las expone como
`goal_lines`.

## Córners, remates y remates al arco

`expected` puede incluir estimaciones separadas para local y visitante:

- `home_corners` / `away_corners`;
- `home_shots` / `away_shots`;
- `home_shots_on_target` / `away_shots_on_target`.

Estas estimaciones usan medias Empirical-Bayes por equipo y condición de
local/visitante. En partidos de copa se prefiere el historial del equipo en su
liga doméstica: Brasil (`71`), Argentina (`128`) o Perú (`281`).

La cobertura avanzada actual incluye 41 partidos de Liga 1 Perú y 99 partidos
utilizables del Brasileirão 2024. Brasil aporta 99 muestras por localía, 20
clubes y valores completos para las tres métricas, por lo que sus predicciones
ya usan el prior de la liga brasileña.

Una liga no sustituye la referencia peruana hasta alcanzar, para cada métrica,
al menos 40 valores válidos y 8 equipos distintos tanto como local como
visitante. Si la muestra no supera ese umbral —como ocurre todavía con
Argentina— el prior peruano puede usarse solo como guía transversal. La
metadata deja auditable la decisión mediante `coverage_gate`,
`prior_selection_reason` y:

- `status=reference_only`;
- `cross_league_reference=true` cuando corresponde;
- `prior_league_id=281`;
- `confidence=low`;
- cantidades reales en `team_rows` y `prior_rows`.

Por tanto, esos valores no deben interpretarse como estadísticas observadas
del equipo ni como una recomendación de apuesta. Si tampoco existe un prior
válido, el campo se omite en vez de inventarse.

## Posibles goleadores y asistentes

Los candidatos solo pueden derivarse de estadísticas de jugadores anteriores
al kickoff, ponderando minutos y apariciones y contrayendo la participación de
eventos mediante Empirical-Bayes. Además, cada nombre exige al menos un gol o
asistencia observado; el prior nunca crea por sí solo un candidato nominal.

Sin muestra reciente (máximo 365 días) o sin una plantilla/alineación vigente
confirmada, las listas permanecen vacías. Esto aplica a los partidos actuales:
el historial de jugadores brasileños disponible termina en diciembre de 2024
y no demuestra quién integra el plantel de 2026. La causa queda registrada en
`model_metadata.player_candidates`, por ejemplo como
`insufficient_freshness` o `no_player_sample`. Los asistentes se almacenan en
`model_metadata.possible_assistants` y el endpoint público los promueve a
`possible_assistants`; los goleadores usan la columna existente
`possible_scorers`.

Cada predicción guarda metadata auditable:

- `model_type=statistical_baseline` y `method=poisson_empirical_bayes`;
- `league_id` y `league_code`;
- cantidad real de filas, temporadas y primer/ultimo kickoff usados;
- muestras del local en casa y del visitante fuera;
- cutoff exacto y regla anti-leakage;
- fuente, cobertura y nivel de confianza de las estadísticas auxiliares;
- fuerza del prior y confirmación de que no se usaron cuotas.

## Publicar fixtures ya almacenados sin API-Football

Desde la raiz del repositorio:

```powershell
.\backend\.venv\Scripts\python.exe -m backend.scripts.predict_stored_baselines --days 30 --limit 25
```

Ese comando solo lee `fixtures`, `fixture_team_statistics`,
`fixture_player_statistics` y `players`, y escribe `predictions` /
`prediction_versions` mediante la clave de backend de Supabase. No crea un
`ApiFootballClient`, no consulta API-Football y por ese motivo no inserta filas
en `api_request_logs`.

Los cinco bundles europeos (`E0`, `F1`, `D1`, `I1`, `SP1`) conservan su flujo
existente. El job combinado también reconoce las cinco ligas baseline, pero el
comando anterior es la opción indicada cuando se desea publicar únicamente a
partir de datos ya almacenados.
