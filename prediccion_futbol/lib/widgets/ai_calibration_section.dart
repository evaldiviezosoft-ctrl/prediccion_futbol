part of '../screens/prediction_screen.dart';

class _AiCalibrationSlot extends StatefulWidget {
  const _AiCalibrationSlot({
    required this.stream,
    required this.baseForecast,
    required this.onRetry,
  });

  final Stream<AiCalibrationResult> stream;
  final List<ProbableForecastPick> baseForecast;
  final VoidCallback onRetry;

  @override
  State<_AiCalibrationSlot> createState() => _AiCalibrationSlotState();
}

class _AiCalibrationSlotState extends State<_AiCalibrationSlot>
    with AutomaticKeepAliveClientMixin {
  @override
  bool get wantKeepAlive => true;

  @override
  Widget build(BuildContext context) {
    super.build(context);
    return _Section(
      icon: Icons.insights_rounded,
      title: 'Pronóstico probable',
      child: StreamBuilder<AiCalibrationResult>(
        stream: widget.stream,
        builder: (context, snapshot) {
          if (snapshot.hasError) {
            if (widget.baseForecast.isNotEmpty) {
              return _ProbableForecastCard(
                picks: widget.baseForecast,
                finalized: false,
              );
            }
            return _AiState(
              key: const ValueKey('ai-calibration-error'),
              icon: Icons.cloud_off_outlined,
              title: 'Análisis contextual no disponible',
              message:
                  'La predicción estadística sigue visible. Puedes reintentar solamente el análisis IA.',
              actionLabel: 'Reintentar análisis',
              onAction: widget.onRetry,
            );
          }
          if (!snapshot.hasData) {
            if (widget.baseForecast.isNotEmpty) {
              return _ProbableForecastCard(
                picks: widget.baseForecast,
                finalized: false,
              );
            }
            return const _AiState(
              key: ValueKey('ai-calibration-loading'),
              icon: Icons.auto_awesome_outlined,
              title: 'Consultando análisis contextual',
              message:
                  'La predicción base permanece disponible mientras consultamos la calibración.',
              loading: true,
            );
          }

          final result = snapshot.data!;
          return switch (result.status) {
            AiCalibrationStatus.pending when widget.baseForecast.isNotEmpty =>
              _ProbableForecastCard(
                picks: widget.baseForecast,
                finalized: false,
              ),
            AiCalibrationStatus.pending => _AiState(
              key: const ValueKey('ai-calibration-pending'),
              icon: Icons.schedule_rounded,
              title: 'Calibración IA en preparación',
              message:
                  result.safeMessage ??
                  'Contrastamos la predicción con la evidencia disponible. Se actualizará automáticamente.',
              loading: true,
            ),
            AiCalibrationStatus.unavailable
                when widget.baseForecast.isNotEmpty =>
              _ProbableForecastCard(
                picks: widget.baseForecast,
                finalized: false,
              ),
            AiCalibrationStatus.unavailable => _AiState(
              key: const ValueKey('ai-calibration-unavailable'),
              icon: Icons.info_outline_rounded,
              title: 'Calibración IA no disponible',
              message:
                  result.safeMessage ??
                  'No hay datos suficientes para calibrar este partido sin inventar información.',
              actionLabel: 'Comprobar de nuevo',
              onAction: widget.onRetry,
            ),
            AiCalibrationStatus.error when widget.baseForecast.isNotEmpty =>
              _ProbableForecastCard(
                picks: widget.baseForecast,
                finalized: false,
              ),
            AiCalibrationStatus.error => _AiState(
              key: const ValueKey('ai-calibration-error'),
              icon: Icons.cloud_off_outlined,
              title: 'No pudimos completar la calibración',
              message:
                  result.safeMessage ??
                  'La predicción estadística no se ha perdido y sigue disponible.',
              actionLabel: 'Comprobar estado',
              onAction: widget.onRetry,
            ),
            AiCalibrationStatus.updated => _AiUpdated(
              key: const ValueKey('ai-calibration-updated'),
              result: result,
              fallbackPicks: widget.baseForecast,
            ),
          };
        },
      ),
    );
  }
}

