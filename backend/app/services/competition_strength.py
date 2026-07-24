"""Conservative, explainable competition-strength priors.

These values are deliberately hand-maintained rather than presented as a live
ranking.  They provide a *small* cross-league correction when two clubs have
histories from competitions of materially different levels.  The narrow
0.80-1.10 band is intentional: team history must remain the primary signal.

A factor is a relative modelling prior, not a claim about the quality of every
club in a competition and not a probability.  Cup factors describe the average
field; club friendlies are neutral because their participants vary too much.
Unknown competitions also resolve to a neutral factor and expose fallback
metadata so callers can lower confidence instead of silently guessing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import re
from typing import Any


MIN_STRENGTH_FACTOR = 0.80
MAX_STRENGTH_FACTOR = 1.10
FALLBACK_STRENGTH_FACTOR = 1.00


@dataclass(frozen=True, slots=True)
class CompetitionStrength:
    """Resolved strength prior with metadata suitable for API explanations."""

    factor: float
    tier: str
    source: str
    explanation: str
    league_id: int | None
    league_code: str | None
    canonical_code: str | None
    competition_name: str | None
    matched_by: str | None
    is_fallback: bool

    def to_metadata(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation."""

        return asdict(self)


@dataclass(frozen=True, slots=True)
class _StrengthEntry:
    league_id: int
    canonical_code: str
    competition_name: str
    factor: float
    tier: str
    explanation: str
    aliases: tuple[str, ...] = ()


