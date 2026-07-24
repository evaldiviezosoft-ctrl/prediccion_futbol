from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from typing import Any

from app.core.config import get_settings

TEAM_ALIASES = {
    'E0': {
        'Manchester United': 'Man United',
    },
    'SP1': {
        'Espanyol': 'Espanol',
        'RCD Espanyol': 'Espanol',
        'Atletico Madrid': 'Ath Madrid',
        'Athletic Club': 'Ath Bilbao',
        'Celta Vigo': 'Celta',
        'Rayo Vallecano': 'Vallecano',
        'Real Betis': 'Betis',
    },
    'D1': {
        'FC St. Pauli': 'St Pauli',
        'SC Freiburg': 'Freiburg',
        'VfL Wolfsburg': 'Wolfsburg',
        'Bayern München': 'Bayern Munich',
        'Borussia Mönchengladbach': "M'gladbach",
    },
}


@lru_cache
def load_profiles(league_code: str) -> dict[str, dict[str, Any]]:
    path = get_settings().team_profile_root / f'{league_code}.json'
    return json.loads(path.read_text(encoding='utf-8'))


def _days_rest(kickoff: datetime, last_date: str | None) -> float | None:
    if not last_date:
        return None
    return max(0, (kickoff.date() - datetime.fromisoformat(last_date).date()).days)


def _value(profile: dict[str, Any], key: str) -> float | None:
    value = profile.get(key)
    return float(value) if value is not None else None


def build_features(
    league_code: str,
    kickoff: datetime,
    home_name: str,
    away_name: str,
    odds: dict[str, float | None],
) -> dict[str, float | None]:
    profiles = load_profiles(league_code)
    aliases = TEAM_ALIASES.get(league_code, {})
    home_name = aliases.get(home_name, home_name)
    away_name = aliases.get(away_name, away_name)

    if home_name not in profiles or away_name not in profiles:
        missing = [name for name in (home_name, away_name) if name not in profiles]
        raise KeyError(
            'No existe perfil histórico para: ' + ', '.join(missing) +
            '. Agrega un alias o un perfil para equipos ascendidos.'
        )

    home = profiles[home_name]
    away = profiles[away_name]
    features: dict[str, float | None] = {
        'HomeSeasonMP': _value(home, 'season_mp'),
        'AwaySeasonMP': _value(away, 'season_mp'),
        'HomeSeasonPPG': _value(home, 'season_ppg'),
        'AwaySeasonPPG': _value(away, 'season_ppg'),
        'HomeSeasonGFpg': _value(home, 'season_gfpg'),
        'AwaySeasonGFpg': _value(away, 'season_gfpg'),
        'HomeSeasonGApg': _value(home, 'season_gapg'),
        'AwaySeasonGApg': _value(away, 'season_gapg'),
        'HomeSeasonShotsPg': _value(home, 'season_shots_pg'),
        'AwaySeasonShotsPg': _value(away, 'season_shots_pg'),
        'HomeSeasonSOTpg': _value(home, 'season_sot_pg'),
        'AwaySeasonSOTpg': _value(away, 'season_sot_pg'),
        'HomeSeasonSOTAgainstPg': _value(home, 'season_sot_against_pg'),
        'AwaySeasonSOTAgainstPg': _value(away, 'season_sot_against_pg'),
        'HomeSeasonCornersPg': _value(home, 'season_corners_pg'),
        'AwaySeasonCornersPg': _value(away, 'season_corners_pg'),
        'HomeFormPPG5': _value(home, 'form_ppg5'),
        'AwayFormPPG5': _value(away, 'form_ppg5'),
        'HomeGF5': _value(home, 'gf5'), 'AwayGF5': _value(away, 'gf5'),
        'HomeGA5': _value(home, 'ga5'), 'AwayGA5': _value(away, 'ga5'),
        'HomeShots5': _value(home, 'shots5'), 'AwayShots5': _value(away, 'shots5'),
        'HomeSOT5': _value(home, 'sot5'), 'AwaySOT5': _value(away, 'sot5'),
        'HomeSOTAgainst5': _value(home, 'sot_against5'),
        'AwaySOTAgainst5': _value(away, 'sot_against5'),
        'HomeCorners5': _value(home, 'corners5'), 'AwayCorners5': _value(away, 'corners5'),
        'HomeVenuePPG5': _value(home, 'home_venue_ppg5'),
        'AwayVenuePPG5': _value(away, 'away_venue_ppg5'),
        'HomeVenueGF5': _value(home, 'home_venue_gf5'),
        'AwayVenueGF5': _value(away, 'away_venue_gf5'),
        'HomeVenueGA5': _value(home, 'home_venue_ga5'),
        'AwayVenueGA5': _value(away, 'away_venue_ga5'),
        'HomeElo': _value(home, 'elo'), 'AwayElo': _value(away, 'elo'),
        'EloDiff': (_value(home, 'elo') or 1500) - (_value(away, 'elo') or 1500),
        'HomeDaysRest': _days_rest(kickoff, home.get('last_match_date')),
        'AwayDaysRest': _days_rest(kickoff, away.get('last_match_date')),
    }
    features.update(odds)
    return features
