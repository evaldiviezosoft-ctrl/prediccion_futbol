class GoalLineProbability {
  const GoalLineProbability({required this.line, required this.probability});

  final double line;
  final double probability;

  factory GoalLineProbability.fromJson(Map<String, dynamic> json) =>
      GoalLineProbability(
        line: (json['line'] as num?)?.toDouble() ?? 0,
        probability: (json['probability'] as num?)?.toDouble() ?? 0,
      );
}

class PossibleScorer {
  const PossibleScorer({
    required this.player,
    required this.team,
    required this.probability,
  });

  final String player;
  final String team;
  final double probability;

  factory PossibleScorer.fromJson(Map<String, dynamic> json) => PossibleScorer(
    player: json['player']?.toString() ?? 'Jugador',
    team: json['team']?.toString() ?? '',
    probability: (json['probability'] as num?)?.toDouble() ?? 0,
  );
}

class Prediction {
  const Prediction({
    required this.fixtureId,
    required this.homeTeam,
    required this.awayTeam,
    required this.stage,
    required this.lineupsConfirmed,
    required this.homeWin,
    required this.draw,
    required this.awayWin,
    required this.over25,
    required this.btts,
    required this.expected,
    required this.goalLines,
    required this.possibleScorers,
    required this.possibleAssistants,
    required this.updatedAt,
    this.modelMetadata = const {},
  });

  final int fixtureId;
  final String homeTeam;
  final String awayTeam;
  final String stage;
  final bool lineupsConfirmed;
  final double homeWin;
  final double draw;
  final double awayWin;
  final double? over25;
  final double? btts;
  final Map<String, dynamic> expected;
  final List<GoalLineProbability> goalLines;
  final List<PossibleScorer> possibleScorers;
  final List<PossibleScorer> possibleAssistants;
  final DateTime updatedAt;
  final Map<String, dynamic> modelMetadata;

  double? expectedValue(String key) => (expected[key] as num?)?.toDouble();

  bool get isStatisticalBaseline =>
      modelMetadata['model_type'] == 'statistical_baseline';

  int? get homeVenueSample {
    final sizes = modelMetadata['sample_sizes'];
    return sizes is Map
        ? (sizes['home_team_home_matches'] as num?)?.toInt()
        : null;
  }

  int? get awayVenueSample {
    final sizes = modelMetadata['sample_sizes'];
    return sizes is Map
        ? (sizes['away_team_away_matches'] as num?)?.toInt()
        : null;
  }

  Iterable<Map<String, dynamic>> get _marketStatisticMetrics sync* {
    final marketStatistics = modelMetadata['market_statistics'];
    if (marketStatistics is! Map) return;
    final teams = marketStatistics['teams'];
    if (teams is! Map) return;
    for (final team in teams.values) {
      if (team is! Map) continue;
      final metrics = team['metrics'];
      if (metrics is! Map) continue;
      for (final metric in metrics.values) {
        if (metric is Map) {
          yield Map<String, dynamic>.from(metric);
        }
      }
    }
  }

  bool get usesCrossLeagueStatisticsReference => _marketStatisticMetrics.any(
    (metric) => metric['cross_league_reference'] == true,
  );

  int? get statisticsReferenceRows {
    int? largest;
    for (final metric in _marketStatisticMetrics) {
      final rows = (metric['prior_rows'] as num?)?.toInt();
      if (rows != null && (largest == null || rows > largest)) {
        largest = rows;
      }
    }
    return largest;
  }

  int? get statisticsReferenceLeagueId {
    final marketStatistics = modelMetadata['market_statistics'];
    return marketStatistics is Map
        ? (marketStatistics['reference_statistics_league_id'] as num?)?.toInt()
        : null;
  }

  factory Prediction.fromJson(Map<String, dynamic> json) {
    double number(String key) => (json[key] as num).toDouble();
    final rawGoalLines = json['goal_lines'];
    final rawScorers = json['possible_scorers'];
    final rawAssistants = json['possible_assistants'];

    return Prediction(
      fixtureId: (json['fixture_id'] as num).toInt(),
      homeTeam: json['home_team_name'] as String,
      awayTeam: json['away_team_name'] as String,
      stage: json['stage'] as String,
      lineupsConfirmed: json['lineups_confirmed'] as bool? ?? false,
      homeWin: number('home_win_probability'),
      draw: number('draw_probability'),
      awayWin: number('away_win_probability'),
      over25: (json['over25_probability'] as num?)?.toDouble(),
      btts: (json['btts_probability'] as num?)?.toDouble(),
      expected: Map<String, dynamic>.from(json['expected'] as Map? ?? const {}),
      goalLines: rawGoalLines is List
          ? rawGoalLines
                .whereType<Map>()
                .map(
                  (item) => GoalLineProbability.fromJson(
                    Map<String, dynamic>.from(item),
                  ),
                )
                .toList()
          : const [],
      possibleScorers: rawScorers is List
          ? rawScorers
                .whereType<Map>()
                .map(
                  (item) =>
                      PossibleScorer.fromJson(Map<String, dynamic>.from(item)),
                )
                .toList()
          : const [],
      possibleAssistants: rawAssistants is List
          ? rawAssistants
                .whereType<Map>()
                .map(
                  (item) =>
                      PossibleScorer.fromJson(Map<String, dynamic>.from(item)),
                )
                .toList()
          : const [],
      updatedAt: DateTime.parse(json['updated_at'] as String).toLocal(),
      modelMetadata: Map<String, dynamic>.from(
        json['model_metadata'] as Map? ?? const {},
      ),
    );
  }
}
