import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../models/backend_health.dart';
import '../models/fixture_summary.dart';
import '../repositories/football_data_source.dart';
import '../theme/app_theme.dart';
import '../widgets/team_mark.dart';
import 'prediction_screen.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.repository});

  final FootballDataSource repository;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  late Future<_HomeLoad> _future;

  @override
  void initState() {
    super.initState();
    _future = _load();
  }

  Future<_HomeLoad> _load() async {
    final health = await widget.repository.checkHealth();
    if (!health.ready) return _HomeLoad(health: health, fixtures: const []);
    final fixtures = await widget.repository.upcomingFixtures(days: 14);
    fixtures.sort((a, b) => a.kickoff.compareTo(b.kickoff));
    return _HomeLoad(health: health, fixtures: fixtures);
  }

  Future<void> _reload() async {
    final next = _load();
    setState(() => _future = next);
    await next;
  }

  void _openPrediction(FixtureSummary fixture) {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) =>
            PredictionScreen(fixture: fixture, repository: widget.repository),
      ),
    );
  }

  @override
  void dispose() {
    widget.repository.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Align(
          alignment: Alignment.topCenter,
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 720),
            child: FutureBuilder<_HomeLoad>(
              future: _future,
              builder: (context, snapshot) {
                return RefreshIndicator(
                  onRefresh: _reload,
                  child: _buildScrollable(snapshot),
                );
              },
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildScrollable(AsyncSnapshot<_HomeLoad> snapshot) {
    final content = <Widget>[
      _HomeHeader(onRefresh: _reload),
      const Padding(
        padding: EdgeInsets.fromLTRB(20, 26, 20, 16),
        child: Text(
          'Próximos partidos',
          style: TextStyle(
            color: AppColors.text,
            fontSize: 34,
            height: 1.08,
            fontWeight: FontWeight.w800,
            letterSpacing: -0.8,
          ),
        ),
      ),
    ];

    if (snapshot.connectionState != ConnectionState.done) {
      content.add(
        const _CenteredState(
          icon: Icons.sync,
          title: 'Cargando calendario',
          message: 'Consultando los próximos partidos y sus predicciones.',
          loading: true,
        ),
      );
    } else if (snapshot.hasError) {
      content.add(
        _CenteredState(
          icon: Icons.cloud_off_outlined,
          title: 'No pudimos conectar',
          message: snapshot.error.toString(),
          actionLabel: 'Reintentar',
          onAction: _reload,
        ),
      );
    } else {
      final result = snapshot.data!;
      if (!result.health.ready) {
        content.add(
          _CenteredState(
            icon: Icons.tune,
            title: 'Backend por configurar',
            message: result.health.pendingSummary,
            actionLabel: 'Comprobar de nuevo',
            onAction: _reload,
          ),
        );
      } else if (result.fixtures.isEmpty) {
        content.add(
          _CenteredState(
            icon: Icons.event_busy_outlined,
            title: 'Sin partidos próximos',
            message:
                'Todavía no hay encuentros sincronizados para los próximos 14 días.',
            actionLabel: 'Actualizar',
            onAction: _reload,
          ),
        );
      } else {
        content.addAll(_fixtureContent(result.fixtures));
      }
    }

    content.add(const SizedBox(height: 36));
    return ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      children: content,
    );
  }

  List<Widget> _fixtureContent(List<FixtureSummary> fixtures) {
    final featured = fixtures.first;
    final rest = fixtures.skip(1).toList();
    final groups = <DateTime, List<FixtureSummary>>{};
    for (final fixture in rest) {
      final key = DateTime(
        fixture.displayKickoff.year,
        fixture.displayKickoff.month,
        fixture.displayKickoff.day,
      );
      groups.putIfAbsent(key, () => []).add(fixture);
    }

    return [
      const Padding(
        padding: EdgeInsets.fromLTRB(20, 8, 20, 12),
        child: Text(
          'DESTACADO',
          style: TextStyle(
            color: AppColors.muted,
            fontSize: 13,
            fontWeight: FontWeight.w800,
            letterSpacing: 1.1,
          ),
        ),
      ),
      Padding(
        padding: const EdgeInsets.symmetric(horizontal: 20),
        child: _FeaturedFixture(
          fixture: featured,
          onTap: () => _openPrediction(featured),
        ),
      ),
      const SizedBox(height: 22),
      for (final entry in groups.entries) ...[
        _DateHeader(date: entry.key),
        for (final fixture in entry.value)
          _FixtureRow(fixture: fixture, onTap: () => _openPrediction(fixture)),
      ],
    ];
  }
}

