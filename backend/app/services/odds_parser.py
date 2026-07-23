from __future__ import annotations
from collections import defaultdict
from typing import Any


def _fair_probabilities(odds: list[float | None]) -> list[float | None]:
    if any(value is None or value <= 1 for value in odds):
        return [None for _ in odds]
    inverse = [1 / float(value) for value in odds]
    total = sum(inverse)
    return [value / total for value in inverse]


def parse_opening_odds(payload: dict[str, Any]) -> dict[str, float | None]:
    match_winner: dict[str, list[float]] = defaultdict(list)
    over_under: dict[str, list[float]] = defaultdict(list)

    for item in payload.get('response', []):
        for bookmaker in item.get('bookmakers', []):
            for bet in bookmaker.get('bets', []):
                name = str(bet.get('name', '')).strip().lower()
                for value in bet.get('values', []):
                    label = str(value.get('value', '')).strip().lower()
                    odd = value.get('odd')
                    try:
                        odd = float(odd)
                    except (TypeError, ValueError):
                        continue
                    if name in {'match winner', '1x2'}:
                        if label in {'home', '1'}:
                            match_winner['H'].append(odd)
                        elif label in {'draw', 'x'}:
                            match_winner['D'].append(odd)
                        elif label in {'away', '2'}:
                            match_winner['A'].append(odd)
                    if 'over/under' in name or 'goals over/under' in name:
                        if label == 'over 2.5':
                            over_under['O'].append(odd)
                        elif label == 'under 2.5':
                            over_under['U'].append(odd)

    def avg(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    h, d, a = avg(match_winner['H']), avg(match_winner['D']), avg(match_winner['A'])
    ph, pd, pa = _fair_probabilities([h, d, a])
    over, under = avg(over_under['O']), avg(over_under['U'])
    po, pu = _fair_probabilities([over, under])
    return {
        'AvgOpenH': h, 'AvgOpenD': d, 'AvgOpenA': a,
        'AvgOpenProbH': ph, 'AvgOpenProbD': pd, 'AvgOpenProbA': pa,
        'B365OpenH': h, 'B365OpenD': d, 'B365OpenA': a,
        'B365OpenProbH': ph, 'B365OpenProbD': pd, 'B365OpenProbA': pa,
        'AvgOUOpenOver': over, 'AvgOUOpenUnder': under,
        'AvgOUOpenProbOver': po, 'AvgOUOpenProbUnder': pu,
        'B365OUOpenOver': over, 'B365OUOpenUnder': under,
        'B365OUOpenProbOver': po, 'B365OUOpenProbUnder': pu,
    }
