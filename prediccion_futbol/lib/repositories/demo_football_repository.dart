import '../models/ai_calibration.dart';
import '../models/backend_health.dart';
import '../models/fixture_summary.dart';
import '../models/prediction.dart';
import 'football_data_source.dart';

class DemoFootballRepository implements FootballDataSource {
  DemoFootballRepository() {
    final now = DateTime.now();
    fixtures = [
      FixtureSummary(
        id: 1001,
        homeTeam: 'Real Madrid',
        awayTeam: 'Valencia',
        homeTeamCountry: 'España',
        awayTeamCountry: 'España',
        kickoff: DateTime(now.year, now.month, now.day, 21),
        leagueId: 140,
        predictionAvailable: true,
        predictionStage: 'prematch',
      ),
      FixtureSummary(
        id: 1002,
        homeTeam: 'Arsenal',
        awayTeam: 'Chelsea',
        kickoff: now.add(const Duration(days: 1, hours: 3)),
        leagueId: 39,
        predictionAvailable: true,
        predictionStage: 'initial',
      ),
      FixtureSummary(
        id: 1003,
        homeTeam: 'Inter',
        awayTeam: 'Roma',
        kickoff: now.add(const Duration(days: 1, hours: 7)),
        leagueId: 135,
        predictionAvailable: true,
        predictionStage: 'prematch',
      ),
      FixtureSummary(
        id: 1004,
        homeTeam: 'Barcelona',
        awayTeam: 'Real Sociedad',
        kickoff: now.add(const Duration(days: 2, hours: 2)),
        leagueId: 140,
      ),
      FixtureSummary(
        id: 1005,
        homeTeam: 'Liverpool',
        awayTeam: 'Tottenham',
        kickoff: now.add(const Duration(days: 2, hours: 5)),
        leagueId: 39,
      ),
      FixtureSummary(
        id: 1006,
        homeTeam: 'Juventus',
        awayTeam: 'Atalanta',
        kickoff: now.add(const Duration(days: 3, hours: 8)),
        leagueId: 135,
      ),
    ];

    prediction = Prediction(
      fixtureId: 1001,
      homeTeam: 'Real Madrid',
      awayTeam: 'Valencia',
      homeTeamCountry: 'España',
      awayTeamCountry: 'España',
      stage: 'prematch',
      lineupsConfirmed: false,
      homeWin: 0.58,
      draw: 0.24,
      awayWin: 0.18,
      over25: 0.64,
      btts: 0.51,
      expected: const {
        'home_goals': 2.1,
        'away_goals': 0.9,
        'home_corners': 6.2,
        'away_corners': 3.8,
        'home_shots': 15.4,
        'away_shots': 9.7,
        'home_shots_on_target': 6.1,
        'away_shots_on_target': 3.2,
        'home_yellow_cards': 2.3,
        'away_yellow_cards': 2.8,
      },
      goalLines: const [
        GoalLineProbability(line: 0.5, probability: 0.91),
        GoalLineProbability(line: 1.5, probability: 0.76),
        GoalLineProbability(line: 2.5, probability: 0.64),
        GoalLineProbability(line: 3.5, probability: 0.39),
        GoalLineProbability(line: 4.5, probability: 0.19),
      ],
      possibleScorers: const [
        PossibleScorer(
          player: 'Vinícius Júnior',
          team: 'Real Madrid',
          probability: 0.42,
        ),
        PossibleScorer(
          player: 'Hugo Duro',
          team: 'Valencia',
          probability: 0.24,
        ),
      ],
      possibleAssistants: const [
        PossibleScorer(
          player: 'Jude Bellingham',
          team: 'Real Madrid',
          probability: 0.31,
        ),
      ],
      updatedAt: now.subtract(const Duration(minutes: 2)),
    );
    aiCalibration = AiCalibrationResult.fromJson({
      'fixture_id': 1001,
      'status': 'updated',
      'generated_at': now.toUtc().toIso8601String(),
      'is_stale': false,
      'analysis': {
        'match_type': 'official',
        'base_probabilities': {'home': .58, 'draw': .24, 'away': .18},
        'adjusted_probabilities': {'home': .55, 'draw': .25, 'away': .20},
        'probable_forecast': [
          {
            'category': 'goals',
            'title': 'Goles totales',
            'prediction': 'Más de 1.5',
            'probability': .76,
            'confidence': 'medium',
          },
          {
            'category': 'corners',
            'title': 'Córners',
            'prediction': 'Más de 7.5',
            'probability': .71,
            'confidence': 'medium',
          },
          {
            'category': 'half_goals',
            'title': 'Gol por tiempo',
            'prediction': 'Más de 0.5 · 2.º tiempo',
            'probability': .73,
            'confidence': 'low',
          },
          {
            'category': 'cards',
            'title': 'Tarjetas amarillas',
            'prediction': 'Más de 0.5 · Valencia',
            'probability': .88,
            'confidence': 'high',
          },
          {
            'category': 'shots',
            'title': 'Remates totales',
            'prediction': '19–30',
            'probability': null,
            'confidence': 'medium',
          },
          {
            'category': 'saves',
            'title': 'Atajadas totales',
            'prediction': '4–9',
            'probability': null,
            'confidence': 'medium',
          },
          {
            'category': 'shots_on_target',
            'title': 'Remates al arco',
            'prediction': '7–12',
            'probability': null,
            'confidence': 'medium',
          },
        ],
        'forecast_finalized': false,
        'notes': [
          {
            'kind': 'adjustment',
            'text':
                'La forma reciente reduce ligeramente la ventaja inicial del local.',
          },
          {
            'kind': 'market',
            'text':
                'Ambos equipos marcan conserva el mejor respaldo disponible.',
          },
          {
            'kind': 'risk',
            'text': 'Las alineaciones todavía no están confirmadas.',
          },
          {
            'kind': 'missing_data',
            'text': 'No hay cuotas recientes para medir valor de mercado.',
          },
        ],
        'adjustments': [
          {
            'factor': 'Forma y nivel de oposición',
            'detail':
                'La muestra reciente reduce ligeramente la ventaja inicial del local.',
            'evidence': 'Últimos partidos comparables disponibles',
          },
        ],
        'preparation_comparison': {
          'local': 'Carga estable y continuidad reciente',
          'visitante': 'Calendario con menor descanso',
        },
        'rotation_effect': {'resumen': 'Alineaciones aún no confirmadas'},
        'projections': {
          'goals': {
            'home': {'min': 1, 'max': 3},
            'away': {'min': 0, 'max': 2},
          },
          'corners': {
            'home': {'min': 5, 'max': 8},
            'away': {'min': 3, 'max': 5},
          },
        },
        'recommended_market': {
          'market': 'btts_yes',
          'confidence': 'medium',
          'justification':
              'Es el mercado con mayor respaldo del modelo y la calibración.',
          'market_data_available': false,
        },
        'conservative_alternative': {
          'market': 'Local o empate',
          'confidence': 'medium',
          'justification': 'Reduce la exposición ante un empate.',
          'market_data_available': false,
        },
        'risks': ['Alineaciones pendientes'],
        'missing_data': ['Cuotas recientes'],
        'possible_model_errors': ['Muestra reciente limitada'],
        'refresh_with_lineups': true,
        'data_quality': 'medium',
        'lineups_considered': false,
        'model_label': 'Calibración contextual IA',
      },
    });
  }

  late final List<FixtureSummary> fixtures;
  late final Prediction prediction;
  late final AiCalibrationResult aiCalibration;

  @override
  Future<BackendHealth> checkHealth() async =>
      const BackendHealth(live: true, ready: true, checks: {'demo': true});

  @override
  Future<List<FixtureSummary>> upcomingFixtures({int days = 14}) async =>
      fixtures;

  @override
  Stream<Prediction?> watchPrediction(int fixtureId) =>
      Stream<Prediction?>.value(
        fixtureId == prediction.fixtureId ? prediction : null,
      );

  @override
  Stream<AiCalibrationResult> watchAiCalibration(int fixtureId) =>
      Stream<AiCalibrationResult>.value(
        fixtureId == prediction.fixtureId
            ? aiCalibration
            : AiCalibrationResult(
                fixtureId: fixtureId,
                status: AiCalibrationStatus.unavailable,
                isStale: false,
                safeMessage: 'El análisis contextual no está disponible.',
              ),
      );

  @override
  void dispose() {}
}
