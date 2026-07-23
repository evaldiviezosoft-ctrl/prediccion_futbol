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

FixtureSummary _fixture(int leagueId) => FixtureSummary(
  id: 1,
  homeTeam: 'Local',
  awayTeam: 'Visitante',
  kickoff: DateTime.utc(2026, 7, 23),
  leagueId: leagueId,
);

Prediction _baselinePrediction() => Prediction.fromJson({
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
      expect(find.text('Marcadores probables'), findsNothing);
    },
  );
}
