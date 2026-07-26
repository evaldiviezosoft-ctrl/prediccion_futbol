import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:prediccion_futbol/models/ai_calibration.dart';
import 'package:prediccion_futbol/models/backend_health.dart';
import 'package:prediccion_futbol/models/fixture_summary.dart';
import 'package:prediccion_futbol/models/prediction.dart';
import 'package:prediccion_futbol/models/probable_forecast.dart';
import 'package:prediccion_futbol/repositories/football_data_source.dart';
import 'package:prediccion_futbol/screens/prediction_screen.dart';

class _CountingRepository implements FootballDataSource {
  _CountingRepository([this.prediction, this.aiCalibration]);

  final Prediction? prediction;
  final AiCalibrationResult? aiCalibration;
  int watchCalls = 0;
  int aiWatchCalls = 0;

  @override
  Stream<Prediction?> watchPrediction(int fixtureId) {
    watchCalls += 1;
    return Stream<Prediction?>.value(prediction);
  }

  @override
  Stream<AiCalibrationResult> watchAiCalibration(int fixtureId) {
    aiWatchCalls += 1;
    return Stream<AiCalibrationResult>.value(
      aiCalibration ??
          AiCalibrationResult(
            fixtureId: fixtureId,
            status: AiCalibrationStatus.unavailable,
            isStale: false,
            safeMessage: 'Análisis no disponible para la prueba.',
          ),
    );
  }

  @override
  Future<BackendHealth> checkHealth() => throw UnimplementedError();

  @override
  Future<List<FixtureSummary>> upcomingFixtures({int days = 14}) =>
      throw UnimplementedError();

  @override
  void dispose() {}
}

FixtureSummary _fixture(
  int leagueId, {
  bool predictionFallbackAvailable = false,
  String? homeTeamCountry,
  String? awayTeamCountry,
}) => FixtureSummary(
  id: 1,
  homeTeam: 'Local',
  awayTeam: 'Visitante',
  homeTeamCountry: homeTeamCountry,
  awayTeamCountry: awayTeamCountry,
  kickoff: DateTime.utc(2026, 7, 23),
  leagueId: leagueId,
  predictionFallbackAvailable: predictionFallbackAvailable,
);

Prediction _baselinePrediction({
  Map<String, dynamic>? crossLeagueCalibration,
  Map<String, dynamic> extraExpected = const {},
}) => Prediction.fromJson({
  'fixture_id': 1,
  'home_team_name': 'Local',
  'away_team_name': 'Visitante',
  'stage': 'prematch',
  'home_win_probability': 0.39,
  'draw_probability': 0.27,
  'away_win_probability': 0.34,
  'expected': {
    'home_goals': 1.2,
    'away_goals': 1.1,
    'home_corners': 6.3,
    'away_corners': 3.5,
    'home_shots': 15.3,
    'away_shots': 10.0,
    'home_shots_on_target': 5.5,
    'away_shots_on_target': 3.0,
    ...extraExpected,
  },
  'goal_lines': [
    {'line': 0.5, 'probability': 0.89},
    {'line': 1.5, 'probability': 0.67},
    {'line': 2.5, 'probability': 0.43},
    {'line': 3.5, 'probability': 0.22},
    {'line': 4.5, 'probability': 0.09},
  ],
  'possible_scorers': <Map<String, dynamic>>[],
  'possible_assistants': <Map<String, dynamic>>[],
  'probable_forecast': [
    {
      'category': 'goals',
      'title': 'Goles totales',
      'prediction': 'Más de 1.5',
      'probability': .67,
      'confidence': 'medium',
    },
    {
      'category': 'corners',
      'title': 'Córners',
      'prediction': 'Más de 7.5',
      'probability': .72,
      'confidence': 'medium',
    },
    {
      'category': 'shots',
      'title': 'Remates totales',
      'prediction': '19–31',
      'probability': null,
      'confidence': 'medium',
    },
    {
      'category': 'shots_on_target',
      'title': 'Remates al arco',
      'prediction': '6–11',
      'probability': null,
      'confidence': 'medium',
    },
  ],
  'updated_at': DateTime.now().toUtc().toIso8601String(),
  'model_metadata': {
    'model_type': 'statistical_baseline',
    'sample_sizes': {'home_team_home_matches': 4, 'away_team_away_matches': 8},
    'cross_league_calibration': ?crossLeagueCalibration,
    'market_statistics': {
      'reference_statistics_league_id': 281,
      'teams': {
        'home': {
          'metrics': {
            'corners': {'prior_rows': 41, 'cross_league_reference': true},
          },
        },
      },
    },
  },
});

