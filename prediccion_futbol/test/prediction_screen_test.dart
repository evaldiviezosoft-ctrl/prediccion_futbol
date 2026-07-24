import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:prediccion_futbol/models/backend_health.dart';
import 'package:prediccion_futbol/models/fixture_summary.dart';
import 'package:prediccion_futbol/models/prediction.dart';
import 'package:prediccion_futbol/repositories/football_data_source.dart';
import 'package:prediccion_futbol/screens/prediction_screen.dart';

class _CountingRepository implements FootballDataSource {
  _CountingRepository([this.prediction]);

  final Prediction? prediction;
  int watchCalls = 0;

  @override
  Stream<Prediction?> watchPrediction(int fixtureId) {
    watchCalls += 1;
    return Stream<Prediction?>.value(prediction);
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

      await tester.scrollUntilVisible(find.text('Goles totales'), 300);
      expect(find.text('Más de 4.5 goles'), findsOneWidget);

      await tester.scrollUntilVisible(
        find.text('Estadísticas por equipo'),
        300,
      );
      expect(find.text('5.5'), findsOneWidget);
      expect(find.text('4.4'), findsNothing);
      expect(find.text('—'), findsWidgets);

      await tester.scrollUntilVisible(find.text('Goleadores probables'), 300);
      expect(
        find.text(
          'Aún no hay plantel o historial individual reciente suficiente.',
        ),
        findsAtLeastNWidgets(1),
      );
      await tester.scrollUntilVisible(find.text('Asistidores probables'), 300);
      expect(
        find.text(
          'Aún no hay plantel o historial individual reciente suficiente.',
        ),
        findsWidgets,
      );

      await tester.scrollUntilVisible(
        find.textContaining('no garantiza resultados'),
        300,
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
    'baseline muestra líneas y explica cuando faltan datos individuales',
    (tester) async {
      final repository = _CountingRepository(_baselinePrediction());

      await tester.pumpWidget(
        MaterialApp(
          home: PredictionScreen(fixture: _fixture(71), repository: repository),
        ),
      );
      await tester.pumpAndSettle();

      await tester.scrollUntilVisible(find.text('Goles totales'), 300);
      expect(find.text('Más de 4.5 goles'), findsOneWidget);

      await tester.scrollUntilVisible(find.text('Goleadores probables'), 300);
      expect(find.text('Córners'), findsOneWidget);
      expect(find.text('Remates'), findsOneWidget);
      expect(find.text('Goles'), findsNothing);
      expect(find.textContaining('Liga 1 de Perú'), findsOneWidget);

      await tester.scrollUntilVisible(find.text('Asistidores probables'), 300);
      expect(
        find.text(
          'Aún no hay plantel o historial individual reciente suficiente.',
        ),
        findsAtLeastNWidgets(1),
      );

      await tester.scrollUntilVisible(
        find.textContaining('Todas las estimaciones'),
        300,
      );
      expect(find.textContaining('no garantizan resultados'), findsOneWidget);
      expect(
        find.text('Calibración orientativa por competición'),
        findsNothing,
      );
      expect(find.text('Marcadores probables'), findsNothing);
    },
  );
}
