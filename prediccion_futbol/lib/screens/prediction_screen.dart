import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../models/ai_calibration.dart';
import '../models/fixture_summary.dart';
import '../models/prediction.dart';
import '../repositories/football_data_source.dart';
import '../theme/app_theme.dart';
import '../widgets/team_mark.dart';

part '../widgets/ai_calibration_section.dart';

class PredictionScreen extends StatefulWidget {
  const PredictionScreen({
    super.key,
    required this.fixture,
    required this.repository,
  });

  final FixtureSummary fixture;
  final FootballDataSource repository;

  @override
  State<PredictionScreen> createState() => _PredictionScreenState();
}

class _PredictionScreenState extends State<PredictionScreen> {
  Stream<Prediction?>? _predictionStream;
  Stream<AiCalibrationResult>? _aiCalibrationStream;

  @override
  void initState() {
    super.initState();
    if (widget.fixture.predictionAccessAvailable) {
      _predictionStream = widget.repository.watchPrediction(widget.fixture.id);
      _aiCalibrationStream = widget.repository.watchAiCalibration(
        widget.fixture.id,
      );
    }
  }

  void _retryPrediction() {
    if (!widget.fixture.predictionAccessAvailable) return;
    setState(() {
      _predictionStream = widget.repository.watchPrediction(widget.fixture.id);
      _aiCalibrationStream = widget.repository.watchAiCalibration(
        widget.fixture.id,
      );
    });
  }

  void _retryAiCalibration() {
    if (!widget.fixture.predictionAccessAvailable) return;
    setState(
      () => _aiCalibrationStream = widget.repository.watchAiCalibration(
        widget.fixture.id,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Predicción')),
      body: Align(
        alignment: Alignment.topCenter,
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 720),
          child: !widget.fixture.predictionAccessAvailable
              ? const _PredictionState(
                  icon: Icons.model_training_outlined,
                  title: 'Modelo aún no disponible',
                  message:
                      'Esta competición ya tiene datos, pero todavía no dispone de un modelo predictivo validado.',
                )
              : StreamBuilder<Prediction?>(
                  stream: _predictionStream!,
                  builder: (context, snapshot) {
                    if (snapshot.hasError) {
                      return _PredictionState(
                        icon: Icons.wifi_off_outlined,
                        title: 'No pudimos escuchar la predicción',
                        message: snapshot.error.toString(),
                        actionLabel: 'Reintentar',
                        onAction: _retryPrediction,
                      );
                    }
                    if (snapshot.connectionState == ConnectionState.waiting) {
                      return const _PredictionState(
                        icon: Icons.sync,
                        title: 'Cargando predicción',
                        message: 'Conectando con los resultados publicados.',
                        loading: true,
                      );
                    }
                    final prediction = snapshot.data;
                    if (prediction == null) {
                      return _PredictionState(
                        icon: Icons.schedule,
                        title: 'Predicción en preparación',
                        message:
                            'El partido ya está sincronizado. El modelo publicará el resultado cuando el backend complete el cálculo.',
                        actionLabel: 'Comprobar de nuevo',
                        onAction: _retryPrediction,
                      );
                    }
                    return _PredictionContent(
                      fixture: widget.fixture,
                      prediction: prediction,
                      aiCalibrationStream: _aiCalibrationStream!,
                      onRetryAiCalibration: _retryAiCalibration,
                    );
                  },
                ),
        ),
      ),
    );
  }
}

class _PredictionContent extends StatelessWidget {
  const _PredictionContent({
    required this.fixture,
    required this.prediction,
    required this.aiCalibrationStream,
    required this.onRetryAiCalibration,
  });

  final FixtureSummary fixture;
  final Prediction prediction;
  final Stream<AiCalibrationResult> aiCalibrationStream;
  final VoidCallback onRetryAiCalibration;