# Assumptions:
# - The top-five domestic leagues are kept close together; this correction is
#   too coarse to justify large gaps between them.
# - South-American and secondary European competitions receive modest factors,
#   not punitive discounts.
# - Continental cups represent mixed fields, so their values sit near neutral.
# - IDs are API-Football league IDs already used by this repository.
_ENTRIES = (
    _StrengthEntry(
        39,
        'premier_league',
        'Premier League',
        1.08,
        'elite',
        'Conservative top-five prior; deepest average domestic field.',
        ('E0', 'england_premier_league'),
    ),
    _StrengthEntry(
        140,
        'laliga',
        'La Liga',
        1.06,
        'elite',
        'Conservative top-five prior.',
        ('SP1', 'la_liga'),
    ),
    _StrengthEntry(
        135,
        'serie_a_italy',
        'Serie A',
        1.05,
        'elite',
        'Conservative top-five prior.',
        ('I1', 'italy_serie_a'),
    ),
    _StrengthEntry(
        78,
        'bundesliga',
        'Bundesliga',
        1.05,
        'elite',
        'Conservative top-five prior.',
        ('D1', 'germany_bundesliga'),
    ),
    _StrengthEntry(
        61,
        'ligue_1',
        'Ligue 1',
        1.03,
        'strong',
        'Conservative top-five prior.',
        ('F1', 'france_ligue_1'),
    ),
    _StrengthEntry(
        40,
        'championship',
        'Championship',
        0.99,
        'strong_second_tier',
        'Strong second tier; modestly below the top-five domestic baseline.',
        ('england_championship', 'api_40'),
    ),
    _StrengthEntry(
        79,
        '2_bundesliga',
        '2. Bundesliga',
        0.96,
        'strong_second_tier',
        'Strong second tier; conservatively below Bundesliga.',
        ('germany_2_bundesliga', 'zweite_bundesliga', 'api_79'),
    ),
    _StrengthEntry(
        141,
        'segunda_division',
        'Segunda División',
        0.96,
        'strong_second_tier',
        'Strong second tier; conservatively below La Liga.',
        ('spain_segunda_division', 'laliga_2', 'api_141'),
    ),
    _StrengthEntry(
        203,
        'super_lig',
        'Süper Lig',
        0.96,
        'strong_regional',
        'Strong European domestic league below the top-five baseline.',
        ('turkey_super_lig', 'api_203'),
    ),
    _StrengthEntry(
        62,
        'ligue_2',
        'Ligue 2',
        0.93,
        'second_tier',
        'Second tier; conservatively below Ligue 1.',
        ('france_ligue_2', 'api_62'),
    ),
    _StrengthEntry(
        95,
        'segunda_liga',
        'Segunda Liga',
        0.92,
        'second_tier',
        'Portuguese second tier; moderate cross-league prior.',
        ('portugal_segunda_liga', 'liga_portugal_2', 'api_95'),
    ),
    _StrengthEntry(
        206,
        'turkish_cup',
        'Turkish Cup',
        0.94,
        'regional_mixed',
        'Mixed Turkish cup field; slightly below the Süper Lig prior.',
        ('turkiye_kupasi', 'api_206'),
    ),
    _StrengthEntry(
        3,
        'uefa_europa_league',
        'UEFA Europa League',
        1.03,
        'strong_mixed',
        'Mixed continental field; kept close to the top-five baseline.',
        ('europa_league',),
    ),
    _StrengthEntry(
        13,
        'copa_libertadores',
        'CONMEBOL Libertadores',
        1.00,
        'continental',
        'Mixed leading South-American field; neutral reference prior.',
        ('libertadores',),
    ),
    _StrengthEntry(
        71,
        'brazil_serie_a',
        'Serie A (Brazil)',
        0.98,
        'strong_regional',
        'Strong regional domestic league with a modest cross-league discount.',
        ('brazilian_serie_a',),
    ),
    _StrengthEntry(
        128,
        'argentina_liga_profesional',
        'Liga Profesional Argentina',
        0.96,
        'strong_regional',
        'Strong regional domestic league with a modest cross-league discount.',
        ('argentina_primera_division',),
    ),
    _StrengthEntry(
        11,
        'copa_sudamericana',
        'CONMEBOL Sudamericana',
        0.95,
        'regional_mixed',
        'Mixed regional cup field; below the Libertadores prior.',
        ('sudamericana',),
    ),
    _StrengthEntry(
        103,
        'eliteserien',
        'Eliteserien',
        0.91,
        'developing_europe',
        'Competitive European domestic league below the top-five baseline.',
        ('norway_eliteserien', 'api_103'),
    ),
    _StrengthEntry(
        129,
        'primera_nacional',
        'Primera Nacional',
        0.87,
        'developing_second_tier',
        'Argentine second tier; conservative regional prior.',
        ('argentina_primera_nacional', 'api_129'),
    ),
    _StrengthEntry(
        138,
        'serie_c_italy',
        'Serie C',
        0.86,
        'developing_lower_tier',
        'Italian third tier; conservative lower-division prior.',
        ('italy_serie_c', 'api_138'),
    ),
    _StrengthEntry(
        891,
        'coppa_italia_serie_c',
        'Coppa Italia Serie C',
        0.86,
        'developing_lower_tier',
        'Cup for Italian Serie C clubs; aligned with its domestic tier.',
        ('italy_serie_c_cup', 'api_891'),
    ),
    _StrengthEntry(
        976,
        'serie_c_playoffs',
        'Serie C Playoffs',
        0.86,
        'developing_lower_tier',
        'Serie C playoff field; aligned with its domestic tier.',
        ('italy_serie_c_playoffs', 'api_976'),
    ),
    _StrengthEntry(
        877,
        'segunda_rfef',
        'Segunda RFEF',
        0.83,
        'developing_lower_tier',
        'Spanish lower division; conservative lower-tier prior.',
        ('spain_segunda_rfef', 'api_877'),
    ),
    _StrengthEntry(
        281,
        'peru_liga_1',
        'Liga 1 (Peru)',
        0.88,
        'developing_regional',
        'Developing regional domestic league; conservative cross-league prior.',
        ('liga_1_peru', 'peru_primera_division'),
    ),
    _StrengthEntry(
        667,
        'friendlies_clubs',
        'Club Friendlies',
        1.00,
        'neutral_context',
        'Friendlies have a mixed field, so the competition itself is neutral.',
        ('club_friendlies', 'international_club_friendlies'),
    ),
)


def _normalise_code(value: str) -> str:
    return re.sub(r'_+', '_', re.sub(r'[^a-z0-9]+', '_', value.casefold())).strip('_')