Prediction _fallbackPrediction({bool singleTeamProfile = true}) =>
    Prediction.fromJson({
      'fixture_id': 1,
      'home_team_name': 'Local',
      'away_team_name': 'Visitante',
      'stage': 'orientative',
      'home_win_probability': 0.42,
      'draw_probability': 0.29,
      'away_win_probability': 0.29,
      'over25_probability': 0.41,
      'btts_probability': 0.46,
      'expected': {
        'home_goals': 1.2,
        'away_goals': 0.9,
        'home_corners': 5.5,
        'away_corners': 4.4,
        'home_shots': 13.2,
        'away_shots': 10.1,
        'home_shots_on_target': 4.8,
        'away_shots_on_target': 3.6,
      },
      'goal_lines': [
        {'line': 0.5, 'probability': 0.88},
        {'line': 1.5, 'probability': 0.62},
        {'line': 2.5, 'probability': 0.41},
        {'line': 3.5, 'probability': 0.2},
        {'line': 4.5, 'probability': 0.08},
      ],
      'possible_scorers': <Map<String, dynamic>>[],
      'possible_assistants': <Map<String, dynamic>>[],
      'updated_at': DateTime.now().toUtc().toIso8601String(),
      'model_metadata': {
        'model_type': 'calendar_fallback',
        'confidence': 'low',
        'single_team_profile': singleTeamProfile,
        'known_profile_sides': singleTeamProfile
            ? <String>['home']
            : <String>['home', 'away'],
      },
    });

