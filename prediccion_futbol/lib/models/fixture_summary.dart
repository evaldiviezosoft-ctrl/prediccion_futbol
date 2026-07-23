const _leagueNames = <int, String>{
  11: 'CONMEBOL Sudamericana',
  13: 'CONMEBOL Libertadores',
  39: 'Premier League',
  61: 'Ligue 1',
  71: 'Brasileirão Série A',
  78: 'Bundesliga',
  128: 'Liga Profesional Argentina',
  135: 'Serie A',
  140: 'LaLiga',
  281: 'Liga 1 Perú',
};

class FixtureSummary {
  const FixtureSummary({
    required this.id,
    required this.homeTeam,
    required this.awayTeam,
    required this.kickoff,
    DateTime? displayKickoff,
    required this.leagueId,
    this.apiLeagueName,
    this.homeTeamLogoUrl,
    this.awayTeamLogoUrl,
    this.round,
    this.statusShort,
    this.predictionAvailable = false,
    bool? predictionModelAvailable,
    this.predictionStage,
  }) : displayKickoff = displayKickoff ?? kickoff,
       predictionModelAvailable =
           predictionModelAvailable ??
           (leagueId == 11 ||
               leagueId == 13 ||
               leagueId == 39 ||
               leagueId == 61 ||
               leagueId == 71 ||
               leagueId == 78 ||
               leagueId == 128 ||
               leagueId == 135 ||
               leagueId == 140 ||
               leagueId == 281);

  final int id;
  final String homeTeam;
  final String awayTeam;
  final DateTime kickoff;
  final DateTime displayKickoff;
  final int leagueId;
  final String? apiLeagueName;
  final String? homeTeamLogoUrl;
  final String? awayTeamLogoUrl;
  final String? round;
  final String? statusShort;
  final bool predictionAvailable;
  final bool predictionModelAvailable;
  final String? predictionStage;

  String get leagueName {
    final provided = apiLeagueName?.trim();
    if (provided != null && provided.isNotEmpty) {
      if (leagueId == 71 && provided.toLowerCase() == 'serie a') {
        return _leagueNames[leagueId]!;
      }
      return provided;
    }
    return _leagueNames[leagueId] ?? 'Liga $leagueId';
  }

  String get predictionStatusLabel {
    if (predictionAvailable) return 'Predicción disponible';
    if (!predictionModelAvailable) return 'Modelo aún no disponible';
    return 'Esperando predicción';
  }

  factory FixtureSummary.fromJson(Map<String, dynamic> json) {
    final limaDate = json['fixture_date_lima']?.toString().trim();
    final kickoffUtc = DateTime.parse(json['kickoff'] as String).toUtc();
    final displayKickoff = limaDate != null && limaDate.isNotEmpty
        ? DateTime.parse(limaDate)
        : _limaWallClock(kickoffUtc);
    return FixtureSummary(
      id: (json['id'] as num).toInt(),
      homeTeam: json['home_team_name'] as String,
      awayTeam: json['away_team_name'] as String,
      kickoff: kickoffUtc,
      displayKickoff: displayKickoff,
      leagueId: (json['league_id'] as num).toInt(),
      apiLeagueName: json['league_name'] as String?,
      homeTeamLogoUrl: json['home_team_logo_url'] as String?,
      awayTeamLogoUrl: json['away_team_logo_url'] as String?,
      round: json['round'] as String?,
      statusShort: json['status_short'] as String?,
      predictionAvailable: json['prediction_available'] == true,
      predictionModelAvailable: json['prediction_model_available'] as bool?,
      predictionStage: json['prediction_stage'] as String?,
    );
  }
}

DateTime _limaWallClock(DateTime utc) {
  // Peru uses UTC-05:00 throughout the year. Return a timezone-free wall
  // clock so formatting never depends on the phone/emulator timezone.
  final lima = utc.toUtc().subtract(const Duration(hours: 5));
  return DateTime(
    lima.year,
    lima.month,
    lima.day,
    lima.hour,
    lima.minute,
    lima.second,
    lima.millisecond,
    lima.microsecond,
  );
}
