from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from difflib import SequenceMatcher
import inspect
import logging
from pathlib import Path
import re
import unicodedata
from typing import Any, Protocol

from pydantic import ValidationError
import yaml

from app.core.config import Settings, get_settings
from app.schemas.api_football import (
    ApiFootballLeagueEntry,
    CompetitionCandidate,
    CompetitionConfig,
    CompetitionResolutionBatch,
    CompetitionSpec,
    CompetitionType,
    ResolvedCompetition,
    UnresolvedCompetition,
)
from app.services.api_football_client import ApiFootballClient


logger = logging.getLogger(__name__)
TARGET_SEASONS = frozenset(range(2021, 2027))
ResolutionCallback = Callable[[ResolvedCompetition], Awaitable[None] | None]


class CompetitionResolutionError(RuntimeError):
    def __init__(self, internal_code: str, reason: str) -> None:
        self.internal_code = internal_code
        self.reason = reason
        super().__init__(f'{internal_code}: {reason}')


class CompetitionResolutionSink(Protocol):
    async def upsert_competition_resolution(
        self,
        resolution: ResolvedCompetition,
    ) -> None: ...


def normalize_label(value: str) -> str:
    decomposed = unicodedata.normalize('NFKD', value)
    without_accents = ''.join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r'[^a-z0-9]+', ' ', without_accents.lower()).strip()


def load_competition_config(path: Path | str) -> CompetitionConfig:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise CompetitionResolutionError('configuration', f'file not found: {path}') from exc
    except yaml.YAMLError as exc:
        raise CompetitionResolutionError('configuration', 'invalid YAML') from exc
    try:
        config = CompetitionConfig.model_validate(raw)
    except ValidationError as exc:
        raise CompetitionResolutionError('configuration', 'invalid competition definitions') from exc
    if not config.competitions:
        raise CompetitionResolutionError(
            'configuration',
            'at least one competition is required',
        )
    return config


def _provider_type(value: str) -> CompetitionType | None:
    normalized = normalize_label(value)
    if normalized == 'league':
        return 'league'
    if normalized == 'cup':
        return 'cup'
    return None


def _name_similarity(actual: str, expected_values: list[str]) -> float:
    actual_normalized = normalize_label(actual)
    actual_tokens = set(actual_normalized.split())
    best = 0.0
    for expected in expected_values:
        expected_normalized = normalize_label(expected)
        if not expected_normalized:
            continue
        if actual_normalized == expected_normalized:
            return 1.0
        expected_tokens = set(expected_normalized.split())
        token_score = (
            len(actual_tokens & expected_tokens) / len(actual_tokens | expected_tokens)
            if actual_tokens | expected_tokens
            else 0.0
        )
        sequence_score = SequenceMatcher(None, actual_normalized, expected_normalized).ratio()
        best = max(best, token_score, sequence_score)
    return best