  @override
  Widget build(BuildContext context) {
    final hasExpectedStats =
        _hasExpectedPair(prediction, 'home_corners', 'away_corners') ||
        _hasExpectedPair(prediction, 'home_shots', 'away_shots') ||
        _hasExpectedPair(
          prediction,
          'home_shots_on_target',
          'away_shots_on_target',
        );
    final isLowConfidenceFallback =
        fixture.predictionFallbackAvailable ||
        prediction.isLowConfidenceFallback;
    final isSingleTeamFallback =
        isLowConfidenceFallback && prediction.isSingleTeamProfileFallback;

    return ListView(
      padding: const EdgeInsets.only(bottom: 32),
      children: [
        _MatchHeader(
          fixture: fixture,
          homeCountry: fixture.homeTeamCountry ?? prediction.homeTeamCountry,
          awayCountry: fixture.awayTeamCountry ?? prediction.awayTeamCountry,
        ),
        if (isLowConfidenceFallback)
          _LowConfidenceFallbackNotice(
            isSingleTeamProfile: isSingleTeamFallback,
          ),
        if (!isSingleTeamFallback)
          _Section(
            icon: Icons.bar_chart_rounded,
            title: 'Predicción 1X2 (probabilidad)',
            child: _ProbabilityPanel(prediction: prediction),
          ),
        _AiCalibrationSlot(
          stream: aiCalibrationStream,
          homeTeam: prediction.homeTeam,
          awayTeam: prediction.awayTeam,
          showProbabilityComparison: !isSingleTeamFallback,
          onRetry: onRetryAiCalibration,
        ),
        if (prediction.goalLines.isNotEmpty)
          _Section(
            icon: Icons.sports_soccer,
            title: 'Goles totales',
            child: _GoalLinesPanel(goalLines: prediction.goalLines),
          ),
        if (hasExpectedStats)
          _Section(
            icon: Icons.show_chart_rounded,
            title: 'Estadísticas por equipo',
            child: Column(
              children: [
                _TeamColumnsHeader(
                  homeTeam: prediction.homeTeam,
                  awayTeam: prediction.awayTeam,
                ),
                if (_hasExpectedPair(
                  prediction,
                  'home_corners',
                  'away_corners',
                ))
                  _StatRow(
                    label: 'Córners',
                    icon: Icons.flag_outlined,
                    home: prediction.displayExpectedValue('home_corners'),
                    away: prediction.displayExpectedValue('away_corners'),
                  ),
                if (_hasExpectedPair(prediction, 'home_shots', 'away_shots'))
                  _StatRow(
                    label: 'Remates',
                    icon: Icons.gps_fixed,
                    home: prediction.displayExpectedValue('home_shots'),
                    away: prediction.displayExpectedValue('away_shots'),
                  ),
                if (_hasExpectedPair(
                  prediction,
                  'home_shots_on_target',
                  'away_shots_on_target',
                ))
                  _StatRow(
                    label: 'Remates al arco',
                    icon: Icons.sports_soccer_outlined,
                    home: prediction.displayExpectedValue(
                      'home_shots_on_target',
                    ),
                    away: prediction.displayExpectedValue(
                      'away_shots_on_target',
                    ),
                  ),
                if (prediction.isStatisticalBaseline) ...[
                  const SizedBox(height: 14),
                  _StatisticsSourceNotice(prediction: prediction),
                ],
              ],
            ),
          ),
        if (prediction.possibleScorers.isNotEmpty ||
            prediction.isStatisticalBaseline ||
            isLowConfidenceFallback)
          _Section(
            icon: Icons.sports_soccer_outlined,
            title: 'Goleadores probables',
            child: prediction.possibleScorers.isEmpty
                ? const _IndividualDataUnavailable()
                : Column(
                    children: prediction.possibleScorers
                        .take(5)
                        .map((player) => _PlayerProbabilityRow(player: player))
                        .toList(),
                  ),
          ),
        if (prediction.possibleAssistants.isNotEmpty ||
            prediction.isStatisticalBaseline ||
            isLowConfidenceFallback)
          _Section(
            icon: Icons.assistant_outlined,
            title: 'Asistidores probables',
            child: prediction.possibleAssistants.isEmpty
                ? const _IndividualDataUnavailable()
                : Column(
                    children: prediction.possibleAssistants
                        .take(5)
                        .map((player) => _PlayerProbabilityRow(player: player))
                        .toList(),
                  ),
          ),
        _StatusFooter(
          prediction: prediction,
          isLowConfidenceFallback: isLowConfidenceFallback,
        ),
      ],
    );
  }
}

class _LowConfidenceFallbackNotice extends StatelessWidget {
  const _LowConfidenceFallbackNotice({required this.isSingleTeamProfile});

  final bool isSingleTeamProfile;