class _AiUpdated extends StatelessWidget {
  const _AiUpdated({
    super.key,
    required this.result,
    required this.fallbackPicks,
  });

  final AiCalibrationResult result;
  final List<ProbableForecastPick> fallbackPicks;

  @override
  Widget build(BuildContext context) {
    final analysis = result.analysis!;
    final picks = analysis.probableForecast.isNotEmpty
        ? analysis.probableForecast
        : fallbackPicks;
    if (picks.isEmpty) {
      return const _AiState(
        icon: Icons.hourglass_empty_rounded,
        title: 'Pronóstico en preparación',
        message:
            'El modelo todavía no tiene respaldo suficiente para publicar '
            'uno de estos mercados.',
      );
    }
    return _ProbableForecastCard(
      picks: picks,
      notes: analysis.notes ?? const [],
      finalized: analysis.forecastFinalized,
    );
  }
}

class _ProbableForecastCard extends StatelessWidget {
  const _ProbableForecastCard({
    required this.picks,
    required this.finalized,
    this.notes = const [],
  });

  final List<ProbableForecastPick> picks;
  final List<AiCalibrationNote> notes;
  final bool finalized;

  @override
  Widget build(BuildContext context) {
    final explanation = notes
        .take(5)
        .map((note) => note.text.trim())
        .where((text) => text.isNotEmpty)
        .join('\n');
    return Container(
      key: const ValueKey('probable-forecast-card'),
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [
            AppColors.surface,
            AppColors.primary.withValues(alpha: .075),
          ],
        ),
        border: Border.all(color: AppColors.primary.withValues(alpha: .38)),
        borderRadius: BorderRadius.circular(18),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: .16),
            blurRadius: 18,
            offset: const Offset(0, 8),
          ),
        ],
      ),
      child: Column(
        children: [
          ...picks
              .take(7)
              .map(
                (pick) => _ProbableForecastRow(
                  pick: pick,
                  showDivider: pick != picks.take(7).last,
                ),
              ),
          if (explanation.isNotEmpty) ...[
            Divider(height: 1, color: AppColors.border.withValues(alpha: .8)),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 13, 16, 10),
              child: Align(
                alignment: Alignment.centerLeft,
                child: Text(
                  explanation,
                  key: const ValueKey('probable-forecast-explanation'),
                  maxLines: 5,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    color: AppColors.muted,
                    fontSize: 12,
                    height: 1.4,
                  ),
                ),
              ),
            ),
          ],
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 9, 16, 13),
            child: Row(
              children: [
                Icon(
                  finalized ? Icons.verified_rounded : Icons.lock_clock_rounded,
                  size: 16,
                  color: finalized ? AppColors.primary : AppColors.muted,
                ),
                const SizedBox(width: 7),
                Expanded(
                  child: Text(
                    finalized
                        ? 'Confirmado con las alineaciones oficiales.'
                        : 'Se mantiene fijo hasta confirmar alineaciones.',
                    style: const TextStyle(
                      color: AppColors.muted,
                      fontSize: 11,
                      height: 1.3,
                    ),
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

class _ProbableForecastRow extends StatelessWidget {
  const _ProbableForecastRow({required this.pick, required this.showDivider});

  final ProbableForecastPick pick;
  final bool showDivider;

  @override
  Widget build(BuildContext context) {
    final probability = pick.probability;
    return Container(
      decoration: BoxDecoration(
        border: showDivider
            ? Border(bottom: BorderSide(color: AppColors.border))
            : null,
      ),
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      child: Row(
        children: [
          Container(
            width: 38,
            height: 38,
            decoration: BoxDecoration(
              color: AppColors.primary.withValues(alpha: .12),
              borderRadius: BorderRadius.circular(11),
            ),
            child: Icon(
              _forecastIcon(pick.category),
              color: AppColors.primary,
              size: 20,
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  pick.title,
                  style: const TextStyle(
                    color: AppColors.muted,
                    fontSize: 11,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(height: 3),
                Text(
                  pick.prediction,
                  style: const TextStyle(
                    color: AppColors.text,
                    fontSize: 15,
                    fontWeight: FontWeight.w900,
                  ),
                ),
              ],
            ),
          ),
          if (probability != null)
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 6),
              decoration: BoxDecoration(
                color: AppColors.primary.withValues(alpha: .12),
                borderRadius: BorderRadius.circular(999),
              ),
              child: Text(
                '${(probability * 100).round()}%',
                style: const TextStyle(
                  color: AppColors.primary,
                  fontSize: 12,
                  fontWeight: FontWeight.w900,
                ),
              ),
            ),
        ],
      ),
    );
  }
}

IconData _forecastIcon(String category) => switch (category) {
  'goals' || 'half_goals' => Icons.sports_soccer_rounded,
  'corners' => Icons.flag_rounded,
  'cards' => Icons.style_rounded,
  'shots' => Icons.gps_fixed_rounded,
  'saves' => Icons.back_hand_rounded,
  'shots_on_target' => Icons.adjust_rounded,
  _ => Icons.query_stats_rounded,
};

class _AiState extends StatelessWidget {
  const _AiState({
    super.key,
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
    return Container(
      padding: const EdgeInsets.all(15),
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border.all(color: AppColors.border),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (loading)
            const SizedBox(
              width: 22,
              height: 22,
              child: CircularProgressIndicator(strokeWidth: 2.5),
            )
          else
            Icon(icon, color: AppColors.muted, size: 22),
          const SizedBox(width: 11),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: const TextStyle(
                    color: AppColors.text,
                    fontWeight: FontWeight.w800,
                  ),
                ),
                const SizedBox(height: 5),
                Text(
                  message,
                  style: const TextStyle(
                    color: AppColors.muted,
                    fontSize: 13,
                    height: 1.4,
                  ),
                ),
                if (actionLabel != null && onAction != null)
                  TextButton(onPressed: onAction, child: Text(actionLabel!)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

// Kept temporarily to parse and render historical snapshots during rollback
// testing; the production path above never exposes these verbose blocks.
// ignore: unused_element
class _AiUpdatedLegacy extends StatelessWidget {
  const _AiUpdatedLegacy({
    required this.result,
    required this.homeTeam,
    required this.awayTeam,
    required this.showProbabilityComparison,
    required this.onRefresh,
  });

  final AiCalibrationResult result;
  final String homeTeam;
  final String awayTeam;
  final bool showProbabilityComparison;
  final VoidCallback onRefresh;

  @override
  Widget build(BuildContext context) {
    final analysis = result.analysis!;
    final usesCompactNotes = analysis.usesCompactNotes;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Wrap(
          spacing: 7,
          runSpacing: 7,
          children: [
            _AiTag('Datos: ${_aiQuality(analysis.dataQuality)}'),
            _AiTag(
              analysis.lineupsConsidered
                  ? 'Alineaciones incluidas'
                  : 'Alineaciones pendientes',
              warning: !analysis.lineupsConsidered,
            ),
            if (analysis.matchType.toLowerCase() == 'friendly')
              const _AiTag('Partido amistoso'),
            if (result.isStale)
              const _AiTag('Revisión desactualizada', warning: true),
          ],
        ),
        if (result.safeMessage != null) ...[
          const SizedBox(height: 12),
          _AiNotice(
            icon: result.reasonCode == 'calibration_refresh_failed'
                ? Icons.warning_amber_rounded
                : Icons.sync_rounded,
            message: result.safeMessage!,
          ),
        ],
        const SizedBox(height: 18),
        if (showProbabilityComparison && analysis.showOneXTwo)
          _AiBlock(
            title: 'Modelo base vs. calibración',
            icon: Icons.tune_rounded,
            child: _AiProbabilities(
              base: analysis.baseProbabilities,
              adjusted: analysis.adjustedProbabilities,
              labels: [homeTeam, 'Empate', awayTeam],
            ),
          )
        else
          const _AiNotice(
            icon: Icons.info_outline_rounded,
            message:
                'No comparamos 1X2 porque todavía falta un perfil histórico '
                'suficiente para uno de los equipos.',
          ),
        if (usesCompactNotes)
          if (analysis.notes!.isNotEmpty)
            _AiBlock(
              title: 'Lectura contextual',
              icon: Icons.notes_rounded,
              child: _AiCompactNotes(analysis.notes!),
            )
          else
            const _AiNotice(
              icon: Icons.info_outline_rounded,
              message: 'No hay notas contextuales adicionales.',
            )
        else ...[
          if (analysis.adjustments.isNotEmpty)
            _AiBlock(
              title: 'Factores y evidencia',
              icon: Icons.fact_check_outlined,
              child: Column(
                children: analysis.adjustments
                    .map((item) => _AiAdjustmentRow(item))
                    .toList(),
              ),
            ),
          if (analysis.preparationComparison.isNotEmpty)
            _AiBlock(
              title: 'Comparación de preparación',
              icon: Icons.fitness_center_outlined,
              child: _AiContextRows(analysis.preparationComparison),
            ),
          if (analysis.rotationEffect.isNotEmpty)
            _AiBlock(
              title: 'Efecto de rotaciones',
              icon: Icons.groups_outlined,
              child: _AiContextRows(analysis.rotationEffect),
            ),
          if (analysis.projections.isNotEmpty)
            _AiBlock(
              title: 'Proyecciones estadísticas',
              icon: Icons.query_stats_rounded,
              child: _AiProjections(
                values: analysis.projections,
                homeTeam: homeTeam,
                awayTeam: awayTeam,
              ),
            ),
        ],
        if (analysis.recommendedMarket != null)
          _AiMarket(
            title: 'Mercado con mayor respaldo',
            value: analysis.recommendedMarket!,
            primary: true,
            showJustification: !usesCompactNotes,
          ),
        if (analysis.conservativeAlternative != null)
          _AiMarket(
            title: 'Alternativa conservadora',
            value: analysis.conservativeAlternative!,
            showJustification: !usesCompactNotes,
          ),
        if (!usesCompactNotes && analysis.risks.isNotEmpty)
          _AiList(title: 'Riesgos', values: analysis.risks, warning: true),
        if (!usesCompactNotes && analysis.missingData.isNotEmpty)
          _AiList(title: 'Datos faltantes', values: analysis.missingData),
        if (!usesCompactNotes && analysis.possibleModelErrors.isNotEmpty)
          _AiList(
            title: 'Posibles límites del modelo',
            values: analysis.possibleModelErrors,
          ),
        if (analysis.refreshWithLineups && !analysis.lineupsConsidered)
          const Padding(
            padding: EdgeInsets.only(top: 14),
            child: Text(
              '↻ Conviene actualizar el análisis cuando se confirmen las alineaciones.',
              style: TextStyle(
                color: AppColors.amber,
                fontSize: 12,
                height: 1.4,
              ),
            ),
          ),
        const SizedBox(height: 16),
        Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: AppColors.surface.withValues(alpha: .72),
            borderRadius: BorderRadius.circular(10),
          ),
          child: const Text(
            'La IA calibra el modelo estadístico con datos disponibles; no '
            'inventa información. Es orientativo y no garantiza resultados '
            'ni beneficios.',
            style: TextStyle(
              color: AppColors.muted,
              fontSize: 12,
              height: 1.45,
            ),
          ),
        ),
        Row(
          children: [
            const Icon(Icons.schedule, color: AppColors.muted, size: 17),
            const SizedBox(width: 7),
            Expanded(
              child: Text(
                result.generatedAt == null
                    ? analysis.modelLabel
                    : '${analysis.modelLabel} · ${_updatedLabel(result.generatedAt!)}',
                style: const TextStyle(color: AppColors.muted, fontSize: 12),
              ),
            ),
            IconButton(
              tooltip: 'Comprobar actualización',
              visualDensity: VisualDensity.compact,
              onPressed: onRefresh,
              icon: const Icon(Icons.refresh_rounded, size: 20),
            ),
          ],
        ),
      ],
    );
  }
}

class _AiTag extends StatelessWidget {
  const _AiTag(this.label, {this.warning = false});

  final String label;
  final bool warning;

  @override
  Widget build(BuildContext context) {
    final color = warning ? AppColors.amber : AppColors.primary;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 6),
      decoration: BoxDecoration(
        color: color.withValues(alpha: .1),
        border: Border.all(color: color.withValues(alpha: .35)),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        label,
        style: TextStyle(
          color: color,
          fontSize: 11,
          fontWeight: FontWeight.w700,
        ),
      ),
    );
  }
}

