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

    prediction = Prediction(
      fixtureId: 1001,
      homeTeam: 'Real Madrid',
      awayTeam: 'Valencia',
      homeTeamCountry: 'España',
      awayTeamCountry: 'España',
      stage: 'prematch',
      lineupsConfirmed: false,
      homeWin: 0.58,
      draw: 0.24,
      awayWin: 0.18,
      over25: 0.64,
      btts: 0.51,
      expected: const {
        'home_goals': 2.1,
        'away_goals': 0.9,
        'home_corners': 6.2,
        'away_corners': 3.8,
        'home_shots': 15.4,
        'away_shots': 9.7,
        'home_shots_on_target': 6.1,
        'away_shots_on_target': 3.2,
      },
      goalLines: const [
        GoalLineProbability(line: 0.5, probability: 0.91),
        GoalLineProbability(line: 1.5, probability: 0.76),
        GoalLineProbability(line: 2.5, probability: 0.64),
        GoalLineProbability(line: 3.5, probability: 0.39),
        GoalLineProbability(line: 4.5, probability: 0.19),
      ],
      possibleScorers: const [
        PossibleScorer(
          player: 'Vinícius Júnior',
          team: 'Real Madrid',
          probability: 0.42,
        ),
        PossibleScorer(
          player: 'Hugo Duro',
          team: 'Valencia',
          probability: 0.24,
        ),
      ],
      possibleAssistants: const [
        PossibleScorer(
          player: 'Jude Bellingham',
          team: 'Real Madrid',
          probability: 0.31,
        ),
      ],
      updatedAt: now.subtract(const Duration(minutes: 2)),
    );
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