AiCalibrationResult _aiResult(
  AiCalibrationStatus status, {
  bool noBet = false,
  bool compact = false,
}) {
  if (status != AiCalibrationStatus.updated) {
    return AiCalibrationResult(
      fixtureId: 1,
      status: status,
      isStale: false,
      safeMessage: status == AiCalibrationStatus.pending
          ? 'Calibración en cola.'
          : 'No fue posible completar el análisis.',
    );
  }
  return AiCalibrationResult(
    fixtureId: 1,
    status: status,
    generatedAt: DateTime.now(),
    isStale: false,
    analysis: AiCalibrationAnalysis(
      matchType: 'official',
      baseProbabilities: const AiProbabilityTriplet(
        home: .39,
        draw: .27,
        away: .34,
      ),
      adjustedProbabilities: const AiProbabilityTriplet(
        home: .36,
        draw: .29,
        away: .35,
      ),
      adjustments: const [
        AiAdjustment(
          factor: 'preparation',
          detail: 'El visitante llega con mejor continuidad.',
          evidence: 'team_history_summary',
          benefitedSide: 'away',
          impactPercentagePoints: 2,
        ),
      ],
      preparationComparison: const [
        AiContextDetail(label: 'Ventaja', value: 'Visitante'),
      ],
      rotationEffect: const [
        AiContextDetail(label: 'Local', value: 'Rotación posible'),
      ],
      projections: const [
        AiProjection(
          metric: 'Goles',
          home: AiProjectionRange(minimum: 0, maximum: 2),
          away: AiProjectionRange(minimum: 1, maximum: 2),
          total: AiProjectionRange(minimum: 1, maximum: 4),
        ),
      ],
      recommendedMarket: AiMarketRecommendation(
        market: noBet ? 'no_bet' : 'over_1_5',
        confidence: noBet ? 'no_bet' : 'medium',
        justification: noBet
            ? 'La evidencia no permite recomendar un mercado.'
            : 'Es el mercado con mayor respaldo.',
        marketDataAvailable: false,
        minimumValueOdds: noBet ? null : 1.48,
      ),
      conservativeAlternative: null,
      risks: const ['Alineaciones pendientes'],
      missingData: const ['Cuotas recientes'],
      possibleModelErrors: const ['Muestra reducida'],
      refreshWithLineups: true,
      dataQuality: 'medium',
      lineupsConsidered: false,
      modelLabel: 'Calibración contextual IA',
      probableForecast: const [
        ProbableForecastPick(
          category: 'goals',
          title: 'Goles totales',
          prediction: 'Más de 1.5',
          probability: .71,
          confidence: 'medium',
        ),
        ProbableForecastPick(
          category: 'corners',
          title: 'Córners',
          prediction: 'Más de 7.5',
          probability: .69,
          confidence: 'medium',
        ),
        ProbableForecastPick(
          category: 'half_goals',
          title: 'Gol por tiempo',
          prediction: 'Más de 0.5 · 2.º tiempo',
          probability: .73,
          confidence: 'low',
        ),
        ProbableForecastPick(
          category: 'cards',
          title: 'Tarjetas amarillas',
          prediction: 'Más de 0.5 · Local',
          probability: .86,
          confidence: 'high',
        ),
        ProbableForecastPick(
          category: 'shots',
          title: 'Remates totales',
          prediction: '19–31',
          confidence: 'medium',
        ),
        ProbableForecastPick(
          category: 'saves',
          title: 'Atajadas totales',
          prediction: '4–9',
          confidence: 'medium',
        ),
        ProbableForecastPick(
          category: 'shots_on_target',
          title: 'Remates al arco',
          prediction: '6–11',
          confidence: 'medium',
        ),
      ],
      notes: compact
          ? const [
              AiCalibrationNote(
                kind: 'adjustment',
                text: 'El contexto reduce la ventaja inicial del local.',
              ),
              AiCalibrationNote(
                kind: 'market',
                text: 'El mercado de goles tiene el mayor respaldo.',
              ),
              AiCalibrationNote(
                kind: 'risk',
                text: 'Las alineaciones siguen pendientes.',
              ),
              AiCalibrationNote(
                kind: 'missing_data',
                text: 'No hay cuotas recientes.',
              ),
              AiCalibrationNote(
                kind: 'model_error',
                text: 'La muestra reciente es limitada.',
              ),
              AiCalibrationNote(
                kind: 'risk',
                text: 'Esta sexta nota no debe mostrarse.',
              ),
            ]
          : null,
    ),
  );
}