class _AiNotice extends StatelessWidget {
  const _AiNotice({required this.icon, required this.message});

  final IconData icon;
  final String message;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(bottom: 18),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border.all(color: AppColors.border),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, color: AppColors.muted, size: 18),
          const SizedBox(width: 9),
          Expanded(
            child: Text(
              message,
              style: const TextStyle(
                color: AppColors.muted,
                fontSize: 12,
                height: 1.4,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _AiBlock extends StatelessWidget {
  const _AiBlock({
    required this.title,
    required this.icon,
    required this.child,
  });

  final String title;
  final IconData icon;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(icon, color: AppColors.blue, size: 19),
              const SizedBox(width: 7),
              Text(
                title,
                style: const TextStyle(
                  color: AppColors.text,
                  fontSize: 15,
                  fontWeight: FontWeight.w800,
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          child,
        ],
      ),
    );
  }
}

class _AiProbabilities extends StatelessWidget {
  const _AiProbabilities({
    required this.base,
    required this.adjusted,
    required this.labels,
  });

  final AiProbabilityTriplet base;
  final AiProbabilityTriplet adjusted;
  final List<String> labels;

  @override
  Widget build(BuildContext context) {
    final baseValues = [base.home, base.draw, base.away];
    final adjustedValues = [adjusted.home, adjusted.draw, adjusted.away];
    return _AiSurface(
      child: Column(
        children: [
          const _AiTableHeader(first: '', values: ['BASE', 'IA', 'CAMBIO']),
          for (var index = 0; index < 3; index++)
            _AiTableRow(
              first: labels[index],
              values: [
                _percent(baseValues[index]),
                _percent(adjustedValues[index]),
                _aiDelta(adjustedValues[index] - baseValues[index]),
              ],
              accentIndex: 1,
            ),
        ],
      ),
    );
  }
}

class _AiAdjustmentRow extends StatelessWidget {
  const _AiAdjustmentRow(this.value);

  final AiAdjustment value;

  @override
  Widget build(BuildContext context) {
    final impact = value.probabilityImpact;
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(11),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(10),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            _aiTerm(value.factor),
            style: const TextStyle(
              color: AppColors.text,
              fontSize: 13,
              fontWeight: FontWeight.w800,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            value.detail,
            style: const TextStyle(
              color: AppColors.muted,
              fontSize: 12,
              height: 1.4,
            ),
          ),
          if (value.evidence != null)
            Text(
              'Evidencia: ${value.evidence}',
              style: const TextStyle(color: AppColors.blue, fontSize: 11),
            ),
          if (impact != null)
            Text(
              'Impacto: local ${_aiDelta(impact.home)}, empate '
              '${_aiDelta(impact.draw)}, visitante ${_aiDelta(impact.away)}',
              style: const TextStyle(color: AppColors.muted, fontSize: 11),
            )
          else if (value.impactPercentagePoints != null)
            Text(
              'Impacto: ${_aiSide(value.benefitedSide)} '
              '${_aiPoints(value.impactPercentagePoints!)}',
              style: const TextStyle(color: AppColors.muted, fontSize: 11),
            ),
        ],
      ),
    );
  }
}

