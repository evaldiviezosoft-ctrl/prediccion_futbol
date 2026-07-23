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
