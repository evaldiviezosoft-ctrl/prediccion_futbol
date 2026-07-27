import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
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
      'https://api-production-1d96.up.railway.app/fixtures/team-logo/132',
    );
    expect(
      fixtures.single.awayTeamLogoUrl,
      'https://api-production-1d96.up.railway.app/fixtures/team-logo/127',
    );
    repository.dispose();
  });

  test(
    'health tolera un arranque en frío dentro del margen configurado',
    () async {
      final client = MockClient((_) async {
        await Future<void>.delayed(const Duration(milliseconds: 30));
        return http.Response(
          jsonEncode({
            'status': 'ready',
            'checks': {'database': true, 'models': true},
          }),
          200,
        );
      });
      final repository = FootballRepository(
        client: client,
        healthTimeout: const Duration(milliseconds: 100),
      );

      final health = await repository.checkHealth();

      expect(health.ready, isTrue);
      repository.dispose();
    },
  );

  test('health clasifica correctamente un timeout real', () async {
    final client = MockClient((_) async {
      await Future<void>.delayed(const Duration(milliseconds: 50));
      return http.Response(jsonEncode({'status': 'ready'}), 200);
    });
    final repository = FootballRepository(
      client: client,
      healthTimeout: const Duration(milliseconds: 5),
    );

    await expectLater(
      repository.checkHealth(),
      throwsA(
        isA<FootballRepositoryException>().having(
          (error) => error.kind,
          'kind',
          RepositoryErrorKind.timeout,
        ),
      ),
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
}