class _AiContextRows extends StatelessWidget {
  const _AiContextRows(this.values);

  final List<AiContextDetail> values;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: values
          .map(
            (item) => Padding(
              padding: const EdgeInsets.only(bottom: 7),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('• ', style: TextStyle(color: AppColors.blue)),
                  Expanded(
                    child: Text.rich(
                      TextSpan(
                        style: const TextStyle(
                          color: AppColors.muted,
                          fontSize: 12,
                          height: 1.4,
                        ),
                        children: [
                          TextSpan(
                            text: '${_aiTerm(item.label)}: ',
                            style: const TextStyle(
                              color: AppColors.text,
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                          TextSpan(text: item.value),
                        ],
                      ),
                    ),
                  ),
                ],
              ),
            ),
          )
          .toList(),
    );
  }
}

class _AiCompactNotes extends StatelessWidget {
  const _AiCompactNotes(this.values);

  final List<AiCalibrationNote> values;

  @override
  Widget build(BuildContext context) {
    final text = values
        .take(5)
        .map((value) => '• ${_aiNoteLabel(value.kind)}: ${value.text}')
        .join('\n');
    return _AiSurface(
      child: Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: Text(
          text,
          key: const ValueKey('ai-compact-notes'),
          maxLines: 5,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(
            color: AppColors.muted,
            fontSize: 12,
            height: 1.45,
          ),
        ),
      ),
    );
  }
}

