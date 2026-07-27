import '../models/backend_health.dart';
import '../models/fixture_summary.dart';
import '../models/prediction.dart';
import 'football_data_source.dart';

class DemoFootballRepository implements FootballDataSource {
  DemoFootballRepository() {
    final now = DateTime.now();
    fixtures = [
      FixtureSummary(
        id: 1001,
        homeTeam: 'Real Madrid',
        awayTeam: 'Valencia',
        homeTeamCountry: 'España',
        awayTeamCountry: 'España',
        kickoff: DateTime(now.year, now.month, now.day, 21),
        leagueId: 140,
        predictionAvailable: true,
        predictionStage: 'prematch',
      ),
      FixtureSummary(
        id: 1002,
        homeTeam: 'Arsenal',
        awayTeam: 'Chelsea',
        kickoff: now.add(const Duration(days: 1, hours: 3)),
        leagueId: 39,
        predictionAvailable: true,
        predictionStage: 'initial',
      ),
      FixtureSummary(
        id: 1003,
        homeTeam: 'Inter',
        awayTeam: 'Roma',
        kickoff: now.add(const Duration(days: 1, hours: 7)),
        leagueId: 135,
        predictionAvailable: true,
        predictionStage: 'prematch',
      ),
      FixtureSummary(
        id: 1004,
        homeTeam: 'Barcelona',
        awayTeam: 'Real Sociedad',
        kickoff: now.add(const Duration(days: 2, hours: 2)),
        leagueId: 140,
      ),
      FixtureSummary(
        id: 1005,
        homeTeam: 'Liverpool',
        awayTeam: 'Tottenham',
        kickoff: now.add(const Duration(days: 2, hours: 5)),
        leagueId: 39,
      ),
      FixtureSummary(
        id: 1006,
        homeTeam: 'Juventus',
        awayTeam: 'Atalanta',
        kickoff: now.add(const Duration(days: 3, hours: 8)),
        leagueId: 135,
      ),
    ];

    prediction = Prediction.fromJson({
      'fixture_id': 1001,
      'home_team_name': 'Real Madrid',
      'away_team_name': 'Valencia',
      'home_team_country': 'España',
      'away_team_country': 'España',
      'stage': 'prematch',
      'lineups_confirmed': false,
      'home_win_probability': .58,
      'draw_probability': .24,
      'away_win_probability': .18,
      'over25_probability': .64,
      'btts_probability': .51,
      'expected': {
        'home_goals': 2.1,
        'away_goals': .9,
        'home_corners': 6.2,
        'away_corners': 3.8,
        'home_shots': 15.4,
        'away_shots': 9.7,
        'home_shots_on_target': 6.1,
        'away_shots_on_target': 3.2,
        'home_yellow_cards': 2.3,
        'away_yellow_cards': 2.8,
      },
      'goal_lines': [
        {'line': .5, 'probability': .95},
        {'line': 1.5, 'probability': .80},
        {'line': 2.5, 'probability': .58},
        {'line': 3.5, 'probability': .35},
        {'line': 4.5, 'probability': .18},
      ],
      'possible_scorers': [
        {
          'player': 'Vinícius Júnior',
          'team': 'Real Madrid',
          'probability': .42,
        },
        {'player': 'Hugo Duro', 'team': 'Valencia', 'probability': .24},
      ],
      'possible_assistants': [
        {
          'player': 'Jude Bellingham',
          'team': 'Real Madrid',
          'probability': .31,
        },
      ],
      'market_forecast': {
        'version': '1.0',
        'method': 'poisson_calibrated',
        'markets': [
          _demoMarket(
            category: 'goals',
            title: 'Goles',
            expectedTotal: 3,
            lines: [.5, 1.5, 2.5, 3.5, 4.5],
            overProbabilities: [.95, .80, .58, .35, .18],
          ),
          _demoMarket(
            category: 'corners',
            title: 'Córners',
            expectedTotal: 10,
            lines: [6.5, 7.5, 8.5, 9.5, 10.5],
            overProbabilities: [.87, .78, .67, .55, .43],
          ),
          _demoMarket(
            category: 'shots',
            title: 'Remates',
            expectedTotal: 25.1,
            lines: [19.5, 21.5, 23.5, 25.5, 27.5],
            overProbabilities: [.83, .72, .61, .48, .36],
          ),
          _demoMarket(
            category: 'yellow_cards',
            title: 'Tarjetas amarillas',
            expectedTotal: 5.1,
            lines: [1.5, 2.5, 3.5, 4.5, 5.5],
            overProbabilities: [.95, .87, .75, .60, .44],
          ),
          _demoMarket(
            category: 'shots_on_target',
            title: 'Remates al arco',
            expectedTotal: 9.3,
            lines: [5.5, 6.5, 7.5, 8.5, 9.5],
            overProbabilities: [.88, .79, .68, .57, .45],
          ),
        ],
      },
      'updated_at': now
          .subtract(const Duration(minutes: 2))
          .toUtc()
          .toIso8601String(),
      'model_metadata': {
        'model_type': 'statistical_baseline',
        'sample_sizes': {
          'home_team_home_matches': 18,
          'away_team_away_matches': 18,
        },
      },
    });
  }

  late final List<FixtureSummary> fixtures;
  late final Prediction prediction;

  @override
  Future<BackendHealth> checkHealth() async =>
      const BackendHealth(live: true, ready: true, checks: {'demo': true});

  @override
  Future<List<FixtureSummary>> upcomingFixtures({int days = 14}) async =>
      fixtures;

  @override
  Stream<Prediction?> watchPrediction(int fixtureId) =>
      Stream<Prediction?>.value(
        fixtureId == prediction.fixtureId ? prediction : null,
      );

  @override
  void dispose() {}
}

Map<String, dynamic> _demoMarket({
  required String category,
  required String title,
  required double expectedTotal,
  required List<double> lines,
  required List<double> overProbabilities,
}) => {
  'category': category,
  'title': title,
  'scope': 'match_total',
  'expected_total': expectedTotal,
  'confidence': 'medium',
  'lines': [
    for (var index = 0; index < lines.length; index++)
      _demoLine(lines[index], overProbabilities[index]),
  ],
};

Map<String, dynamic> _demoLine(double line, double overProbability) {
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