class CompetitionResolver:
    """Resolve configured competitions without relying on hard-coded provider IDs."""

    def __init__(
        self,
        client: ApiFootballClient,
        *,
        settings: Settings | None = None,
        config_path: Path | None = None,
        resolution_sink: CompetitionResolutionSink | ResolutionCallback | None = None,
        minimum_name_similarity: float = 0.82,
        ambiguity_margin: float = 2.0,
    ) -> None:
        if not 0 <= minimum_name_similarity <= 1:
            raise ValueError('minimum_name_similarity must be between 0 and 1')
        if ambiguity_margin < 0:
            raise ValueError('ambiguity_margin cannot be negative')
        selected_settings = settings or get_settings()
        self.client = client
        self.config_path = config_path or selected_settings.competitions_config_path
        self.resolution_sink = resolution_sink
        self.minimum_name_similarity = minimum_name_similarity
        self.ambiguity_margin = ambiguity_margin

    @property
    def config(self) -> CompetitionConfig:
        return load_competition_config(self.config_path)

    async def resolve_all(self, *, include_disabled: bool = False) -> CompetitionResolutionBatch:
        batch = CompetitionResolutionBatch()
        for spec in self.config.competitions:
            if not spec.enabled and not include_disabled:
                continue
            try:
                resolution = await self.resolve_one(spec)
                await self._persist(resolution)
                batch.resolved.append(resolution)
            except CompetitionResolutionError as exc:
                logger.warning(
                    'competition_resolution_failed',
                    extra={'internal_code': spec.internal_code, 'reason': exc.reason},
                )
                batch.unresolved.append(
                    UnresolvedCompetition(internal_code=spec.internal_code, reason=exc.reason)
                )
        return batch

    async def resolve_one(self, spec: CompetitionSpec) -> ResolvedCompetition:
        entries = await self._fetch_candidates(spec)
        ranked: list[tuple[CompetitionCandidate, ApiFootballLeagueEntry, float]] = []
        expected_names = [spec.expected_name, *spec.aliases]

        for entry in entries:
            actual_type = _provider_type(entry.league.type)
            country_matches = normalize_label(entry.country.name) == normalize_label(spec.country)
            type_matches = actual_type == spec.type
            name_similarity = _name_similarity(entry.league.name, expected_names)
            if not country_matches or not type_matches or actual_type is None:
                score = max(0.0, min(100.0, name_similarity * 70))
            else:
                available_years = {season.year for season in entry.seasons}
                season_coverage = len(available_years & TARGET_SEASONS) / len(TARGET_SEASONS)
                score = min(100.0, name_similarity * 70 + 15 + 10 + season_coverage * 5)
            candidate = CompetitionCandidate(
                api_league_id=entry.league.id,
                name=entry.league.name,
                country=entry.country.name,
                competition_type=actual_type or spec.type,
                score=round(score, 3),
                available_seasons=sorted({season.year for season in entry.seasons}),
            )
            ranked.append((candidate, entry, name_similarity))

        ranked.sort(key=lambda item: (-item[0].score, item[0].api_league_id))
        for candidate, _entry, _name_similarity_value in ranked[1:]:
            logger.info(
                'competition_resolution_alternative',
                extra={
                    'internal_code': spec.internal_code,
                    'api_league_id': candidate.api_league_id,
                    'candidate_name': candidate.name,
                    'candidate_country': candidate.country,
                    'candidate_type': candidate.competition_type,
                    'score': candidate.score,
                },
            )
        if not ranked:
            raise CompetitionResolutionError(spec.internal_code, 'no candidates returned')

        selected_candidate, selected_entry, selected_similarity = ranked[0]
        selected_type = _provider_type(selected_entry.league.type)
        if normalize_label(selected_entry.country.name) != normalize_label(spec.country):
            raise CompetitionResolutionError(spec.internal_code, 'no candidate matched the country')
        if selected_type != spec.type:
            raise CompetitionResolutionError(spec.internal_code, 'no candidate matched the type')
        if selected_similarity < self.minimum_name_similarity:
            raise CompetitionResolutionError(spec.internal_code, 'no candidate matched the name safely')
        if len(ranked) > 1:
            runner_up = ranked[1][0]
            if selected_candidate.score - runner_up.score < self.ambiguity_margin:
                raise CompetitionResolutionError(spec.internal_code, 'ambiguous provider candidates')

        alternatives = [candidate for candidate, _entry, _similarity in ranked[1:]]
        resolution = ResolvedCompetition(
            internal_code=spec.internal_code,
            api_league_id=selected_entry.league.id,
            name=selected_entry.league.name,
            country=selected_entry.country.name,
            competition_type=spec.type,
            logo_url=selected_entry.league.logo,
            match_score=selected_candidate.score,
            seasons=selected_entry.seasons,
            alternatives=alternatives,
        )
        logger.info(
            'competition_resolved',
            extra={
                'internal_code': spec.internal_code,
                'api_league_id': resolution.api_league_id,
                'competition_name': resolution.name,
                'country': resolution.country,
                'type': resolution.competition_type,
                'available_seasons': resolution.available_seasons,
            },
        )
        return resolution

    async def _fetch_candidates(self, spec: CompetitionSpec) -> list[ApiFootballLeagueEntry]:
        raw_candidates: dict[int, dict[str, Any]] = {}
        search_terms = list(dict.fromkeys([spec.expected_name, *spec.aliases]))
        # The provider rejects `search` and `country` when sent together. Search
        # by official name first and validate country/type locally; use the
        # country catalog only as a fallback.
        for search_term in search_terms:
            results = await self.client.leagues(search=search_term)
            for raw in results:
                league = raw.get('league') if isinstance(raw, dict) else None
                league_id = league.get('id') if isinstance(league, dict) else None
                if isinstance(league_id, int):
                    raw_candidates[league_id] = raw
            if self._has_safe_candidate(spec, raw_candidates.values()):
                break

        if not self._has_safe_candidate(spec, raw_candidates.values()):
            results = await self.client.leagues(country=spec.country)
            for raw in results:
                league = raw.get('league') if isinstance(raw, dict) else None
                league_id = league.get('id') if isinstance(league, dict) else None
                if isinstance(league_id, int):
                    raw_candidates[league_id] = raw

        entries: list[ApiFootballLeagueEntry] = []
        for raw in raw_candidates.values():
            try:
                entries.append(ApiFootballLeagueEntry.model_validate(raw))
            except ValidationError:
                logger.warning(
                    'competition_candidate_invalid',
                    extra={'internal_code': spec.internal_code},
                )
        return entries

    def _has_safe_candidate(
        self,
        spec: CompetitionSpec,
        candidates: Iterable[dict[str, Any]],
    ) -> bool:
        expected_names = [spec.expected_name, *spec.aliases]
        for raw in candidates:
            try:
                entry = ApiFootballLeagueEntry.model_validate(raw)
            except ValidationError:
                continue
            if normalize_label(entry.country.name) != normalize_label(spec.country):
                continue
            if _provider_type(entry.league.type) != spec.type:
                continue
            if _name_similarity(entry.league.name, expected_names) >= self.minimum_name_similarity:
                return True
        return False

    async def _persist(self, resolution: ResolvedCompetition) -> None:
        if self.resolution_sink is None:
            return
        if callable(self.resolution_sink):
            result = self.resolution_sink(resolution)
        else:
            result = self.resolution_sink.upsert_competition_resolution(resolution)
        if inspect.isawaitable(result):
            await result


__all__ = [
    'CompetitionResolutionError',
    'CompetitionResolutionSink',
    'CompetitionResolver',
    'load_competition_config',
    'normalize_label',
]
