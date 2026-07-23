class BackendHealth {
  const BackendHealth({
    required this.live,
    required this.ready,
    required this.checks,
  });

  final bool live;
  final bool ready;
  final Map<String, bool> checks;

  factory BackendHealth.fromJson(Map<String, dynamic> json) {
    final rawChecks = json['checks'];
    final status = json['status'];
    return BackendHealth(
      live:
          status == 'ok' ||
          status == 'alive' ||
          status == 'ready' ||
          status == 'not_ready' ||
          json['live'] == true,
      ready: status == 'ready' || json['ready'] == true,
      checks: rawChecks is Map
          ? rawChecks.map(
              (key, value) => MapEntry(key.toString(), value == true),
            )
          : const {},
    );
  }

  String get pendingSummary {
    const labels = <String, String>{
      'api_football_configured': 'clave de API-Football',
      'supabase_configured': 'conexión privada con Supabase',
      'admin_configured': 'token administrativo',
      'timezone': 'zona horaria',
      'database': 'base de datos',
      'models': 'modelos de predicción',
      'team_profiles': 'perfiles de equipos',
    };
    final pending = checks.entries
        .where((item) => !item.value)
        .map((item) => labels[item.key] ?? item.key)
        .toList();
    if (pending.isEmpty) return 'El backend todavía no está listo.';
    return 'Pendiente: ${pending.join(', ')}.';
  }
}