  @override
  Widget build(BuildContext context) {
    final message = isSingleTeamProfile
        ? 'Baja confianza: solo uno de los equipos tiene historial; '
              'no publicamos 1X2. Tómala solo como guía.'
        : 'Baja confianza: usa datos parciales de los equipos y una '
              'referencia general. Tómala solo como guía.';
    return Container(
      margin: const EdgeInsets.fromLTRB(20, 18, 20, 0),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.amber.withValues(alpha: .08),
        border: Border.all(color: AppColors.amber.withValues(alpha: .45)),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(
            Icons.warning_amber_rounded,
            color: AppColors.amber,
            size: 23,
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'Predicción orientativa',
                  style: TextStyle(
                    color: AppColors.amber,
                    fontWeight: FontWeight.w800,
                  ),
                ),
                const SizedBox(height: 5),
                Text(
                  message,
                  style: const TextStyle(color: AppColors.muted, height: 1.35),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _MatchHeader extends StatelessWidget {
  const _MatchHeader({
    required this.fixture,
    this.homeCountry,
    this.awayCountry,
  });

  final FixtureSummary fixture;
  final String? homeCountry;
  final String? awayCountry;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(24, 20, 24, 28),
      decoration: const BoxDecoration(
        border: Border(bottom: BorderSide(color: AppColors.border)),
      ),
      child: Column(
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(Icons.sports_soccer, color: AppColors.muted, size: 19),
              const SizedBox(width: 8),
              Text(
                fixture.leagueName,
                style: const TextStyle(
                  color: AppColors.text,
                  fontSize: 17,
                  fontWeight: FontWeight.w800,
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Text(
            DateFormat('dd/MM/yyyy • HH:mm').format(fixture.displayKickoff),
            style: const TextStyle(color: AppColors.muted, fontSize: 16),
          ),
          const SizedBox(height: 22),
          Row(
            children: [
              Expanded(
                child: _HeaderTeam(
                  team: fixture.homeTeam,
                  country: homeCountry,
                  logoUrl: fixture.homeTeamLogoUrl,
                ),
              ),
              const Padding(
                padding: EdgeInsets.symmetric(horizontal: 12),
                child: Text(
                  'VS',
                  style: TextStyle(
                    color: AppColors.muted,
                    fontWeight: FontWeight.w800,
                  ),
                ),
              ),
              Expanded(
                child: _HeaderTeam(
                  team: fixture.awayTeam,
                  country: awayCountry,
                  logoUrl: fixture.awayTeamLogoUrl,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _HeaderTeam extends StatelessWidget {
  const _HeaderTeam({required this.team, this.country, this.logoUrl});

  final String team;
  final String? country;
  final String? logoUrl;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        TeamMark(team: team, logoUrl: logoUrl, size: 68),
        const SizedBox(height: 11),
        Text(
          team,
          textAlign: TextAlign.center,
          maxLines: 2,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(
            color: AppColors.text,
            fontSize: 18,
            fontWeight: FontWeight.w800,
          ),
        ),
        if (country != null) ...[
          const SizedBox(height: 5),
          Text(
            country!,
            textAlign: TextAlign.center,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(
              color: AppColors.muted,
              fontSize: 12,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ],
    );
  }
}

class _Section extends StatelessWidget {
  const _Section({
    required this.icon,
    required this.title,
    required this.child,
  });

  final IconData icon;
  final String title;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(20, 22, 20, 20),
      decoration: const BoxDecoration(
        border: Border(bottom: BorderSide(color: AppColors.border)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(icon, color: AppColors.primary, size: 24),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  title,
                  style: Theme.of(context).textTheme.titleLarge,
                ),
              ),
            ],
          ),
          const SizedBox(height: 20),
          child,
        ],
      ),
    );
  }
}

class _ProbabilityPanel extends StatelessWidget {
  const _ProbabilityPanel({required this.prediction});

  final Prediction prediction;

  @override
  Widget build(BuildContext context) {
    final values = [prediction.homeWin, prediction.draw, prediction.awayWin];
    const colors = [AppColors.primary, AppColors.blue, AppColors.violet];
    final labels = [prediction.homeTeam, 'Empate', prediction.awayTeam];

    return Column(
      children: [
        ClipRRect(
          borderRadius: BorderRadius.circular(10),
          child: SizedBox(
            height: 58,
            child: Row(
              children: [
                for (var index = 0; index < values.length; index++)
                  Expanded(
                    flex: (values[index] * 1000).round().clamp(1, 1000),
                    child: Container(
                      alignment: Alignment.center,
                      color: colors[index],
                      child: Text(
                        _percent(values[index]),
                        style: const TextStyle(
                          color: Colors.white,
                          fontSize: 19,
                          fontWeight: FontWeight.w900,
                        ),
                      ),
                    ),
                  ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 18),
        Row(
          children: [
            for (var index = 0; index < values.length; index++)
              Expanded(
                child: Column(
                  children: [
                    Row(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Container(
                          width: 9,
                          height: 9,
                          decoration: BoxDecoration(
                            color: colors[index],
                            shape: BoxShape.circle,
                          ),
                        ),
                        const SizedBox(width: 6),
                        Flexible(
                          child: Text(
                            labels[index],
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              color: AppColors.muted,
                              fontSize: 12,
                            ),
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 6),
                    Text(
                      _percent(values[index]),
                      style: TextStyle(
                        color: colors[index],
                        fontSize: 22,
                        fontWeight: FontWeight.w900,
                      ),
                    ),
                  ],
                ),
              ),
          ],
        ),
      ],
    );
  }
}

class _StatRow extends StatelessWidget {
  const _StatRow({
    required this.label,
    required this.icon,
    required this.home,
    required this.away,
  });

  final String label;
  final IconData icon;
  final double? home;
  final double? away;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 15),
      decoration: const BoxDecoration(
        border: Border(bottom: BorderSide(color: AppColors.border)),
      ),
      child: Row(
        children: [
          Expanded(
            child: Text(
              _number(home),
              style: const TextStyle(
                color: AppColors.primary,
                fontSize: 20,
                fontWeight: FontWeight.w900,
              ),
            ),
          ),
          Expanded(
            flex: 2,
            child: Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(icon, color: AppColors.muted, size: 20),
                const SizedBox(width: 8),
                Flexible(
                  child: Text(
                    label,
                    textAlign: TextAlign.center,
                    style: const TextStyle(color: AppColors.muted),
                  ),
                ),
              ],
            ),
          ),
          Expanded(
            child: Text(
              _number(away),
              textAlign: TextAlign.right,
              style: const TextStyle(
                color: AppColors.blue,
                fontSize: 20,
                fontWeight: FontWeight.w900,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _TeamColumnsHeader extends StatelessWidget {
  const _TeamColumnsHeader({required this.homeTeam, required this.awayTeam});

  final String homeTeam;
  final String awayTeam;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 4),
      child: Row(
        children: [
          Expanded(
            child: Text(
              homeTeam,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(
                color: AppColors.primary,
                fontSize: 12,
                fontWeight: FontWeight.w700,
              ),
            ),
          ),
          const Expanded(
            flex: 2,
            child: Text(
              'PROMEDIO ESPERADO',
              textAlign: TextAlign.center,
              style: TextStyle(
                color: AppColors.muted,
                fontSize: 10,
                fontWeight: FontWeight.w700,
                letterSpacing: .5,
              ),
            ),
          ),
          Expanded(
            child: Text(
              awayTeam,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              textAlign: TextAlign.right,
              style: const TextStyle(
                color: AppColors.blue,
                fontSize: 12,
                fontWeight: FontWeight.w700,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _GoalLinesPanel extends StatelessWidget {
  const _GoalLinesPanel({required this.goalLines});

  static const supportedLines = [0.5, 1.5, 2.5, 3.5, 4.5];

  final List<GoalLineProbability> goalLines;

  @override
  Widget build(BuildContext context) {
    final probabilities = {
      for (final goalLine in goalLines)
        goalLine.line.toStringAsFixed(1): goalLine.probability,
    };

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text(
          'Probabilidad de que el partido supere cada línea.',
          style: TextStyle(color: AppColors.muted, fontSize: 13),
        ),
        const SizedBox(height: 10),
        for (final line in supportedLines)
          _GoalLineRow(
            line: line,
            probability: probabilities[line.toStringAsFixed(1)],
          ),
      ],
    );
  }
}

class _GoalLineRow extends StatelessWidget {
  const _GoalLineRow({required this.line, required this.probability});

  final double line;
  final double? probability;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  'Más de ${line.toStringAsFixed(1)} goles',
                  style: const TextStyle(
                    color: AppColors.text,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
              Text(
                probability == null ? 'Sin datos' : _percent(probability!),
                style: TextStyle(
                  color: probability == null
                      ? AppColors.muted
                      : AppColors.primary,
                  fontSize: 17,
                  fontWeight: FontWeight.w900,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          ClipRRect(
            borderRadius: BorderRadius.circular(999),
            child: LinearProgressIndicator(
              minHeight: 8,
              value: probability?.clamp(0, 1) ?? 0,
              color: probability == null ? AppColors.muted : AppColors.primary,
              backgroundColor: AppColors.surfaceStrong,
            ),
          ),
        ],
      ),
    );
  }
}

class _PlayerProbabilityRow extends StatelessWidget {
  const _PlayerProbabilityRow({required this.player});

  final PossibleScorer player;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 12),
      decoration: const BoxDecoration(
        border: Border(bottom: BorderSide(color: AppColors.border)),
      ),
      child: Column(
        children: [
          Row(
            children: [
              const Icon(
                Icons.person_outline_rounded,
                color: AppColors.muted,
                size: 22,
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      player.player,
                      style: const TextStyle(
                        color: AppColors.text,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      player.team,
                      style: const TextStyle(
                        color: AppColors.muted,
                        fontSize: 13,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 12),
              Text(
                _percent(player.probability),
                style: const TextStyle(
                  color: AppColors.primary,
                  fontSize: 17,
                  fontWeight: FontWeight.w900,
                ),
              ),
            ],
          ),
          const SizedBox(height: 9),
          ClipRRect(
            borderRadius: BorderRadius.circular(999),
            child: LinearProgressIndicator(
              minHeight: 6,
              value: player.probability.clamp(0, 1),
              color: AppColors.primary,
              backgroundColor: AppColors.surfaceStrong,
            ),
          ),
        ],
      ),
    );
  }
}

class _IndividualDataUnavailable extends StatelessWidget {
  const _IndividualDataUnavailable();

  @override
  Widget build(BuildContext context) {
    return const Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(Icons.info_outline_rounded, color: AppColors.muted, size: 21),
        SizedBox(width: 10),
        Expanded(
          child: Text(
            'Aún no hay plantel o historial individual reciente suficiente.',
            style: TextStyle(color: AppColors.muted, height: 1.4),
          ),
        ),
      ],
    );
  }
}

class _StatisticsSourceNotice extends StatelessWidget {
  const _StatisticsSourceNotice({required this.prediction});

  final Prediction prediction;

  @override
  Widget build(BuildContext context) {
    final rows = prediction.statisticsReferenceRows;
    final isPeruReference = prediction.statisticsReferenceLeagueId == 281;
    final reference = isPeruReference
        ? 'Liga 1 de Perú'
        : 'otra liga disponible';
    final sample = rows == null ? '' : ' ($rows partidos por localía)';
    final message = prediction.usesCrossLeagueStatisticsReference
        ? 'Algunos valores usan una referencia histórica de baja confianza: '
              '$reference$sample. Úsala solo como guía.'
        : 'Muestra histórica reducida$sample. Úsala solo como guía.';

    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Icon(
          Icons.info_outline_rounded,
          color: AppColors.amber,
          size: 20,
        ),
        const SizedBox(width: 9),
        Expanded(
          child: Text(
            message,
            style: const TextStyle(
              color: AppColors.muted,
              fontSize: 13,
              height: 1.4,
            ),
          ),
        ),
      ],
    );
  }
}

class _StatusFooter extends StatelessWidget {
  const _StatusFooter({
    required this.prediction,
    required this.isLowConfidenceFallback,
  });

  final Prediction prediction;
  final bool isLowConfidenceFallback;

  @override
  Widget build(BuildContext context) {
    final crossLeagueCalibration = prediction.crossLeagueCalibration;
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 24, 20, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.schedule, color: AppColors.muted, size: 21),
              const SizedBox(width: 9),
              Text(
                _updatedLabel(prediction.updatedAt),
                style: const TextStyle(color: AppColors.muted),
              ),
            ],
          ),
          const SizedBox(height: 13),
          if (isLowConfidenceFallback) ...[
            const Row(
              children: [
                Icon(
                  Icons.warning_amber_rounded,
                  color: AppColors.amber,
                  size: 22,
                ),
                SizedBox(width: 9),
                Expanded(
                  child: Text(
                    'Predicción orientativa • baja confianza',
                    style: TextStyle(
                      color: AppColors.amber,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            const Text(
              'No es un modelo validado para esta competición y no garantiza resultados.',
              style: TextStyle(color: AppColors.muted, fontSize: 13),
            ),
          ] else if (prediction.isStatisticalBaseline) ...[
            const Row(
              children: [
                Icon(
                  Icons.model_training_outlined,
                  color: AppColors.amber,
                  size: 22,
                ),
                SizedBox(width: 9),
                Expanded(
                  child: Text(
                    'Modelo estadístico histórico • Poisson',
                    style: TextStyle(
                      color: AppColors.amber,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
              ],
            ),
            if (prediction.homeVenueSample != null &&
                prediction.awayVenueSample != null) ...[
              const SizedBox(height: 8),
              Text(
                'Muestra por localía: ${prediction.homeVenueSample} del local y '
                '${prediction.awayVenueSample} del visitante.',
                style: const TextStyle(color: AppColors.muted, fontSize: 13),
              ),
            ],
            const SizedBox(height: 8),
            const Text(
              'Todas las estimaciones son orientativas y no garantizan resultados.',
              style: TextStyle(color: AppColors.muted, fontSize: 13),
            ),
          ] else
            Row(
              children: [
                Icon(
                  prediction.lineupsConfirmed
                      ? Icons.groups_2_outlined
                      : Icons.group_outlined,
                  color: prediction.lineupsConfirmed
                      ? AppColors.primary
                      : AppColors.amber,
                  size: 22,
                ),
                const SizedBox(width: 9),
                Expanded(
                  child: Text(
                    prediction.lineupsConfirmed
                        ? 'Alineaciones confirmadas'
                        : '${_stageLabel(prediction.stage)} • alineaciones pendientes',
                    style: TextStyle(
                      color: prediction.lineupsConfirmed
                          ? AppColors.primary
                          : AppColors.amber,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
              ],
            ),
          if (crossLeagueCalibration != null) ...[
            const SizedBox(height: 14),
            _CrossLeagueCalibrationNote(calibration: crossLeagueCalibration),
          ],
        ],
      ),
    );
  }
}

class _CrossLeagueCalibrationNote extends StatelessWidget {
  const _CrossLeagueCalibrationNote({required this.calibration});

  final CrossLeagueCalibration calibration;

  @override
  Widget build(BuildContext context) {
    final factors =
        '${calibration.homeCompetition} '
        '${calibration.homeFactor.toStringAsFixed(2)} · '
        '${calibration.awayCompetition} '
        '${calibration.awayFactor.toStringAsFixed(2)}';
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border.all(color: AppColors.border),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(Icons.tune_rounded, color: AppColors.primary, size: 20),
          const SizedBox(width: 9),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'Calibración orientativa por competición',
                  style: TextStyle(
                    color: AppColors.text,
                    fontSize: 13,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(height: 4),
                Text(
                  '$factors — factores de contexto, no probabilidades.',
                  style: const TextStyle(
                    color: AppColors.muted,
                    fontSize: 12,
                    height: 1.35,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _PredictionState extends StatelessWidget {
  const _PredictionState({
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
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(30),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (loading)
              const CircularProgressIndicator()
            else
              Icon(icon, size: 52, color: AppColors.muted),
            const SizedBox(height: 22),
            Text(
              title,
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.titleLarge,
            ),
            const SizedBox(height: 9),
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
      ),
    );
  }
}

String _percent(double value) => '${(value * 100).round()}%';

bool _hasExpectedPair(Prediction prediction, String homeKey, String awayKey) =>
    prediction.displayExpectedValue(homeKey) != null ||
    prediction.displayExpectedValue(awayKey) != null;

String _number(double? value) => value == null ? '—' : value.toStringAsFixed(1);

String _updatedLabel(DateTime updatedAt) {
  final difference = DateTime.now().difference(updatedAt);
  if (difference.inMinutes < 1) {
    return 'Actualizado ahora';
  }
  if (difference.inMinutes < 60) {
    return 'Actualizado hace ${difference.inMinutes} min';
  }
  if (difference.inHours < 24) {
    return 'Actualizado hace ${difference.inHours} h';
  }
  return 'Actualizado ${DateFormat('dd/MM HH:mm').format(updatedAt)}';
}

String _stageLabel(String stage) => switch (stage) {
  'initial' => 'Predicción inicial',
  'prematch' => 'Predicción prepartido',
  'waiting_lineups' => 'Esperando alineaciones',
  'lineups_confirmed' => 'Alineaciones confirmadas',
  'final_prematch' => 'Predicción final prepartido',
  _ => 'Predicción actual',
};
