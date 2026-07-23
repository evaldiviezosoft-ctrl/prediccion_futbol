from __future__ import annotations
from typing import Any


def possible_scorers(lineups_payload: dict[str, Any], cached_player_profiles: dict[int, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Heurística inicial.

    Solo devuelve jugadores confirmados como titulares que tengan un perfil
    previamente guardado. Para un modelo serio agrega goles/90, xG/90,
    remates al arco/90, penaltis y minutos esperados.
    """
    cached_player_profiles = cached_player_profiles or {}
    candidates: list[dict[str, Any]] = []
    for team in lineups_payload.get('response', []):
        team_name = team.get('team', {}).get('name')
        for item in team.get('startXI', []):
            player = item.get('player', {})
            player_id = player.get('id')
            profile = cached_player_profiles.get(player_id)
            if not profile:
                continue
            goals_per90 = float(profile.get('goals_per90') or 0)
            sot_per90 = float(profile.get('shots_on_target_per90') or 0)
            penalty_bonus = 0.12 if profile.get('penalty_taker') else 0.0
            raw = min(0.75, 0.08 + goals_per90 * 0.33 + sot_per90 * 0.08 + penalty_bonus)
            candidates.append({
                'player_id': player_id,
                'player': player.get('name'),
                'team': team_name,
                'probability': round(raw, 4),
                'starter': True,
            })
    return sorted(candidates, key=lambda item: item['probability'], reverse=True)[:10]