class _AiProjections extends StatelessWidget {
  const _AiProjections({
    required this.values,
    required this.homeTeam,
    required this.awayTeam,
  });

  final List<AiProjection> values;
  final String homeTeam;
  final String awayTeam;

  @override
  Widget build(BuildContext context) {
    return _AiSurface(
      child: Column(
        children: [
          _AiTableHeader(
            first: '',
            values: [_aiShort(homeTeam), _aiShort(awayTeam), 'TOTAL'],
          ),
          for (final value in values)
            _AiTableRow(
              first: _aiTerm(value.metric),
              values: [
                _aiRange(value.home),
                _aiRange(value.away),
                _aiRange(value.total),
              ],
            ),
        ],
      ),
    );
  }
}

class _AiTableHeader extends StatelessWidget {
  const _AiTableHeader({required this.first, required this.values});

  final String first;
  final List<String> values;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(flex: 2, child: Text(first)),
        for (final value in values)
          Expanded(
            child: Text(
              value,
              textAlign: TextAlign.right,
              style: const TextStyle(
                color: AppColors.muted,
                fontSize: 9,
                fontWeight: FontWeight.w800,
              ),
            ),
          ),
      ],
    );
  }
}

class _AiTableRow extends StatelessWidget {
  const _AiTableRow({
    required this.first,
    required this.values,
    this.accentIndex,
  });