class _HomeLoad {
  const _HomeLoad({required this.health, required this.fixtures});

  final BackendHealth health;
  final List<FixtureSummary> fixtures;
}

class _HomeHeader extends StatelessWidget {
  const _HomeHeader({required this.onRefresh});

  final Future<void> Function() onRefresh;

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 76,
      padding: const EdgeInsets.symmetric(horizontal: 20),
      decoration: const BoxDecoration(
        border: Border(bottom: BorderSide(color: AppColors.border)),
      ),
      child: Row(
        children: [
          Container(
            width: 42,
            height: 42,
            decoration: const BoxDecoration(
              color: AppColors.primary,
              shape: BoxShape.circle,
            ),
            child: const Icon(
              Icons.sports_soccer,
              color: Color(0xFF03150A),
              size: 27,
            ),
          ),
          const SizedBox(width: 13),
          const Expanded(
            child: Text(
              'Predicción Fútbol',
              style: TextStyle(
                color: AppColors.text,
                fontSize: 22,
                fontWeight: FontWeight.w800,
                letterSpacing: -0.3,
              ),
            ),
          ),
          IconButton(
            tooltip: 'Actualizar partidos',
            onPressed: onRefresh,
            icon: const Icon(Icons.refresh_rounded, size: 27),
          ),
        ],
      ),
    );
  }
}

class _FeaturedFixture extends StatelessWidget {
  const _FeaturedFixture({required this.fixture, required this.onTap});

