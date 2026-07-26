import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:prediccion_futbol/models/ai_calibration.dart';
import 'package:prediccion_futbol/models/prediction.dart';
import 'package:prediccion_futbol/repositories/football_repository.dart';

Map<String, dynamic> _predictionJson() => {
  'fixture_id': 1492292,
  'home_team_name': 'Chapecoense-SC',
  'away_team_name': 'Flamengo',
  'stage': 'prematch',
  'home_win_probability': 0.2,
  'draw_probability': 0.25,
  'away_win_probability': 0.55,
  'expected': {'home_goals': 0.9, 'away_goals': 1.7},
  'updated_at': '2026-07-22T15:00:00Z',
};

Map<String, dynamic> _analysisJson(String status) => {
  'fixture_id': 1492292,
  'status': status,
  'retry_after_seconds': status == 'pending' ? 0 : null,
  'is_stale': false,
  'generated_at': status == 'updated' ? '2026-07-26T03:00:00Z' : null,
  'analysis': status == 'updated'
      ? {
          'match_type': 'official',
          'base_probabilities': {'home': .2, 'draw': .25, 'away': .55},
          'adjusted_probabilities': {'home': .22, 'draw': .26, 'away': .52},
          'adjustments': <Map<String, dynamic>>[],
          'preparation_comparison': <String, dynamic>{},
          'rotation_effect': <String, dynamic>{},
          'projections': <String, dynamic>{},
          'recommended_market': null,
          'conservative_alternative': null,
          'risks': <String>[],
          'missing_data': <String>[],
          'possible_model_errors': <String>[],
          'refresh_with_lineups': true,
          'data_quality': 'medium',
          'lineups_considered': false,
          'model_label': 'Calibración contextual IA',
        }
      : null,
};

void main() {
  test('upcoming prefiere los escudos servidos por el backend', () async {
    final client = MockClient((_) async {
      return http.Response(
        jsonEncode([
          {
            'id': 1492292,
            'home_team_name': 'Chapecoense-SC',
            'away_team_name': 'Flamengo',
            'kickoff': '2026-07-23T00:30:00Z',
            'fixture_date_lima': '2026-07-22T19:30:00',
            'league_id': 71,
            'home_team_logo_url': 'https://cdn.example/home.png',
            'away_team_logo_url': 'https://cdn.example/away.png',
            'home_team_logo_proxy_path': '/fixtures/team-logo/132',
            'away_team_logo_proxy_path': '/fixtures/team-logo/127',
          },
        ]),
        200,
      );
    });
    final repository = FootballRepository(client: client);

    final fixtures = await repository.upcomingFixtures();

    expect(
      fixtures.single.homeTeamLogoUrl,
      'http://10.0.2.2:8000/fixtures/team-logo/132',
    );
    expect(
      fixtures.single.awayTeamLogoUrl,
      'http://10.0.2.2:8000/fixtures/team-logo/127',
    );
    repository.dispose();
  });

  test(
    'polling vuelve a consultar después de 404 y publica el resultado',
    () async {
      var calls = 0;
      final client = MockClient((_) async {
        calls += 1;
        return calls == 1
            ? http.Response('', 404)
            : http.Response(jsonEncode(_predictionJson()), 200);
      });
      final repository = FootballRepository(
        client: client,
        pollInterval: Duration.zero,
        retryBaseDelay: Duration.zero,
      );

      final values = await repository.watchPrediction(1492292).take(2).toList();

      expect(values.first, isNull);
      expect(values.last, isA<Prediction>());
      expect(values.last!.awayTeam, 'Flamengo');
      expect(calls, 2);
      repository.dispose();
    },
  );

  test('polling se recupera de un fallo HTTP transitorio', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls += 1;
      return calls == 1
          ? http.Response('error temporal', 500)
          : http.Response(jsonEncode(_predictionJson()), 200);
    });
    final repository = FootballRepository(
      client: client,
      pollInterval: Duration.zero,
      retryBaseDelay: Duration.zero,
    );

    final prediction = await repository.watchPrediction(1492292).first;

    expect(prediction, isA<Prediction>());
    expect(calls, 2);
    repository.dispose();
  });

  test('polling informa error después del límite de reintentos', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls += 1;
      return http.Response('error temporal', 500);
    });
    final repository = FootballRepository(
      client: client,
      pollInterval: Duration.zero,
      retryBaseDelay: Duration.zero,
      maxTransientFailures: 3,
    );

    await expectLater(
      repository.watchPrediction(1492292),
      emitsError(isA<FootballRepositoryException>()),
    );

    expect(calls, 3);
    repository.dispose();
  });

  test(
    'polling de IA avanza de pending a updated sin afectar la base',
    () async {
      var calls = 0;
      final client = MockClient((request) async {
        expect(request.url.path, '/predictions/1492292/analysis');
        calls += 1;
        return http.Response(
          jsonEncode(_analysisJson(calls == 1 ? 'pending' : 'updated')),
          200,
        );
      });
      final repository = FootballRepository(
        client: client,
        pollInterval: Duration.zero,
        retryBaseDelay: Duration.zero,
      );

      final values = await repository
          .watchAiCalibration(1492292)
          .take(2)
          .toList();

      expect(values.first.status, AiCalibrationStatus.pending);
      expect(values.last.status, AiCalibrationStatus.updated);
      expect(values.last.analysis!.adjustedProbabilities.away, .52);
      expect(calls, 2);
      repository.dispose();
    },
  );

  test('un payload IA inválido se informa como datos inesperados', () async {
    final client = MockClient(
      (_) async => http.Response(
        jsonEncode({
          'fixture_id': 1492292,
          'status': 'updated',
          'generated_at': '2026-07-26T03:00:00Z',
          'analysis': {
            'base_probabilities': {'home': .2, 'draw': .25, 'away': .55},
            'adjusted_probabilities': {'home': .9, 'draw': .2, 'away': .1},
          },
        }),
        200,
      ),
    );
    final repository = FootballRepository(client: client);

    await expectLater(
      repository.watchAiCalibration(1492292),
      emitsError(
        isA<FootballRepositoryException>().having(
          (error) => error.kind,
          'kind',
          RepositoryErrorKind.invalidData,
        ),
      ),
    );
    repository.dispose();
  });
}
