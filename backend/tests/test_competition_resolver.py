import asyncio
from pathlib import Path

import pytest

from app.core.config import Settings
from app.schemas.api_football import CompetitionSpec
from app.services.competition_resolver import (
    CompetitionResolutionError,
    CompetitionResolver,
    load_competition_config,
)


CONFIG_PATH = Path(__file__).resolve().parents[1] / 'app' / 'config' / 'competitions.yaml'


def _league(league_id, name, country, competition_type, years):
    return {
        'league': {
            'id': league_id,
            'name': name,
            'type': competition_type,
            'logo': f'https://img.example/{league_id}.png',
        },
        'country': {'name': country, 'code': None, 'flag': None},
        'seasons': [
            {
                'year': year,
                'start': f'{year}-01-01',
                'end': f'{year}-12-31',
                'current': year == max(years),
                'coverage': {'fixtures': {'statistics_fixtures': True}},
            }
            for year in years
        ],
    }


class FakeClient:
    def __init__(self, candidates):
        self.candidates = candidates
        self.calls = []

    async def leagues(self, *, search=None, country=None, season=None):
        self.calls.append({'search': search, 'country': country, 'season': season})
        return self.candidates


def _settings():
    return Settings(
        _env_file=None,
        api_football_key='valid_api_key_0123456789',
        competitions_config_path=CONFIG_PATH,
    )


def test_yaml_contains_unique_unresolved_competitions():
    config = load_competition_config(CONFIG_PATH)

    internal_codes = {item.internal_code for item in config.competitions}
    assert len(internal_codes) == len(config.competitions)
    assert {
        'peru_liga_1',
        'uefa_europa_league',
        'friendlies_clubs',
    } <= internal_codes
    assert all(item.resolved_api_league_id is None for item in config.competitions)
    peru = next(item for item in config.competitions if item.internal_code == 'peru_liga_1')
    assert 'Liga 1' in peru.aliases
    assert 'Primera División' in peru.aliases
    europa = next(
        item for item in config.competitions if item.internal_code == 'uefa_europa_league'
    )
    assert europa.expected_name == 'UEFA Europa League'
    friendlies = next(
        item for item in config.competitions if item.internal_code == 'friendlies_clubs'
    )
    assert friendlies.expected_name == 'Friendlies Clubs'


def test_empty_competition_config_is_rejected(tmp_path):
    config_path = tmp_path / 'competitions.yaml'
    config_path.write_text('competitions: []\n', encoding='utf-8')

    with pytest.raises(CompetitionResolutionError, match='at least one competition'):
        load_competition_config(config_path)


def test_resolver_selects_safe_name_country_and_type_match_and_persists_it():
    candidates = [
        _league(900, 'Serie A', 'Brazil', 'League', [2025, 2026]),
        _league(901, 'Serie A', 'Italy', 'Cup', [2025, 2026]),
        _league(902, 'Serie A', 'Italy', 'League', [2021, 2022, 2023, 2024, 2025, 2026]),
    ]
    client = FakeClient(candidates)
    persisted = []
    resolver = CompetitionResolver(
        client,
        settings=_settings(),
        resolution_sink=persisted.append,
    )
    spec = CompetitionSpec(
        internal_code='serie_a_italy',
        expected_name='Serie A',
        country='Italy',
        type='league',
        enabled=True,
        resolved_api_league_id=None,
    )

    async def exercise():
        resolution = await resolver.resolve_one(spec)
        await resolver._persist(resolution)
        return resolution

    resolution = asyncio.run(exercise())
    assert resolution.api_league_id == 902
    assert resolution.available_seasons == [2021, 2022, 2023, 2024, 2025, 2026]
    assert len(persisted) == 1
    assert client.calls == [{'search': 'Serie A', 'country': None, 'season': None}]


def test_missing_season_is_reported_unavailable_without_failing_resolution():
    client = FakeClient([_league(700, 'Primera División', 'Peru', 'League', [2023, 2024])])
    resolver = CompetitionResolver(client, settings=_settings())
    spec = CompetitionSpec(
        internal_code='peru_liga_1',
        expected_name='Primera Division',
        aliases=['Primera División', 'Liga 1'],
        country='Peru',
        type='league',
    )

    resolution = asyncio.run(resolver.resolve_one(spec))

    assert resolution.availability_for(2024) == 'available'
    assert resolution.availability_for(2026) == 'unavailable'


def test_resolver_tries_peru_alias_when_first_search_is_not_a_safe_match():
    class AliasClient:
        def __init__(self):
            self.searches = []

        async def leagues(self, *, search=None, country=None, season=None):
            self.searches.append(search)
            if search == 'Liga 1':
                return [_league(700, 'Primera División', 'Peru', 'League', [2026])]
            return [_league(701, 'Liga 1 Femenina', 'Peru', 'League', [2026])]

    client = AliasClient()
    resolver = CompetitionResolver(client, settings=_settings())
    spec = CompetitionSpec(
        internal_code='peru_liga_1',
        expected_name='Peruvian Primera Division',
        aliases=['Liga 1', 'Primera División'],
        country='Peru',
        type='league',
    )

    resolution = asyncio.run(resolver.resolve_one(spec))

    assert resolution.api_league_id == 700
    assert client.searches == ['Peruvian Primera Division', 'Liga 1']


def test_resolve_all_keeps_processing_when_one_competition_is_unresolved(tmp_path):
    config_path = tmp_path / 'competitions.yaml'
    entries = [
        {
            'internal_code': f'league_{index}',
            'expected_name': f'League {index}',
            'country': 'Country',
            'type': 'league',
            'enabled': True,
            'resolved_api_league_id': None,
        }
        for index in range(10)
    ]
    import yaml

    config_path.write_text(yaml.safe_dump({'competitions': entries}), encoding='utf-8')

    class SelectiveClient:
        async def leagues(self, *, search=None, country=None, season=None):
            if search is None:
                return []
            if search == 'League 4':
                return []
            index = int(search.split()[-1])
            return [_league(1000 + index, search, 'Country', 'League', [2026])]

    resolver = CompetitionResolver(
        SelectiveClient(),
        settings=_settings(),
        config_path=config_path,
    )
    batch = asyncio.run(resolver.resolve_all())

    assert len(batch.resolved) == 9
    assert [item.internal_code for item in batch.unresolved] == ['league_4']