  final FixtureSummary fixture;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final statusColor = fixture.predictionAvailable
        ? AppColors.primary
        : AppColors.amber;
    return Card(
      child: InkWell(
        borderRadius: BorderRadius.circular(16),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(18),
          child: Column(
            children: [
              Row(
                children: [
                  const Icon(
                    Icons.sports_soccer,
                    size: 18,
                    color: AppColors.muted,
                  ),
                  const SizedBox(width: 8),
                  Text(
                    fixture.leagueName,
                    style: const TextStyle(
                      color: AppColors.muted,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  const Spacer(),
                  Text(
                    _featuredKickoff(fixture.displayKickoff),
                    style: const TextStyle(
                      color: AppColors.muted,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 22),
              Row(
                children: [
                  Expanded(
                    child: _FeaturedTeam(
                      team: fixture.homeTeam,
                      logoUrl: fixture.homeTeamLogoUrl,
                    ),
                  ),
                  const Padding(
                    padding: EdgeInsets.symmetric(horizontal: 8),
                    child: Text(
                      'VS',
                      style: TextStyle(
                        color: AppColors.muted,
                        fontSize: 15,
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                  ),
                  Expanded(
                    child: _FeaturedTeam(
                      team: fixture.awayTeam,
                      logoUrl: fixture.awayTeamLogoUrl,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 22),
              const Divider(height: 1),
              const SizedBox(height: 16),
              Row(
                children: [
                  Icon(
                    fixture.predictionAvailable
                        ? Icons.check_circle_outline
                        : Icons.schedule,
                    color: statusColor,
                    size: 21,
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      fixture.predictionStatusLabel,
                      style: TextStyle(
                        color: statusColor,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ),
                  if (fixture.predictionAvailable)
                    FilledButton.icon(
                      onPressed: onTap,
                      icon: const Icon(Icons.bar_chart_rounded, size: 19),
                      label: const Text('Ver predicción'),
                    )
                  else
                    const Icon(Icons.chevron_right, color: AppColors.muted),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _FeaturedTeam extends StatelessWidget {
  const _FeaturedTeam({required this.team, this.logoUrl});

  final String team;
  final String? logoUrl;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        TeamMark(team: team, logoUrl: logoUrl, size: 62),
        const SizedBox(height: 10),
        Text(
          team,
          textAlign: TextAlign.center,
          maxLines: 2,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(
            color: AppColors.text,
            fontSize: 17,
            fontWeight: FontWeight.w800,
          ),
        ),
      ],
    );
  }
}

class _DateHeader extends StatelessWidget {
  const _DateHeader({required this.date});

  final DateTime date;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(20, 20, 20, 13),
      decoration: const BoxDecoration(
        border: Border(bottom: BorderSide(color: AppColors.border)),
      ),
      child: Row(
        children: [
          const Icon(
            Icons.calendar_today_outlined,
            color: AppColors.muted,
            size: 21,
          ),
          const SizedBox(width: 10),
          Text(
            _spanishDate(date),
            style: const TextStyle(
              color: AppColors.text,
              fontSize: 18,
              fontWeight: FontWeight.w800,
            ),
          ),
        ],
      ),
    );
  }
}

class _FixtureRow extends StatelessWidget {
  const _FixtureRow({required this.fixture, required this.onTap});

  final FixtureSummary fixture;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final statusColor = fixture.predictionAvailable
        ? AppColors.primary
        : AppColors.amber;
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 16),
          decoration: const BoxDecoration(
            border: Border(bottom: BorderSide(color: AppColors.border)),
          ),
          child: Row(
            children: [
              SizedBox(
                width: 82,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      fixture.leagueName,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        color: AppColors.muted,
                        fontSize: 12,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    const SizedBox(height: 7),
                    Text(
                      DateFormat('HH:mm').format(fixture.displayKickoff),
                      style: const TextStyle(
                        color: AppColors.muted,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              SizedBox(
                width: 50,
                height: 40,
                child: Stack(
                  children: [
                    Positioned(
                      left: 0,
                      child: TeamMark(
                        team: fixture.homeTeam,
                        logoUrl: fixture.homeTeamLogoUrl,
                        size: 36,
                      ),
                    ),
                    Positioned(
                      right: 0,
                      child: TeamMark(
                        team: fixture.awayTeam,
                        logoUrl: fixture.awayTeamLogoUrl,
                        size: 36,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '${fixture.homeTeam}  vs  ${fixture.awayTeam}',
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        color: AppColors.text,
                        fontSize: 15,
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                    const SizedBox(height: 8),
                    Row(
                      children: [
                        Icon(
                          fixture.predictionAvailable
                              ? Icons.check_circle_outline
                              : Icons.schedule,
                          color: statusColor,
                          size: 17,
                        ),
                        const SizedBox(width: 6),
                        Flexible(
                          child: Text(
                            fixture.predictionStatusLabel,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(
                              color: statusColor,
                              fontSize: 13,
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 6),
              const Icon(Icons.chevron_right, color: AppColors.muted),
            ],
          ),
        ),
      ),
    );
  }
}

class _CenteredState extends StatelessWidget {
  const _CenteredState({
    required this.icon,
    required this.title,
    required this.message,
    this.loading = false,
    this.actionLabel,
    this.onAction,
  });

  final IconData icon;
  final String title;
  final String message;
  final bool loading;
  final String? actionLabel;
  final Future<void> Function()? onAction;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(28, 74, 28, 48),
      child: Column(
        children: [
          if (loading)
            const SizedBox(
              width: 38,
              height: 38,
              child: CircularProgressIndicator(strokeWidth: 3),
            )
          else
            Icon(icon, size: 48, color: AppColors.muted),
          const SizedBox(height: 20),
          Text(
            title,
            textAlign: TextAlign.center,
            style: Theme.of(context).textTheme.titleLarge,
          ),
          const SizedBox(height: 8),
          Text(
            message,
            textAlign: TextAlign.center,
            style: Theme.of(context).textTheme.bodyMedium,
          ),
          if (actionLabel != null && onAction != null) ...[
            const SizedBox(height: 22),
            FilledButton(onPressed: onAction, child: Text(actionLabel!)),
          ],
        ],
      ),
    );
  }
}

String _featuredKickoff(DateTime date) {
  final now = _limaWallClockNow();
  final today = DateTime(now.year, now.month, now.day);
  final day = DateTime(date.year, date.month, date.day);
  final prefix = day == today ? 'Hoy' : DateFormat('dd/MM').format(date);
  return '$prefix • ${DateFormat('HH:mm').format(date)}';
}

DateTime _limaWallClockNow() {
  final lima = DateTime.now().toUtc().subtract(const Duration(hours: 5));
  return DateTime(lima.year, lima.month, lima.day, lima.hour, lima.minute);
}

String _spanishDate(DateTime date) {
  const weekdays = [
    'lunes',
    'martes',
    'miércoles',
    'jueves',
    'viernes',
    'sábado',
    'domingo',
  ];
  const months = [
    'enero',
    'febrero',
    'marzo',
    'abril',
    'mayo',
    'junio',
    'julio',
    'agosto',
    'septiembre',
    'octubre',
    'noviembre',
    'diciembre',
  ];
  final weekday = weekdays[date.weekday - 1];
  return '${weekday[0].toUpperCase()}${weekday.substring(1)}, ${date.day} de ${months[date.month - 1]}';
}
