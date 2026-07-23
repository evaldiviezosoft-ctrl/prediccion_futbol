from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


CompetitionType = Literal['league', 'cup']
AvailabilityStatus = Literal['available', 'unavailable']


class CompetitionSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')

    internal_code: str = Field(pattern=r'^[a-z0-9_]+$')
    expected_name: str = Field(min_length=2)
    aliases: list[str] = Field(default_factory=list)
    country: str = Field(min_length=2)
    type: CompetitionType
    enabled: bool = True
    resolved_api_league_id: int | None = Field(default=None, gt=0)

    @field_validator('expected_name', 'country')
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('value cannot be blank')
        return value

    @field_validator('aliases')
    @classmethod
    def clean_aliases(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))


class CompetitionConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    competitions: list[CompetitionSpec]

    @field_validator('competitions')
    @classmethod
    def unique_internal_codes(cls, values: list[CompetitionSpec]) -> list[CompetitionSpec]:
        codes = [item.internal_code for item in values]
        if len(codes) != len(set(codes)):
            raise ValueError('competition internal_code values must be unique')
        return values


class ApiFootballPaging(BaseModel):
    model_config = ConfigDict(extra='ignore')

    current: int = Field(default=1, ge=1)
    total: int = Field(default=1, ge=0)


class ApiFootballEnvelope(BaseModel):
    model_config = ConfigDict(extra='allow')

    get: str | None = None
    parameters: dict[str, Any] | list[Any] = Field(default_factory=dict)
    errors: dict[str, Any] | list[Any] | str | None = Field(default_factory=dict)
    results: int = Field(default=0, ge=0)
    paging: ApiFootballPaging = Field(default_factory=ApiFootballPaging)
    response: list[dict[str, Any]] | dict[str, Any] | None = Field(default_factory=list)


class ApiFootballLeague(BaseModel):
    model_config = ConfigDict(extra='allow')

    id: int = Field(gt=0)
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    logo: str | None = None


class ApiFootballCountry(BaseModel):
    model_config = ConfigDict(extra='allow')

    name: str = Field(min_length=1)
    code: str | None = None
    flag: str | None = None


class ApiFootballSeason(BaseModel):
    model_config = ConfigDict(extra='allow')

    year: int = Field(ge=1900, le=2200)
    start: date | None = None
    end: date | None = None
    current: bool = False
    coverage: dict[str, Any] | None = None


class ApiFootballLeagueEntry(BaseModel):
    model_config = ConfigDict(extra='allow')

    league: ApiFootballLeague
    country: ApiFootballCountry
    seasons: list[ApiFootballSeason] = Field(default_factory=list)


class CompetitionCandidate(BaseModel):
    api_league_id: int
    name: str
    country: str
    competition_type: CompetitionType
    score: float = Field(ge=0, le=100)
    available_seasons: list[int] = Field(default_factory=list)


class ResolvedCompetition(BaseModel):
    internal_code: str
    api_league_id: int
    name: str
    country: str
    competition_type: CompetitionType
    logo_url: str | None = None
    match_score: float = Field(ge=0, le=100)
    seasons: list[ApiFootballSeason] = Field(default_factory=list)
    alternatives: list[CompetitionCandidate] = Field(default_factory=list)

    @property
    def available_seasons(self) -> list[int]:
        return sorted({season.year for season in self.seasons})

    def availability_for(self, season: int) -> AvailabilityStatus:
        return 'available' if season in self.available_seasons else 'unavailable'


class UnresolvedCompetition(BaseModel):
    internal_code: str
    reason: str


class CompetitionResolutionBatch(BaseModel):
    resolved: list[ResolvedCompetition] = Field(default_factory=list)
    unresolved: list[UnresolvedCompetition] = Field(default_factory=list)


class RateLimitSnapshot(BaseModel):
    daily_limit: int | None = Field(default=None, ge=0)
    daily_remaining: int | None = Field(default=None, ge=0)
    minute_limit: int | None = Field(default=None, ge=0)
    minute_remaining: int | None = Field(default=None, ge=0)
    requests_this_run: int = Field(default=0, ge=0)
    max_requests_per_run: int = Field(gt=0)
    daily_safety_reserve: int = Field(ge=0)
    can_continue: bool = True
    stop_reason: str | None = None


class ApiRequestLogRecord(BaseModel):
    endpoint: str
    parameters_json: dict[str, Any]
    requested_at: datetime
    response_status: int | None = Field(default=None, ge=100, le=599)
    results_count: int = Field(default=0, ge=0)
    daily_limit: int | None = Field(default=None, ge=0)
    daily_remaining: int | None = Field(default=None, ge=0)
    minute_limit: int | None = Field(default=None, ge=0)
    minute_remaining: int | None = Field(default=None, ge=0)
    duration_ms: int = Field(ge=0)
    error_message: str | None = None
    request_hash: str = Field(min_length=64, max_length=64)