  final String first;
  final List<String> values;
  final int? accentIndex;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Row(
        children: [
          Expanded(
            flex: 2,
            child: Text(
              first,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(
                color: AppColors.text,
                fontSize: 12,
                fontWeight: FontWeight.w700,
              ),
            ),
          ),
          for (var index = 0; index < values.length; index++)
            Expanded(
              child: Text(
                values[index],
                textAlign: TextAlign.right,
                style: TextStyle(
                  color: index == accentIndex
                      ? AppColors.primary
                      : AppColors.muted,
                  fontSize: 12,
                  fontWeight: index == accentIndex
                      ? FontWeight.w800
                      : FontWeight.w500,
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _AiSurface extends StatelessWidget {
  const _AiSurface({required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(11, 10, 11, 4),
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border.all(color: AppColors.border),
        borderRadius: BorderRadius.circular(11),
      ),
      child: child,
    );
  }
}

class _AiMarket extends StatelessWidget {
  const _AiMarket({
    required this.title,
    required this.value,
    this.primary = false,
    this.showJustification = true,
  });

  final String title;
  final AiMarketRecommendation value;
  final bool primary;
  final bool showJustification;

  @override
  Widget build(BuildContext context) {
    final noBet = value.recommendsNoBet;
    final color = noBet
        ? AppColors.amber
        : primary
        ? AppColors.primary
        : AppColors.blue;
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.only(bottom: 11),
      padding: const EdgeInsets.all(13),
      decoration: BoxDecoration(
        color: color.withValues(alpha: .08),
        border: Border.all(color: color.withValues(alpha: .38)),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  title,
                  style: TextStyle(
                    color: color,
                    fontSize: 13,
                    fontWeight: FontWeight.w800,
                  ),
                ),
              ),
              Text(
                _aiConfidence(value.confidence),
                style: const TextStyle(
                  color: AppColors.muted,
                  fontSize: 9,
                  fontWeight: FontWeight.w800,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            noBet
                ? 'No hay una selección recomendable'
                : _aiMarketLabel(value.market),
            style: const TextStyle(
              color: AppColors.text,
              fontSize: 16,
              fontWeight: FontWeight.w900,
            ),
          ),
          if (showJustification) ...[
            const SizedBox(height: 5),
            Text(
              value.justification,
              style: const TextStyle(
                color: AppColors.muted,
                fontSize: 12,
                height: 1.4,
              ),
            ),
          ],
          if (value.minimumValueOdds != null)
            Text(
              '${value.marketDataAvailable ? 'Cuota mínima de valor: ' : 'Cuota teórica de referencia: '}'
              '${value.minimumValueOdds!.toStringAsFixed(2)}',
              style: TextStyle(
                color: color,
                fontSize: 12,
                fontWeight: FontWeight.w700,
              ),
            ),
          if (!value.marketDataAvailable && !noBet)
            const Text(
              'Sin cuotas recientes: aún no se ha comprobado una ventaja de mercado.',
              style: TextStyle(color: AppColors.muted, fontSize: 11),
            ),
          if (value.marketDataAvailable &&
              value.estimatedEdgePercentagePoints != null)
            Text(
              'Ventaja estimada: '
              '${_aiPoints(value.estimatedEdgePercentagePoints!)}',
              style: TextStyle(
                color: color,
                fontSize: 11,
                fontWeight: FontWeight.w700,
              ),
            ),
        ],
      ),
    );
  }
}

class _AiList extends StatelessWidget {
  const _AiList({
    required this.title,
    required this.values,
    this.warning = false,
  });

  final String title;
  final List<String> values;
  final bool warning;

  @override
  Widget build(BuildContext context) {
    final color = warning ? AppColors.amber : AppColors.muted;
    return Padding(
      padding: const EdgeInsets.only(top: 13),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            title,
            style: TextStyle(
              color: color,
              fontSize: 13,
              fontWeight: FontWeight.w800,
            ),
          ),
          const SizedBox(height: 5),
          for (final value in values)
            Text(
              '• $value',
              style: const TextStyle(
                color: AppColors.muted,
                fontSize: 12,
                height: 1.4,
              ),
            ),
        ],
      ),
    );
  }
}

