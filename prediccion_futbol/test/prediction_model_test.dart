import 'package:flutter_test/flutter_test.dart';
import 'package:prediccion_futbol/models/prediction.dart';

void main() {
  test('Prediction expone la procedencia y muestra del baseline', () {
    final prediction = Prediction.fromJson({
      'fixture_id': 1492292,
      'home_team_name': 'Chapecoense-SC',
      'away_team_name': 'Flamengo',
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
      'updated_at': '2026-07-22T15:00:00Z',
      'model_metadata': {
        'model_type': 'statistical_baseline',
        'sample_sizes': {
          'home_team_home_matches': 0,
          'away_team_away_matches': 57,
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
    expect(prediction.homeVenueSample, 0);
    expect(prediction.awayVenueSample, 57);
    expect(prediction.expectedValue('home_goals'), 0.9);
    expect(prediction.goalLines, hasLength(5));
    expect(prediction.goalLines[2].line, 2.5);
    expect(prediction.goalLines[2].probability, 0.49);
    expect(prediction.possibleScorers.single.player, 'Pedro');
    expect(prediction.possibleAssistants.single.player, 'Arrascaeta');
    expect(prediction.usesCrossLeagueStatisticsReference, isTrue);
    expect(prediction.statisticsReferenceRows, 41);
    expect(prediction.statisticsReferenceLeagueId, 281);
  });
}
