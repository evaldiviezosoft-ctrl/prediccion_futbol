# Fallback para competiciones de calendario

Europa League (`3`) y amistosos de clubes (`667`) no usan los modelos
entrenados de las ligas. El backend puede publicar un fallback únicamente
cuando al menos uno de los clubes coincide con un perfil local de
`backend/data/team_profiles` o dispone de cinco partidos competitivos
terminados y almacenados en Supabase.

## Método y nivel de confianza

El fallback se identifica siempre con:

- `model_type=calendar_profile_fallback`;
- `method=calendar_profile_poisson`;
- `version=1.3`;
- `confidence=low`;
- `venue_assumption=neutral`;
- `not_calibrated_for_friendlies=true`.

Las tasas de temporada son la evidencia principal. Cada tasa del club se
contrae mediante Empirical Bayes hacia la media de su liga, con una fuerza
previa de ocho partidos. La forma de los últimos cinco partidos aporta solo un
ajuste del 15 %. En amistosos no se usan tasas específicas de local/visitante,
porque la sede puede ser neutral.

Cuando falta el perfil de un rival, sus componentes de goles usan la media
neutral de la liga del club conocido. Esto permite producir las probabilidades
requeridas por el contrato y las líneas de goles de `+0.5` a `+4.5`, pero la
metadata conserva `single_team_profile=true` para que la interfaz no presente
1X2 como un mercado suficientemente respaldado.

## Calibración de fuerza entre competiciones

La versión 1.3 corrige la comparación directa de tasas de gol entre ligas de
distinto nivel. Cada perfil conserva primero su estimación Empirical Bayes; si
ambos clubes tienen una competición de origen conocida, el modelo resuelve un
factor conservador y versionado desde
`backend/app/services/competition_strength.py`.

El ajuste modifica únicamente la proporción de goles esperados entre los dos
clubes y conserva el total del partido. Por tanto, no aumenta artificialmente
los mercados de goles. En amistosos se aplica solo el 80 % de la diferencia y
el cambio queda limitado a una banda equivalente de ±220 puntos.

Como referencia, Premier League usa `1.08` y Eliteserien `0.91`. Son priors
manuales y estrechos, no un ranking en vivo ni una garantía sobre cada club.
Si falta la competición de uno de los lados o no existe en el catálogo, el
ajuste se omite en vez de inventar una fuerza.

La explicación completa queda en
`features_snapshot.cross_league_calibration` y
`model_metadata.cross_league_calibration`: fuentes, factores, rating
equivalente, medias antes/después, multiplicadores, límites y si el total fue
preservado.

## Estadísticas e individuos

Para córners, remates y remates al arco se publican únicamente las claves del
club con perfil. El prior neutral del rival queda documentado como
`reference_only` y `published=false`; nunca se presenta como estadística propia
de ese equipo. Si ambos clubes tienen perfiles, se publican ambos lados aunque
procedan de ligas distintas.

Los goleadores y asistentes solo se derivan de estadísticas individuales
almacenadas en Supabase, terminadas antes del kickoff y pertenecientes al club
con perfil. Sin muestra individual reciente las listas quedan vacías. Nunca se
crea un jugador nominal para el rival desconocido.

## Ejecución

`refresh_prediction` resuelve este flujo antes de construir un cliente de
API-Football. Tanto el job combinado como el job de fixtures almacenados
filtran primero los partidos de calendario y después aplican el límite de
predicciones. Así, una lista larga de amistosos desconocidos no desplaza a un
partido respaldado como Barcelona o Manchester United.

No se requieren nuevas tablas ni migraciones. El registro conserva el contrato
existente de `predictions`; toda la trazabilidad adicional vive dentro de
`model_metadata` y `features_snapshot`.