String _aiQuality(String value) => switch (value.toLowerCase()) {
  'high' || 'alta' => 'alta',
  'medium' || 'media' => 'media',
  'low' || 'baja' => 'baja',
  _ => 'no disponible',
};

String _aiConfidence(String value) => switch (value.toLowerCase()) {
  'high' || 'alta' => 'CONFIANZA ALTA',
  'medium' || 'media' => 'CONFIANZA MEDIA',
  'low' || 'baja' => 'CONFIANZA BAJA',
  'no_bet' => 'SIN SELECCIÓN',
  _ => 'ORIENTATIVO',
};

String _aiDelta(double value) {
  final points = value * 100;
  final shown = (points - points.round()).abs() < .05
      ? points.round().toString()
      : points.toStringAsFixed(1);
  return '${points > .05 ? '+' : ''}$shown pp';
}

String _aiRange(AiProjectionRange? range) {
  if (range == null) return '—';
  String number(double value) => value == value.roundToDouble()
      ? value.toInt().toString()
      : value.toStringAsFixed(1);
  return (range.maximum - range.minimum).abs() < .05
      ? number(range.minimum)
      : '${number(range.minimum)}–${number(range.maximum)}';
}

String _aiShort(String team) {
  final value = team.trim();
  return value.length <= 8
      ? value.toUpperCase()
      : '${value.substring(0, 7).toUpperCase()}…';
}