void main() {
  testWidgets('una liga sin modelo no abre una suscripción', (tester) async {
    final repository = _CountingRepository();

    await tester.pumpWidget(
      MaterialApp(
        home: PredictionScreen(fixture: _fixture(999), repository: repository),
      ),
    );

    expect(find.text('Modelo aún no disponible'), findsOneWidget);
    expect(repository.watchCalls, 0);
  });

  testWidgets(
    'un fallback consulta la predicción y advierte que es orientativa',
    (tester) async {
      final repository = _CountingRepository(_fallbackPrediction());

      await tester.pumpWidget(
        MaterialApp(
          home: PredictionScreen(
            fixture: _fixture(667, predictionFallbackAvailable: true),
            repository: repository,
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(repository.watchCalls, 1);
      expect(find.text('Predicción orientativa'), findsOneWidget);
      expect(
        find.textContaining(
          'solo uno de los equipos tiene historial; no publicamos 1X2',
        ),
        findsOneWidget,
      );
      expect(find.text('Predicción 1X2 (probabilidad)'), findsNothing);

      await tester.scrollUntilVisible(find.text('Más de 4.5 goles'), 300);
      expect(find.text('Más de 4.5 goles'), findsOneWidget);

      await tester.scrollUntilVisible(
        find.text('Estadísticas por equipo'),
        300,
      );
      expect(find.text('5.5'), findsOneWidget);
      expect(find.text('4.4'), findsNothing);
      expect(find.text('—'), findsWidgets);

      await tester.scrollUntilVisible(
        find.textContaining('no garantiza resultados'),
        300,
      );
      expect(find.text('Goleadores probables'), findsNothing);
      expect(find.text('Asistidores probables'), findsNothing);
      expect(
        find.text(
          'Aún no hay plantel o historial individual reciente suficiente.',
        ),
        findsNothing,
      );
      expect(
        find.text('Predicción orientativa • baja confianza'),
        findsOneWidget,
      );
    },
  );

  testWidgets('un fallback con ambos perfiles sí muestra 1X2', (tester) async {
    final repository = _CountingRepository(
      _fallbackPrediction(singleTeamProfile: false),
    );

    await tester.pumpWidget(
      MaterialApp(
        home: PredictionScreen(
          fixture: _fixture(667, predictionFallbackAvailable: true),
          repository: repository,
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.scrollUntilVisible(
      find.text('Predicción 1X2 (probabilidad)'),
      300,
    );
    expect(find.text('Predicción 1X2 (probabilidad)'), findsOneWidget);
    expect(
      find.textContaining('solo uno de los equipos tiene historial'),
      findsNothing,
    );
  });

  testWidgets('una liga modelada consulta la predicción', (tester) async {
    final repository = _CountingRepository();

    await tester.pumpWidget(
      MaterialApp(
        home: PredictionScreen(fixture: _fixture(39), repository: repository),
      ),
    );
    await tester.pump();

    expect(repository.watchCalls, 1);
    expect(find.text('Predicción en preparación'), findsOneWidget);
  });

  testWidgets('la cabecera muestra el país debajo de cada club', (
    tester,
  ) async {
    final repository = _CountingRepository(_baselinePrediction());

    await tester.pumpWidget(
      MaterialApp(
        home: PredictionScreen(
          fixture: _fixture(
            39,
            homeTeamCountry: 'Spain',
            awayTeamCountry: 'Gibraltar',
          ),
          repository: repository,
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Spain'), findsOneWidget);
    expect(find.text('Gibraltar'), findsOneWidget);
  });

  testWidgets('el pie explica la calibración aplicada entre competiciones', (
    tester,
  ) async {
    final repository = _CountingRepository(
      _baselinePrediction(
        crossLeagueCalibration: {
          'applied': true,
          'home_source': {'competition_name': 'Eliteserien', 'factor': 0.91},
          'away_source': {'competition_name': 'Premier League', 'factor': 1.08},
        },
      ),
    );

    await tester.pumpWidget(
      MaterialApp(
        home: PredictionScreen(
          fixture: _fixture(667, predictionFallbackAvailable: true),
          repository: repository,
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.scrollUntilVisible(
      find.text('Calibración orientativa por competición'),
      300,
    );
    expect(
      find.text('Calibración orientativa por competición'),
      findsOneWidget,
    );
    expect(
      find.textContaining('Eliteserien 0.91 · Premier League 1.08'),
      findsOneWidget,
    );
    expect(
      find.textContaining('factores de contexto, no probabilidades'),
      findsOneWidget,
    );
  });

  testWidgets('una liga sudamericana con baseline consulta la predicción', (
    tester,
  ) async {
    final repository = _CountingRepository();

    await tester.pumpWidget(
      MaterialApp(
        home: PredictionScreen(fixture: _fixture(71), repository: repository),
      ),
    );
    await tester.pump();

    expect(repository.watchCalls, 1);
    expect(find.text('Predicción en preparación'), findsOneWidget);
  });

  testWidgets(
    'baseline muestra líneas y oculta por completo datos individuales vacíos',
    (tester) async {
      final repository = _CountingRepository(_baselinePrediction());

      await tester.pumpWidget(
        MaterialApp(
          home: PredictionScreen(fixture: _fixture(71), repository: repository),
        ),
      );
      await tester.pumpAndSettle();

      await tester.scrollUntilVisible(find.text('Más de 4.5 goles'), 300);
      expect(find.text('Más de 4.5 goles'), findsOneWidget);

      await tester.scrollUntilVisible(
        find.textContaining('Todas las estimaciones'),
        300,
      );
      expect(find.text('Córners'), findsAtLeastNWidgets(1));
      expect(find.text('Remates'), findsOneWidget);
      expect(find.text('Goles'), findsNothing);
      expect(find.textContaining('Liga 1 de Perú'), findsOneWidget);
      expect(find.text('Goleadores probables'), findsNothing);
      expect(find.text('Asistidores probables'), findsNothing);
      expect(find.textContaining('no garantizan resultados'), findsOneWidget);
      expect(
        find.text('Calibración orientativa por competición'),
        findsNothing,
      );
      expect(find.text('Marcadores probables'), findsNothing);
    },
  );

  testWidgets(
    'muestra tarjetas amarillas solo con el par y aclara su referencia',
    (tester) async {
      final repository = _CountingRepository(
        _baselinePrediction(
          extraExpected: const {
            'home_yellow_cards': 2.4,
            'away_yellow_cards': 3.1,
          },
        ),
      );

      await tester.pumpWidget(
        MaterialApp(
          home: PredictionScreen(fixture: _fixture(71), repository: repository),
        ),
      );
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(find.text('Tarjetas amarillas'), 300);

      expect(find.text('Tarjetas amarillas'), findsOneWidget);
      expect(find.text('2.4'), findsOneWidget);
      expect(find.text('3.1'), findsOneWidget);
      expect(
        find.text(
          'Referencia histórica; se actualizará con los partidos recientes '
          'de la liga.',
        ),
        findsOneWidget,
      );
    },
  );

  testWidgets('no muestra tarjetas amarillas cuando falta un valor del par', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: PredictionScreen(
          fixture: _fixture(71),
          repository: _CountingRepository(
            _baselinePrediction(
              extraExpected: const {'home_yellow_cards': 2.4},
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
      find.textContaining('Todas las estimaciones'),
      300,
    );

    expect(find.text('Tarjetas amarillas'), findsNothing);
    expect(
      find.textContaining('Referencia histórica; se actualizará'),
      findsNothing,
    );
  });

  testWidgets('pending no oculta la predicción estadística base', (
    tester,
  ) async {
    final repository = _CountingRepository(
      _baselinePrediction(),
      _aiResult(AiCalibrationStatus.pending),
    );

    await tester.pumpWidget(
      MaterialApp(
        home: PredictionScreen(fixture: _fixture(71), repository: repository),
      ),
    );
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 20));

    expect(find.text('Predicción 1X2 (probabilidad)'), findsOneWidget);
    await tester.scrollUntilVisible(
      find.byKey(const ValueKey('probable-forecast-card')),
      300,
    );
    expect(find.text('Pronóstico probable'), findsOneWidget);
    expect(find.text('Más de 1.5'), findsOneWidget);
    expect(find.textContaining('Se mantiene fijo'), findsOneWidget);
    expect(find.text('39%'), findsAtLeastNWidgets(1));
  });

  testWidgets('muestra únicamente el pronóstico probable estructurado', (
    tester,
  ) async {
    final repository = _CountingRepository(
      _baselinePrediction(),
      _aiResult(AiCalibrationStatus.updated),
    );

    await tester.pumpWidget(
      MaterialApp(
        home: PredictionScreen(fixture: _fixture(71), repository: repository),
      ),
    );
    await tester.pumpAndSettle();

    await tester.scrollUntilVisible(
      find.byKey(const ValueKey('probable-forecast-card')),
      300,
    );
    expect(
      find.byKey(const ValueKey('ai-calibration-updated')),
      findsOneWidget,
    );
    expect(find.text('Más de 1.5'), findsOneWidget);
    expect(find.text('Más de 7.5'), findsOneWidget);
    expect(find.text('19–31'), findsOneWidget);
    expect(find.text('4–9'), findsOneWidget);
    expect(find.text('Modelo base vs. calibración'), findsNothing);
    expect(find.text('Factores y evidencia'), findsNothing);
    expect(find.text('Riesgos'), findsNothing);
    expect(find.text('Datos faltantes'), findsNothing);
    expect(find.text('Posibles límites del modelo'), findsNothing);
  });

  testWidgets('mantiene la suscripción IA al bajar y volver a subir', (
    tester,
  ) async {
    final repository = _CountingRepository(
      _baselinePrediction(),
      _aiResult(AiCalibrationStatus.updated),
    );

    await tester.pumpWidget(
      MaterialApp(
        home: PredictionScreen(fixture: _fixture(71), repository: repository),
      ),
    );
    await tester.pumpAndSettle();

    final list = find.byType(ListView);
    for (var index = 0; index < 12; index++) {
      await tester.drag(list, const Offset(0, -500));
      await tester.pump();
    }
    for (var index = 0; index < 12; index++) {
      await tester.drag(list, const Offset(0, 500));
      await tester.pump();
    }
    await tester.pumpAndSettle();

    expect(tester.takeException(), isNull);
    expect(
      find.byKey(const ValueKey('ai-calibration-updated')),
      findsOneWidget,
    );
    expect(repository.aiWatchCalls, 1);
  });

  testWidgets(
    'notas compactas reemplazan bloques narrativos legados y se limitan',
    (tester) async {
      final repository = _CountingRepository(
        _baselinePrediction(),
        _aiResult(AiCalibrationStatus.updated, compact: true),
      );

      await tester.pumpWidget(
        MaterialApp(
          home: PredictionScreen(fixture: _fixture(71), repository: repository),
        ),
      );
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(
        find.byKey(const ValueKey('probable-forecast-card')),
        300,
      );

      expect(find.text('Factores y evidencia'), findsNothing);
      expect(find.text('Comparación de preparación'), findsNothing);
      expect(find.text('Efecto de rotaciones'), findsNothing);
      expect(find.text('Proyecciones estadísticas'), findsNothing);
      final notes = tester.widget<Text>(
        find.byKey(const ValueKey('probable-forecast-explanation')),
      );
      expect(notes.maxLines, 5);
      expect(notes.overflow, TextOverflow.ellipsis);
      expect(notes.data, contains('El contexto reduce'));
      expect(notes.data, contains('La muestra reciente'));
      expect(notes.data, isNot(contains('sexta nota')));
      expect(find.text('Es el mercado con mayor respaldo.'), findsNothing);
    },
  );

  testWidgets('IA no compara 1X2 cuando solo existe un perfil histórico', (
    tester,
  ) async {
    final repository = _CountingRepository(
      _fallbackPrediction(singleTeamProfile: true),
      _aiResult(AiCalibrationStatus.updated),
    );

    await tester.pumpWidget(
      MaterialApp(
        home: PredictionScreen(
          fixture: _fixture(667, predictionFallbackAvailable: true),
          repository: repository,
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.scrollUntilVisible(
      find.byKey(const ValueKey('probable-forecast-card')),
      300,
    );
    expect(find.text('Modelo base vs. calibración'), findsNothing);
    expect(find.textContaining('No comparamos 1X2'), findsNothing);
  });

  testWidgets(
    'un error de IA conserva la base y ofrece reintento independiente',
    (tester) async {
      final repository = _CountingRepository(
        _baselinePrediction(),
        _aiResult(AiCalibrationStatus.error),
      );

      await tester.pumpWidget(
        MaterialApp(
          home: PredictionScreen(fixture: _fixture(71), repository: repository),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('Predicción 1X2 (probabilidad)'), findsOneWidget);
      await tester.scrollUntilVisible(
        find.byKey(const ValueKey('probable-forecast-card')),
        300,
      );
      expect(find.text('Más de 1.5'), findsOneWidget);
      expect(find.text('No pudimos completar la calibración'), findsNothing);
    },
  );

  testWidgets('no_bet se presenta sin prometer una apuesta segura', (
    tester,
  ) async {
    final repository = _CountingRepository(
      _baselinePrediction(),
      _aiResult(AiCalibrationStatus.updated, noBet: true),
    );

    await tester.pumpWidget(
      MaterialApp(
        home: PredictionScreen(fixture: _fixture(71), repository: repository),
      ),
    );
    await tester.pumpAndSettle();

    await tester.scrollUntilVisible(
      find.byKey(const ValueKey('probable-forecast-card')),
      300,
    );
    expect(find.text('Más de 1.5'), findsOneWidget);
    expect(find.text('No hay una selección recomendable'), findsNothing);
    expect(find.textContaining('apuesta segura'), findsNothing);
  });
}
