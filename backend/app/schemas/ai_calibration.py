from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


MatchType = Literal['friendly', 'official']
Confidence = Literal['high', 'medium', 'low']
DataQuality = Literal['high', 'medium', 'low']
EvidenceKey = Literal[
    'fixture_metadata',
    'base_prediction',
    'model_metadata',
    'feature_snapshot',
    'team_history_summary',
    'team_statistics_summary',
    'lineup_snapshot',
    'injury_snapshot',
    'odds_snapshot',
]
AdjustmentFactor = Literal[
    'preparation',
    'relative_competition_strength',
    'confirmed_lineups',
    'expected_rotations',
    'home_travel_conditions',
    'confirmed_absences',
    'market_disagreement',
    'data_uncertainty',
]
Side = Literal['home', 'away', 'neither']
PreparationAdvantage = Literal['home', 'away', 'balanced']
ProjectionStatus = Literal['available', 'no_disponible']
BetMarket = Literal[
    'home_win',
    'draw',
    'away_win',
    'double_chance_home_draw',
    'double_chance_draw_away',
    'draw_no_bet_home',
    'draw_no_bet_away',
    'over_0_5',
    'under_0_5',
    'over_1_5',
    'under_1_5',
    'over_2_5',
    'under_2_5',
    'over_3_5',
    'under_3_5',
    'over_4_5',
    'under_4_5',
    'btts_yes',
    'btts_no',
    'no_bet',
]
BetConfidence = Literal['high', 'medium', 'low', 'no_bet']
CalibrationStatus = Literal['pending', 'unavailable', 'error', 'updated']
ForecastCategory = Literal[
    'goals',
    'corners',
    'half_goals',
    'cards',
    'shots',
    'saves',
    'shots_on_target',
]
CalibrationNoteKind = Literal[
    'adjustment',
    'market',
    'risk',
    'missing_data',
    'model_error',
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class ProbabilityBps(StrictModel):
    """Internal probability contract.

    Basis points avoid float drift and make the exact 100% invariant explicit
    before anything is persisted or exposed to the client.
    """

    home: int = Field(ge=0, le=10_000)
    draw: int = Field(ge=0, le=10_000)
    away: int = Field(ge=0, le=10_000)

    @model_validator(mode='after')
    def sums_to_one_hundred_percent(self) -> 'ProbabilityBps':
        if self.home + self.draw + self.away != 10_000:
            raise ValueError('1X2 probabilities must sum to exactly 10000 bps.')
        return self


class CalibrationAdjustment(StrictModel):
    factor: AdjustmentFactor
    benefited_side: Side
    impact_bps: int = Field(ge=-1_200, le=1_200)
    confidence: Confidence
    evidence_keys: list[EvidenceKey] = Field(min_length=1, max_length=5)


class CalibrationNote(StrictModel):
    """One short user-facing line produced by the model."""

    kind: CalibrationNoteKind
    text: Annotated[str, Field(min_length=1, max_length=160)]


class ProbableForecastPick(StrictModel):
    """A server-calculated prediction that the AI is not allowed to invent."""

    category: ForecastCategory
    title: Annotated[str, Field(min_length=1, max_length=80)]
    prediction: Annotated[str, Field(min_length=1, max_length=100)]
    probability: float | None = Field(default=None, ge=0, le=1)
    confidence: Confidence


class PreparationComparison(StrictModel):
    advantage: PreparationAdvantage
    explanation: str = Field(min_length=1, max_length=500)
    evidence_keys: list[EvidenceKey] = Field(min_length=1, max_length=5)


class TeamRotationEffect(StrictModel):
    estimated_performance_change_pct: float | None
    confidence: Confidence
    explanation: str = Field(min_length=1, max_length=500)
    evidence_keys: list[EvidenceKey] = Field(min_length=1, max_length=5)

    @model_validator(mode='after')
    def validate_estimate(self) -> 'TeamRotationEffect':
        value = self.estimated_performance_change_pct
        if value is not None and not -100.0 <= value <= 100.0:
            raise ValueError('Rotation estimate must be between -100 and 100 percent.')
        return self


class RotationEffect(StrictModel):
    home: TeamRotationEffect
    away: TeamRotationEffect


class ProjectionRange(StrictModel):
    status: ProjectionStatus
    min: float | None
    max: float | None
    evidence_keys: list[EvidenceKey] = Field(max_length=5)

    @model_validator(mode='after')
    def validate_range(self) -> 'ProjectionRange':
        if self.status == 'no_disponible':
            if self.min is not None or self.max is not None:
                raise ValueError('Unavailable projections cannot contain a range.')
            if self.evidence_keys:
                raise ValueError('Unavailable projections cannot cite evidence.')
            return self
        if self.min is None or self.max is None:
            raise ValueError('Available projections require both bounds.')
        if self.min < 0 or self.max < self.min or self.max > 100:
            raise ValueError('Projection bounds must be ordered between 0 and 100.')
        if not self.evidence_keys:
            raise ValueError('Available projections require evidence.')
        return self


class MetricProjections(StrictModel):
    home: ProjectionRange
    away: ProjectionRange
    total: ProjectionRange


class CalibrationProjections(StrictModel):
    goals: MetricProjections
    corners: MetricProjections
    shots: MetricProjections
    shots_on_target: MetricProjections


class BetRecommendation(StrictModel):
    market: BetMarket
    confidence: BetConfidence
    evidence_keys: list[EvidenceKey] = Field(max_length=5)

    @model_validator(mode='after')
    def validate_no_bet_contract(self) -> 'BetRecommendation':
        if self.market == 'no_bet' or self.confidence == 'no_bet':
            if self.market != 'no_bet' or self.confidence != 'no_bet':
                raise ValueError('no_bet market and confidence must be used together.')
        return self


class AICalibrationModelOutput(StrictModel):
    """Compact Structured Outputs schema sent to the OpenAI Responses API.

    Deterministic projections and verbose UI sections are deliberately absent:
    the backend derives them after validating this small calibration decision.
    """

    match_type: MatchType
    base_probabilities_bps: ProbabilityBps
    adjusted_probabilities_bps: ProbabilityBps
    adjustments: list[CalibrationAdjustment] = Field(max_length=5)
    recommended_market: BetRecommendation
    conservative_alternative: BetRecommendation
    notes: list[CalibrationNote] = Field(max_length=5)
    refresh_with_lineups: bool
    data_quality: DataQuality
    lineups_considered: bool


class PublicProbabilities(StrictModel):
    home: float = Field(ge=0, le=1)
    draw: float = Field(ge=0, le=1)
    away: float = Field(ge=0, le=1)

    @model_validator(mode='after')
    def sums_to_one(self) -> 'PublicProbabilities':
        if abs(self.home + self.draw + self.away - 1.0) > 0.000001:
            raise ValueError('Public 1X2 probabilities must sum to 1.')
        return self


class PublicAdjustment(StrictModel):
    factor: AdjustmentFactor
    benefited_side: Side
    impact_percentage_points: float = Field(ge=-12, le=12)
    confidence: Confidence
    evidence_keys: list[EvidenceKey]
    explanation: str


class PublicBetRecommendation(StrictModel):
    market: BetMarket
    minimum_value_odds: float | None
    confidence: BetConfidence
    estimated_edge_percentage_points: float | None
    justification: str
    evidence_keys: list[EvidenceKey]
    market_data_available: bool


class AICalibrationAnalysis(StrictModel):
    match_type: MatchType
    show_1x2: bool
    base_probabilities: PublicProbabilities
    adjusted_probabilities: PublicProbabilities
    adjustments: list[PublicAdjustment]
    preparation_comparison: PreparationComparison
    rotation_effect: RotationEffect
    projections: CalibrationProjections
    recommended_market: PublicBetRecommendation
    conservative_alternative: PublicBetRecommendation
    risks: list[str]
    missing_data: list[str]
    possible_model_errors: list[str]
    notes: list[CalibrationNote] = Field(default_factory=list, max_length=5)
    probable_forecast: list[ProbableForecastPick] = Field(
        default_factory=list,
        max_length=7,
    )
    forecast_finalized: bool = False
    refresh_with_lineups: bool
    data_quality: DataQuality
    lineups_considered: bool
    model_label: Literal['Calibración contextual IA']


class AICalibrationEnvelope(StrictModel):
    fixture_id: int = Field(gt=0)
    status: CalibrationStatus
    retry_after_seconds: int | None = Field(default=None, ge=0, le=86_400)
    reason_code: str | None = Field(default=None, max_length=100)
    safe_message: str | None = Field(default=None, max_length=500)
    is_stale: bool = False
    generated_at: str | None = None
    analysis: AICalibrationAnalysis | None = None

    @model_validator(mode='after')
    def validate_status_payload(self) -> 'AICalibrationEnvelope':
        if self.status == 'updated':
            if self.analysis is None or self.generated_at is None:
                raise ValueError('Updated calibration requires analysis and generated_at.')
        elif self.analysis is not None:
            raise ValueError('Only updated calibration may expose analysis.')
        return self
