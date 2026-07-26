import 'package:flutter_test/flutter_test.dart';
import 'package:prediccion_futbol/models/prediction.dart';

void main() {
  test('Prediction expone la procedencia y muestra del baseline', () {
    final prediction = Prediction.fromJson({
      'fixture_id': 1492292,
      'home_team_name': 'Chapecoense-SC',
      'away_team_name': 'Flamengo',
      'home_team_country': 'Brazil',
      'away_team_country': 'Brazil',
      'stage': 'prematch',
      'home_win_probability': 0.2,
      'draw_probability': 0.25,
      'away_win_probability': 0.55,
      'expected': {
        'home_goals': 0.9,
        'away_goals': 1.7,
        'home_corners': 4.2,
        'away_corners': 5.1,
      },
      'goal_lines': [
        {'line': 0.5, 'probability': 0.92},
        {'line': 1.5, 'probability': 0.73},
        {'line': 2.5, 'probability': 0.49},
        {'line': 3.5, 'probability': 0.27},
        {'line': 4.5, 'probability': 0.12},
      ],
      'possible_scorers': [
        {'player': 'Pedro', 'team': 'Flamengo', 'probability': 0.38},
      ],
      'possible_assistants': [
        {'player': 'Arrascaeta', 'team': 'Flamengo', 'probability': 0.29},
      ],
      'probable_forecast': [
        {
          'category': 'goals',
          'title': 'Goles totales',
          'prediction': 'Más de 1.5',
          'probability': .73,
          'confidence': 'medium',
        },
      ],
      'updated_at': '2026-07-22T15:00:00Z',
      'model_metadata': {
        'model_type': 'statistical_baseline',
        'sample_sizes': {
          'home_team_home_matches': 0,
          'away_team_away_matches': 57,
        },
        'cross_league_calibration': {
          'applied': true,
          'home_source': {'competition_name': 'Eliteserien', 'factor': 0.91},
          'away_source': {'competition_name': 'Premier League', 'factor': 1.08},
        },
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

    expect(prediction.isStatisticalBaseline, isTrue);
    expect(prediction.homeTeamCountry, 'Brazil');
    expect(prediction.awayTeamCountry, 'Brazil');
    expect(prediction.homeVenueSample, 0);
    expect(prediction.awayVenueSample, 57);
    expect(prediction.expectedValue('home_goals'), 0.9);
    expect(prediction.goalLines, hasLength(5));
    expect(prediction.goalLines[2].line, 2.5);
    expect(prediction.goalLines[2].probability, 0.49);
    expect(prediction.possibleScorers.single.player, 'Pedro');
    expect(prediction.possibleAssistants.single.player, 'Arrascaeta');
    expect(prediction.probableForecast.single.category, 'goals');
    expect(prediction.probableForecast.single.prediction, 'Más de 1.5');
    expect(prediction.usesCrossLeagueStatisticsReference, isTrue);
    expect(prediction.statisticsReferenceRows, 41);
    expect(prediction.statisticsReferenceLeagueId, 281);
    expect(prediction.crossLeagueCalibration?.homeCompetition, 'Eliteserien');
    expect(prediction.crossLeagueCalibration?.homeFactor, 0.91);
    expect(
      prediction.crossLeagueCalibration?.awayCompetition,
      'Premier League',
    );
    expect(prediction.crossLeagueCalibration?.awayFactor, 1.08);
  });

  test('Prediction reconoce un fallback marcado con confianza baja', () {
    final prediction = Prediction.fromJson({
      'fixture_id': 1,
      'home_team_name': 'Barcelona',
      'away_team_name': 'Europa FC',
      'stage': 'orientative',
      'home_win_probability': 0.5,
      'draw_probability': 0.25,
      'away_win_probability': 0.25,
      'expected': {'home_corners': 5.0, 'away_corners': 4.2},
      'updated_at': '2026-07-23T15:00:00Z',
      'model_metadata': {
        'model_type': 'calendar_fallback',
        'confidence': 'low',
        'single_team_profile': true,
        'known_profile_sides': ['away'],
      },
    });

    expect(prediction.isStatisticalBaseline, isFalse);
    expect(prediction.isLowConfidenceFallback, isTrue);
    expect(prediction.isSingleTeamProfileFallback, isTrue);
    expect(prediction.knownProfileSides, {'away'});
    expect(prediction.displayExpectedValue('home_corners'), isNull);
    expect(prediction.displayExpectedValue('away_corners'), 4.2);
    expect(prediction.crossLeagueCalibration, isNull);
  });

  test('Prediction omite una calibración no aplicada o incompleta', () {
    const baseMetadata = <String, dynamic>{
      'applied': false,
      'home_source': {'competition_name': 'Eliteserien', 'factor': 0.91},
      'away_source': {'competition_name': 'Premier League', 'factor': 1.08},
    };

    expect(CrossLeagueCalibration.tryParse(baseMetadata), isNull);
    expect(
      CrossLeagueCalibration.tryParse({
        ...baseMetadata,
        'applied': true,
        'away_source': {'competition_name': 'Premier League'},
      }),
      isNull,
    );
  });
}