def _validate_catalog(
    entries: tuple[_StrengthEntry, ...],
) -> tuple[dict[int, _StrengthEntry], dict[str, _StrengthEntry]]:
    by_id: dict[int, _StrengthEntry] = {}
    by_code: dict[str, _StrengthEntry] = {}
    for entry in entries:
        if entry.league_id < 1:
            raise ValueError(f'Invalid league ID in strength catalog: {entry.league_id}')
        if not math.isfinite(entry.factor) or not (
            MIN_STRENGTH_FACTOR <= entry.factor <= MAX_STRENGTH_FACTOR
        ):
            raise ValueError(
                f'Strength factor for {entry.canonical_code} must be between '
                f'{MIN_STRENGTH_FACTOR:.2f} and {MAX_STRENGTH_FACTOR:.2f}.'
            )
        if entry.league_id in by_id:
            raise ValueError(f'Duplicate league ID in strength catalog: {entry.league_id}')
        by_id[entry.league_id] = entry
        for raw_code in (entry.canonical_code, *entry.aliases):
            code = _normalise_code(raw_code)
            existing = by_code.get(code)
            if existing is not None and existing != entry:
                raise ValueError(f'Duplicate league code in strength catalog: {raw_code}')
            by_code[code] = entry
    return by_id, by_code


COMPETITION_STRENGTH_BY_ID, _COMPETITION_STRENGTH_BY_CODE = _validate_catalog(
    _ENTRIES
)
COMPETITION_STRENGTH_CODES = frozenset(_COMPETITION_STRENGTH_BY_CODE)


def _validated_league_id(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError('league_id must be a positive integer or None.')
    return value


def _validated_league_code(value: str | None) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    if not isinstance(value, str):
        raise ValueError('league_code must be a string or None.')
    original = value.strip()
    if not original:
        return None, None
    return original, _normalise_code(original)


def resolve_competition_strength(
    *,
    league_code: str | None = None,
    league_id: int | None = None,
) -> CompetitionStrength:
    """Resolve a prior by ID first, then code, with an explicit neutral fallback.

    When both identifiers are supplied, an exact ID is authoritative because
    provider IDs are less ambiguous than display/internal codes.  A known code
    is still used if the supplied positive ID is unknown.
    """

    requested_id = _validated_league_id(league_id)
    requested_code, normalised_code = _validated_league_code(league_code)

    entry = (
        COMPETITION_STRENGTH_BY_ID.get(requested_id)
        if requested_id is not None
        else None
    )
    matched_by = 'league_id' if entry is not None else None
    if entry is None and normalised_code is not None:
        entry = _COMPETITION_STRENGTH_BY_CODE.get(normalised_code)
        matched_by = 'league_code' if entry is not None else None

    if entry is None:
        supplied = (
            f'league_id={requested_id}'
            if requested_id is not None
            else f'league_code={requested_code!r}'
            if requested_code is not None
            else 'no competition identifier'
        )
        return CompetitionStrength(
            factor=FALLBACK_STRENGTH_FACTOR,
            tier='unknown',
            source='explicit_neutral_fallback',
            explanation=(
                f'No maintained strength prior for {supplied}; using neutral '
                'factor and callers should lower confidence.'
            ),
            league_id=requested_id,
            league_code=requested_code,
            canonical_code=None,
            competition_name=None,
            matched_by=None,
            is_fallback=True,
        )

    return CompetitionStrength(
        factor=entry.factor,
        tier=entry.tier,
        source='maintained_conservative_catalog',
        explanation=entry.explanation,
        league_id=entry.league_id,
        league_code=requested_code or entry.canonical_code,
        canonical_code=entry.canonical_code,
        competition_name=entry.competition_name,
        matched_by=matched_by,
        is_fallback=False,
    )


def competition_strength_factor(
    *,
    league_code: str | None = None,
    league_id: int | None = None,
) -> float:
    """Convenience wrapper for numeric-only consumers."""

    return resolve_competition_strength(
        league_code=league_code,
        league_id=league_id,
    ).factor
