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

FixtureSummary _fixture({
  int leagueId = 71,
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

Map<String, dynamic> _market({
  required String category,
  required String title,
  required double expectedTotal,
  required List<double> lines,
  required List<double> overProbabilities,
  String confidence = 'medium',
}) => {
  'category': category,
  'title': title,
  'scope': 'match_total',
  'expected_total': expectedTotal,
  'confidence': confidence,
  'lines': [
    for (var index = 0; index < lines.length; index++)
      _line(lines[index], overProbabilities[index]),
  ],
};

Map<String, dynamic> _line(double line, double overProbability) {
  final underProbability = 1 - overProbability;
  final selection = overProbability >= .6
      ? 'over'
      : underProbability >= .6
      ? 'under'
      : 'none';
  return {
    'line': line,
    'over_probability': overProbability,
    'under_probability': underProbability,
    'selection': selection,
    'selection_probability': switch (selection) {
      'over' => overProbability,
      'under' => underProbability,
      _ => null,
    },
  };
}

Prediction _prediction({bool singleTeamFallback = false}) =>
    Prediction.fromJson({
      'fixture_id': 1,
      'home_team_name': 'Local',
      'away_team_name': 'Visitante',
      'home_team_country': 'Perú',
      'away_team_country': 'Brasil',
      'stage': 'prematch',
      'lineups_confirmed': false,
      'home_win_probability': .39,
      'draw_probability': .27,
      'away_win_probability': .34,
      'expected': {
        'home_goals': 1.2,
        'away_goals': 1.1,
        'home_corners': 6.3,
        'away_corners': 3.5,
        'home_shots': 15.3,
        'away_shots': 10,
        'home_shots_on_target': 5.5,
        'away_shots_on_target': 3,
        'home_yellow_cards': 2.4,
        'away_yellow_cards': 3.1,
      },
      'goal_lines': [
        {'line': .5, 'probability': .89},
        {'line': 1.5, 'probability': .67},
        {'line': 2.5, 'probability': .43},
        {'line': 3.5, 'probability': .22},
        {'line': 4.5, 'probability': .09},
      ],
      'possible_scorers': <Map<String, dynamic>>[],
      'possible_assistants': <Map<String, dynamic>>[],
      'market_forecast': {
        'version': '1.0',
        'method': 'poisson_empirical',
        'markets': [
          _market(
            category: 'goals',
            title: 'Goles',
            expectedTotal: 2.3,
            lines: [.5, 1.5, 2.5, 3.5, 4.5],
            overProbabilities: [.90, .68, .52, .24, .09],
          ),
          _market(
            category: 'corners',
            title: 'Córners',
            expectedTotal: 9.8,
            lines: [6.5, 7.5, 8.5, 9.5, 10.5],
            overProbabilities: [.82, .73, .63, .52, .41],
          ),
          _market(
            category: 'shots',
            title: 'Remates',
            expectedTotal: 25.3,
            lines: [19.5, 21.5, 23.5, 25.5, 27.5],
            overProbabilities: [.83, .74, .62, .49, .36],
          ),
          _market(
            category: 'yellow_cards',
            title: 'Tarjetas amarillas',
            expectedTotal: 5.5,
            lines: [1.5, 2.5, 3.5, 4.5, 5.5],
            overProbabilities: [.95, .88, .76, .64, .49],
          ),
          _market(
            category: 'shots_on_target',
            title: 'Remates al arco',
            expectedTotal: 8.5,
            lines: [4.5, 5.5, 6.5, 7.5, 8.5],
            overProbabilities: [.88, .79, .68, .57, .46],
          ),
        ],
      },
      'updated_at': DateTime.now().toUtc().toIso8601String(),
      'model_metadata': {
        'model_type': singleTeamFallback
            ? 'calendar_fallback'
            : 'statistical_baseline',
        'confidence': singleTeamFallback ? 'low' : 'medium',
        'single_team_profile': singleTeamFallback,
        'known_profile_sides': singleTeamFallback
            ? <String>['home']
            : <String>['home', 'away'],
        'sample_sizes': {
          'home_team_home_matches': 12,
          'away_team_away_matches': 11,
        },
      },
    });

Future<void> _pumpPrediction(
  WidgetTester tester,
  _CountingRepository repository, {
  FixtureSummary? fixture,
}) async {
  await tester.pumpWidget(
    MaterialApp(
      home: PredictionScreen(
        fixture: fixture ?? _fixture(),
        repository: repository,
      ),
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('una liga sin modelo no abre una suscripción', (tester) async {
    final repository = _CountingRepository();

    await _pumpPrediction(tester, repository, fixture: _fixture(leagueId: 999));

    expect(find.text('Modelo aún no disponible'), findsOneWidget);
    expect(repository.watchCalls, 0);
  });

  testWidgets('una predicción usa una sola suscripción del backend', (
    tester,
  ) async {
    final repository = _CountingRepository(_prediction());

    await _pumpPrediction(tester, repository);

    expect(repository.watchCalls, 1);
    expect(find.text('Predicción 1X2 (probabilidad)'), findsOneWidget);
    expect(find.textContaining('Calibración IA'), findsNothing);
  });

  testWidgets('la cabecera muestra el país debajo de cada club', (
    tester,
  ) async {
    await _pumpPrediction(
      tester,
      _CountingRepository(_prediction()),
      fixture: _fixture(homeTeamCountry: 'Perú', awayTeamCountry: 'Brasil'),
    );

    expect(find.text('Perú'), findsOneWidget);
    expect(find.text('Brasil'), findsOneWidget);
  });

  testWidgets('goles muestra más, menos y ausencia de señal', (tester) async {
    await _pumpPrediction(tester, _CountingRepository(_prediction()));

    await tester.scrollUntilVisible(
      find.byKey(const ValueKey('market-goals')),
      300,
    );

    expect(find.text('Más de 0.5'), findsOneWidget);
    expect(find.text('90%'), findsOneWidget);
    expect(find.text('Línea 2.5'), findsOneWidget);
    expect(find.text('Sin señal clara'), findsAtLeastNWidgets(1));
    expect(find.text('Menos de 3.5'), findsOneWidget);
    expect(find.text('76%'), findsAtLeastNWidgets(1));
    expect(find.byKey(const ValueKey('market-line-goals-4.5')), findsOneWidget);
  });

  testWidgets(
    'después de estadísticas por equipo muestra cinco líneas por total',
    (tester) async {
      await _pumpPrediction(tester, _CountingRepository(_prediction()));

      await tester.scrollUntilVisible(
        find.text('Estadísticas por equipo'),
        300,
      );
      final statisticsOffset = tester
          .state<ScrollableState>(find.byType(Scrollable).first)
          .position
          .pixels;
      await tester.scrollUntilVisible(
        find.text('Totales de ambos equipos'),
        300,
      );
      final totalsOffset = tester
          .state<ScrollableState>(find.byType(Scrollable).first)
          .position
          .pixels;

      expect(totalsOffset, greaterThan(statisticsOffset));
      for (final category in [
        'corners',
        'shots',
        'yellow_cards',
        'shots_on_target',
      ]) {
        await tester.scrollUntilVisible(
          find.byKey(ValueKey<String>('market-$category')),
          250,
        );
        expect(
          find.descendant(
            of: find.byKey(ValueKey<String>('market-$category')),
            matching: find.byWidgetPredicate(
              (widget) =>
                  widget.key is ValueKey<String> &&
                  (widget.key! as ValueKey<String>).value.startsWith(
                    'market-line-$category-',
                  ),
            ),
          ),
          findsNWidgets(5),
        );
      }
      expect(find.text('Total esperado 9.8'), findsOneWidget);
      expect(find.text('Total esperado 25.3'), findsOneWidget);
      expect(find.text('Total esperado 5.5'), findsOneWidget);
      expect(find.text('Total esperado 8.5'), findsOneWidget);
    },
  );

  testWidgets('un solo perfil oculta 1X2 y los mercados combinados', (
    tester,
  ) async {
    await _pumpPrediction(
      tester,
      _CountingRepository(_prediction(singleTeamFallback: true)),
      fixture: _fixture(predictionFallbackAvailable: true),
    );

    expect(find.text('Predicción orientativa'), findsOneWidget);
    expect(find.text('Predicción 1X2 (probabilidad)'), findsNothing);
    expect(find.text('Goles totales'), findsNothing);
    expect(find.text('Totales de ambos equipos'), findsNothing);
    expect(find.text('Estadísticas por equipo'), findsOneWidget);
  });

  testWidgets(
    'viewport móvil soporta recorrer y regresar sin huecos ni error',
    (tester) async {
      tester.view.physicalSize = const Size(390, 844);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      await _pumpPrediction(tester, _CountingRepository(_prediction()));

      final list = find.byType(ListView);
      expect(
        tester.widget<ListView>(list).key,
        const PageStorageKey<String>('prediction-1'),
      );
      for (var index = 0; index < 14; index++) {
        await tester.drag(list, const Offset(0, -500));
        await tester.pump();
      }
      for (var index = 0; index < 14; index++) {
        await tester.drag(list, const Offset(0, 500));
        await tester.pump();
      }
      await tester.pumpAndSettle();

      expect(tester.takeException(), isNull);
      expect(find.text('Predicción 1X2 (probabilidad)'), findsOneWidget);
    },
  );
}
