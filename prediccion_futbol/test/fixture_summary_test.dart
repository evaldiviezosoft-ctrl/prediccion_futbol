import 'package:flutter_test/flutter_test.dart';
import 'package:prediccion_futbol/models/fixture_summary.dart';

void main() {
  test('FixtureSummary usa nombres API y reconoce ligas sudamericanas', () {
    final fromApi = FixtureSummary.fromJson({
      'id': 10,
      'home_team_name': 'Alianza Lima',
      'away_team_name': 'Sporting Cristal',
      'kickoff': '2026-07-23T01:00:00Z',
      'fixture_date_lima': '2026-07-22T20:00:00',
      'league_id': 281,
      'league_name': 'Primera División',
      'home_team_country': 'Peru',
      'away_team_country': 'Peru',
      'home_team_logo_url': 'https://example.test/alianza.png',
      'away_team_logo_url': 'https://example.test/cristal.png',
    });
    final fallback = FixtureSummary(
      id: 11,
      homeTeam: 'Flamengo',
      awayTeam: 'Palmeiras',
      kickoff: DateTime.utc(2026, 7, 23),
      leagueId: 71,
      apiLeagueName: 'Serie A',
    );

    expect(fromApi.leagueName, 'Primera División');
    expect(fromApi.kickoff, DateTime.utc(2026, 7, 23, 1));
    expect(fromApi.displayKickoff, DateTime(2026, 7, 22, 20));
    expect(fromApi.homeTeamLogoUrl, 'https://example.test/alianza.png');
    expect(fromApi.awayTeamLogoUrl, 'https://example.test/cristal.png');
    expect(fromApi.homeTeamCountry, 'Peru');
    expect(fromApi.awayTeamCountry, 'Peru');
    expect(fromApi.predictionModelAvailable, isTrue);
    expect(fromApi.predictionStatusLabel, 'Esperando predicción');
    expect(fallback.leagueName, 'Brasileirão Série A');
    expect(fallback.predictionModelAvailable, isTrue);
  });

  test('FixtureSummary distingue ligas que ya tienen modelo', () {
    final fixture = FixtureSummary(
      id: 12,
      homeTeam: 'Arsenal',
      awayTeam: 'Liverpool',
      kickoff: DateTime.utc(2026, 8, 1),
      leagueId: 39,
    );

    expect(fixture.predictionModelAvailable, isTrue);
    expect(fixture.predictionStatusLabel, 'Esperando predicción');
  });

  test('competiciones de calendario se muestran sin afirmar un modelo', () {
    final europaLeague = FixtureSummary(
      id: 20,
      homeTeam: 'Equipo local',
      awayTeam: 'Equipo visitante',
      kickoff: DateTime.utc(2026, 7, 24),
      leagueId: 3,
    );
    final clubFriendly = FixtureSummary(
      id: 21,
      homeTeam: 'Barcelona',
      awayTeam: 'Manchester United',
      kickoff: DateTime.utc(2026, 7, 24),
      leagueId: 667,
      apiLeagueName: 'Friendlies Clubs',
    );

    expect(europaLeague.leagueName, 'UEFA Europa League');
    expect(europaLeague.predictionModelAvailable, isFalse);
    expect(europaLeague.predictionStatusLabel, 'Modelo aún no disponible');
    expect(clubFriendly.leagueName, 'Amistosos de clubes');
    expect(clubFriendly.predictionModelAvailable, isFalse);
    expect(clubFriendly.predictionStatusLabel, 'Modelo aún no disponible');
  });

  test('el backend puede desactivar la capacidad sin cambiar Flutter', () {
    final fixture = FixtureSummary.fromJson({
      'id': 12,
      'home_team_name': 'Arsenal',
      'away_team_name': 'Liverpool',
      'kickoff': '2026-08-01T15:00:00Z',
      'league_id': 39,
      'prediction_model_available': false,
    });

    expect(fixture.predictionModelAvailable, isFalse);
    expect(fixture.predictionStatusLabel, 'Modelo aún no disponible');
  });

  test(
    'un fallback permite abrir una guía sin afirmar que existe un modelo',
    () {
      final fixture = FixtureSummary.fromJson({
        'id': 22,
        'home_team_name': 'Barcelona',
        'away_team_name': 'Europa FC',
        'kickoff': '2026-07-24T18:00:00Z',
        'league_id': 667,
        'prediction_available': true,
        'prediction_model_available': false,
        'prediction_fallback_available': true,
      });

      expect(fixture.predictionModelAvailable, isFalse);
      expect(fixture.predictionFallbackAvailable, isTrue);
      expect(fixture.predictionAccessAvailable, isTrue);
      expect(
        fixture.predictionStatusLabel,
        'Predicción orientativa disponible',
      );
    },
  );

  test('el fallback horario también usa Lima y no la zona del dispositivo', () {
    final fixture = FixtureSummary.fromJson({
      'id': 13,
      'home_team_name': 'Local',
      'away_team_name': 'Visitante',
      'kickoff': '2026-07-23T00:30:00Z',
      'league_id': 71,
    });

    expect(fixture.displayKickoff, DateTime(2026, 7, 22, 19, 30));
  });
}