String _aiTerm(String value) => switch (value.toLowerCase()) {
  'preparation' => 'Preparación',
  'relative_competition_strength' => 'Nivel relativo de competición',
  'confirmed_lineups' => 'Alineaciones confirmadas',
  'expected_rotations' => 'Rotaciones esperadas',
  'home_travel_conditions' => 'Viaje y localía',
  'confirmed_absences' => 'Ausencias confirmadas',
  'market_disagreement' => 'Diferencia con el mercado',
  'data_uncertainty' => 'Incertidumbre de los datos',
  'goals' => 'Goles',
  'corners' => 'Córners',
  'shots' => 'Remates',
  'shots_on_target' => 'Remates al arco',
  _ => value,
};

String _aiSide(String? value) => switch (value?.toLowerCase()) {
  'home' => 'local',
  'away' => 'visitante',
  'neither' => 'sin lado beneficiado',
  _ => 'ajuste',
};

String _aiNoteLabel(String value) => switch (value.toLowerCase()) {
  'adjustment' => 'Ajuste',
  'market' => 'Mercado',
  'risk' => 'Riesgo',
  'missing_data' => 'Dato faltante',
  'model_error' => 'Límite del modelo',
  _ => 'Nota',
};

String _aiPoints(double value) {
  final shown = (value - value.round()).abs() < .05
      ? value.round().toString()
      : value.toStringAsFixed(1);
  return '${value > .05 ? '+' : ''}$shown pp';
}

String _aiMarketLabel(String value) => switch (value.toLowerCase()) {
  'home_win' => 'Gana el local',
  'draw' => 'Empate',
  'away_win' => 'Gana el visitante',
  'double_chance_home_draw' => 'Doble oportunidad: local o empate',
  'double_chance_draw_away' => 'Doble oportunidad: empate o visitante',
  'draw_no_bet_home' => 'Local, empate no válido',
  'draw_no_bet_away' => 'Visitante, empate no válido',
  'over_0_5' => 'Más de 0.5 goles',
  'under_0_5' => 'Menos de 0.5 goles',
  'over_1_5' => 'Más de 1.5 goles',
  'under_1_5' => 'Menos de 1.5 goles',
  'over_2_5' => 'Más de 2.5 goles',
  'under_2_5' => 'Menos de 2.5 goles',
  'over_3_5' => 'Más de 3.5 goles',
  'under_3_5' => 'Menos de 3.5 goles',
  'over_4_5' => 'Más de 4.5 goles',
  'under_4_5' => 'Menos de 4.5 goles',
  'btts_yes' => 'Ambos equipos marcan',
  'btts_no' => 'No marcan ambos equipos',
  'no_bet' => 'No hay una selección recomendable',
  _ => value,
};
