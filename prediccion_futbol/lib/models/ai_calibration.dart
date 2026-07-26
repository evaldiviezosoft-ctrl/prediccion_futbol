enum AiCalibrationStatus { pending, unavailable, error, updated }

class AiProbabilityTriplet {
  const AiProbabilityTriplet({
    required this.home,
    required this.draw,
    required this.away,
  });

  final double home;
  final double draw;
  final double away;

  factory AiProbabilityTriplet.fromJson(Object? value) {
    if (value is! Map) {
      throw const FormatException('Probability object is missing.');
    }

    var home = _number(value['home'] ?? value['local']);
    var draw = _number(value['draw'] ?? value['empate']);
    var away = _number(value['away'] ?? value['visitante']);
    if (home == null || draw == null || away == null) {
      throw const FormatException('Probability values are incomplete.');
    }

    final sum = home + draw + away;
    if (sum > 99 && sum < 101) {
      home /= 100;
      draw /= 100;
      away /= 100;
    }
    if (![home, draw, away].every((item) => item >= 0 && item <= 1) ||
        ((home + draw + away) - 1).abs() > .015) {
      throw const FormatException('Probability values are invalid.');
    }

    return AiProbabilityTriplet(home: home, draw: draw, away: away);
  }
}

class AiAdjustment {
  const AiAdjustment({
    required this.factor,
    required this.detail,
    this.evidence,
    this.probabilityImpact,
    this.benefitedSide,
    this.impactPercentagePoints,
    this.confidence,
  });

  final String factor;
  final String detail;
  final String? evidence;
  final AiProbabilityTriplet? probabilityImpact;
  final String? benefitedSide;
  final double? impactPercentagePoints;
  final String? confidence;

  factory AiAdjustment.fromJson(Object? value) {
    if (value is String) {
      return AiAdjustment(factor: 'Ajuste contextual', detail: value.trim());
    }
    if (value is! Map) {
      throw const FormatException('Invalid adjustment.');
    }

    final factor =
        _text(
          value['factor'] ?? value['name'] ?? value['title'] ?? value['nombre'],
        ) ??
        'Ajuste contextual';
    final detail =
        _text(
          value['detail'] ??
              value['explanation'] ??
              value['reason'] ??
              value['justification'] ??
              value['impact'] ??
              value['detalle'],
        ) ??
        'Sin detalle adicional.';
    final evidence = _readableValue(
      value['evidence'] ??
          value['evidence_keys'] ??
          value['source'] ??
          value['evidencia'],
    );
    final rawImpact =
        value['probability_impact'] ??
        value['probability_delta'] ??
        value['impacto_probabilidades'];
    AiProbabilityTriplet? probabilityImpact;
    if (rawImpact is Map) {
      probabilityImpact = _parseProbabilityImpact(rawImpact);
    }

    return AiAdjustment(
      factor: factor,
      detail: detail,
      evidence: evidence,
      probabilityImpact: probabilityImpact,
      benefitedSide: _text(
        value['benefited_side'] ?? value['lado_beneficiado'],
      ),
      impactPercentagePoints: _number(
        value['impact_percentage_points'] ??
            value['impacto_puntos_porcentuales'],
      ),
      confidence: _text(value['confidence'] ?? value['confianza']),
    );
  }
}

class AiContextDetail {
  const AiContextDetail({required this.label, required this.value});

  final String label;
  final String value;
}

class AiProjectionRange {
  const AiProjectionRange({required this.minimum, required this.maximum});

  final double minimum;
  final double maximum;

  factory AiProjectionRange.fromJson(Object? value) {
    double? minimum;
    double? maximum;
    if (value is num) {
      minimum = value.toDouble();
      maximum = value.toDouble();
    } else if (value is List && value.length >= 2) {
      minimum = _number(value[0]);
      maximum = _number(value[1]);
    } else if (value is Map) {
      minimum = _number(
        value['min'] ?? value['minimum'] ?? value['desde'] ?? value['low'],
      );
      maximum = _number(
        value['max'] ?? value['maximum'] ?? value['hasta'] ?? value['high'],
      );
      final exact = _number(value['value'] ?? value['expected']);
      minimum ??= exact;
      maximum ??= exact;
    } else if (value is String) {
      final matches = RegExp(r'-?\d+(?:[.,]\d+)?')
          .allMatches(value)
          .map((match) {
            return double.tryParse(match.group(0)!.replaceAll(',', '.'));
          })
          .whereType<double>()
          .toList();
      if (matches.isNotEmpty) {
        minimum = matches.first;
        maximum = matches.length > 1 ? matches[1] : matches.first;
      }
    }

    if (minimum == null ||
        maximum == null ||
        !minimum.isFinite ||
        !maximum.isFinite ||
        minimum < 0 ||
        maximum < minimum) {
      throw const FormatException('Invalid projection range.');
    }
    return AiProjectionRange(minimum: minimum, maximum: maximum);
  }
}

class AiProjection {
  const AiProjection({required this.metric, this.home, this.away, this.total});

  final String metric;
  final AiProjectionRange? home;
  final AiProjectionRange? away;
  final AiProjectionRange? total;
}

class AiMarketRecommendation {
  const AiMarketRecommendation({
    required this.market,
    required this.confidence,
    required this.justification,
    required this.marketDataAvailable,
    this.minimumValueOdds,
    this.estimatedEdgePercentagePoints,
  });

  final String market;
  final String confidence;
  final String justification;
  final bool marketDataAvailable;
  final double? minimumValueOdds;
  final double? estimatedEdgePercentagePoints;

  bool get recommendsNoBet =>
      confidence.toLowerCase() == 'no_bet' ||
      market.toLowerCase() == 'no_bet' ||
      market.toLowerCase().contains('no hay');

  factory AiMarketRecommendation.fromJson(Object? value) {
    if (value is String) {
      return AiMarketRecommendation(
        market: value.trim(),
        confidence: 'unknown',
        justification: 'Selección basada en la información disponible.',
        marketDataAvailable: false,
      );
    }
    if (value is! Map) {
      throw const FormatException('Invalid market recommendation.');
    }

    final market =
        _text(
          value['market'] ??
              value['selection'] ??
              value['mercado'] ??
              value['seleccion'],
        ) ??
        'No hay una selección recomendable';
    final confidence =
        _text(value['confidence'] ?? value['confianza']) ?? 'unknown';
    final justification =
        _text(
          value['justification'] ??
              value['reason'] ??
              value['justificacion'] ??
              value['motivo'],
        ) ??
        'Sin justificación adicional.';
    final minimumOdds = _number(
      value['minimum_value_odds'] ??
          value['minimum_odds'] ??
          value['cuota_minima_valor'],
    );

    return AiMarketRecommendation(
      market: market,
      confidence: confidence,
      justification: justification,
      marketDataAvailable:
          _boolean(
            value['market_data_available'] ??
                value['odds_available'] ??
                value['cuotas_disponibles'],
          ) ??
          false,
      minimumValueOdds: minimumOdds != null && minimumOdds > 1
          ? minimumOdds
          : null,
      estimatedEdgePercentagePoints: _number(
        value['estimated_edge_percentage_points'] ??
            value['ventaja_estimada_puntos_porcentuales'],
      ),
    );
  }
}

class AiCalibrationAnalysis {
  const AiCalibrationAnalysis({
    required this.matchType,
    required this.baseProbabilities,
    required this.adjustedProbabilities,
    required this.adjustments,
    required this.preparationComparison,
    required this.rotationEffect,
    required this.projections,
    required this.risks,
    required this.missingData,
    required this.possibleModelErrors,
    required this.refreshWithLineups,
    required this.dataQuality,
    required this.lineupsConsidered,
    required this.modelLabel,
    this.showOneXTwo = true,
    this.recommendedMarket,
    this.conservativeAlternative,
  });

  final String matchType;
  final AiProbabilityTriplet baseProbabilities;
  final AiProbabilityTriplet adjustedProbabilities;
  final List<AiAdjustment> adjustments;
  final List<AiContextDetail> preparationComparison;
  final List<AiContextDetail> rotationEffect;
  final List<AiProjection> projections;
  final AiMarketRecommendation? recommendedMarket;
  final AiMarketRecommendation? conservativeAlternative;
  final List<String> risks;
  final List<String> missingData;
  final List<String> possibleModelErrors;
  final bool refreshWithLineups;
  final String dataQuality;
  final bool lineupsConsidered;
  final String modelLabel;
  final bool showOneXTwo;

  factory AiCalibrationAnalysis.fromJson(Map<String, dynamic> json) {
    final base = AiProbabilityTriplet.fromJson(
      json['base_probabilities'] ?? json['probabilidades_modelo'],
    );
    final adjusted = AiProbabilityTriplet.fromJson(
      json['adjusted_probabilities'] ?? json['probabilidades_ajustadas'],
    );
    final rawAdjustments = json['adjustments'] ?? json['ajustes'];
    final adjustments = rawAdjustments is List
        ? rawAdjustments
              .map(AiAdjustment.fromJson)
              .where((item) => item.detail.isNotEmpty)
              .toList(growable: false)
        : const <AiAdjustment>[];

    return AiCalibrationAnalysis(
      matchType:
          _text(json['match_type'] ?? json['tipo_partido']) ?? 'official',
      baseProbabilities: base,
      adjustedProbabilities: adjusted,
      adjustments: adjustments,
      preparationComparison: _parseContextDetails(
        json['preparation_comparison'] ?? json['comparacion_preparacion'],
      ),
      rotationEffect: _parseContextDetails(
        json['rotation_effect'] ?? json['efecto_rotaciones'],
      ),
      projections: _parseProjections(
        json['projections'] ?? json['proyecciones'],
      ),
      recommendedMarket: _tryMarket(
        json['recommended_market'] ?? json['mejor_apuesta'],
      ),
      conservativeAlternative: _tryMarket(
        json['conservative_alternative'] ?? json['alternativa_conservadora'],
      ),
      risks: _stringList(json['risks'] ?? json['riesgos']),
      missingData: _stringList(json['missing_data'] ?? json['datos_faltantes']),
      possibleModelErrors: _stringList(
        json['possible_model_errors'] ?? json['posibles_errores_modelo'],
      ),
      refreshWithLineups:
          _boolean(
            json['refresh_with_lineups'] ??
                json['recomendacion_actualizar_con_alineaciones'],
          ) ??
          false,
      dataQuality:
          _text(json['data_quality'] ?? json['calidad_datos']) ?? 'unknown',
      lineupsConsidered:
          _boolean(
            json['lineups_considered'] ?? json['alineaciones_consideradas'],
          ) ??
          false,
      modelLabel:
          _text(json['model_label'] ?? json['etiqueta_modelo']) ??
          'Calibración contextual IA',
      showOneXTwo: _boolean(json['show_1x2']) ?? true,
    );
  }
}

class AiCalibrationResult {
  const AiCalibrationResult({
    required this.fixtureId,
    required this.status,
    required this.isStale,
    this.generatedAt,
    this.retryAfterSeconds,
    this.reasonCode,
    this.safeMessage,
    this.analysis,
  });

  final int fixtureId;
  final AiCalibrationStatus status;
  final DateTime? generatedAt;
  final int? retryAfterSeconds;
  final String? reasonCode;
  final String? safeMessage;
  final bool isStale;
  final AiCalibrationAnalysis? analysis;

  bool get shouldPoll =>
      status == AiCalibrationStatus.pending ||
      (status == AiCalibrationStatus.updated &&
          isStale &&
          reasonCode != 'calibration_refresh_failed');

  factory AiCalibrationResult.fromJson(Map<String, dynamic> json) {
    final fixtureId = (json['fixture_id'] as num?)?.toInt();
    if (fixtureId == null || fixtureId <= 0) {
      throw const FormatException('Fixture id is missing.');
    }
    final status = switch (_text(json['status'])?.toLowerCase()) {
      'pending' || 'processing' => AiCalibrationStatus.pending,
      'unavailable' => AiCalibrationStatus.unavailable,
      'error' => AiCalibrationStatus.error,
      'updated' || 'ready' => AiCalibrationStatus.updated,
      _ => throw const FormatException('Unknown AI analysis status.'),
    };
    final rawAnalysis = json['analysis'];
    final analysis = rawAnalysis is Map
        ? AiCalibrationAnalysis.fromJson(Map<String, dynamic>.from(rawAnalysis))
        : null;
    if (status == AiCalibrationStatus.updated && analysis == null) {
      throw const FormatException('Updated AI analysis is missing.');
    }
    final retryAfter = (json['retry_after_seconds'] as num?)?.toInt();

    return AiCalibrationResult(
      fixtureId: fixtureId,
      status: status,
      generatedAt: _dateTime(json['generated_at']),
      retryAfterSeconds: retryAfter != null && retryAfter > 0
          ? retryAfter.clamp(1, 86400).toInt()
          : null,
      reasonCode: _text(json['reason_code']),
      safeMessage: _text(json['safe_message']),
      isStale: _boolean(json['is_stale']) ?? false,
      analysis: analysis,
    );
  }
}

AiMarketRecommendation? _tryMarket(Object? value) {
  if (value == null) return null;
  try {
    return AiMarketRecommendation.fromJson(value);
  } on FormatException {
    return null;
  }
}

AiProbabilityTriplet? _parseProbabilityImpact(Map<dynamic, dynamic> value) {
  double? read(String english, String spanish) =>
      _number(value[english] ?? value[spanish]);
  final home = read('home', 'local');
  final draw = read('draw', 'empate');
  final away = read('away', 'visitante');
  if (home == null || draw == null || away == null) return null;
  if (![home, draw, away].every((item) => item.isFinite && item.abs() <= 100)) {
    return null;
  }
  final divisor = [home, draw, away].any((item) => item.abs() > 1) ? 100 : 1;
  return AiProbabilityTriplet(
    home: home / divisor,
    draw: draw / divisor,
    away: away / divisor,
  );
}

List<AiContextDetail> _parseContextDetails(Object? value) {
  if (value == null) return const [];
  if (value is String) {
    final text = value.trim();
    return text.isEmpty
        ? const []
        : [AiContextDetail(label: 'Resumen', value: text)];
  }
  if (value is List) {
    return value.expand(_parseContextDetails).take(8).toList(growable: false);
  }
  if (value is Map) {
    return value.entries
        .map((entry) {
          final readable = _readableValue(entry.value);
          return readable == null
              ? null
              : AiContextDetail(
                  label: _humanize(entry.key.toString()),
                  value: readable,
                );
        })
        .whereType<AiContextDetail>()
        .take(8)
        .toList(growable: false);
  }
  return const [];
}

List<AiProjection> _parseProjections(Object? value) {
  if (value is List) {
    return value
        .map(_projectionFromListItem)
        .whereType<AiProjection>()
        .take(8)
        .toList(growable: false);
  }
  if (value is! Map) return const [];

  final projections = <AiProjection>[];
  final loose = <String, _ProjectionParts>{};
  for (final entry in value.entries) {
    final key = entry.key.toString();
    final nested = entry.value;
    if (nested is Map &&
        (nested.containsKey('home') ||
            nested.containsKey('away') ||
            nested.containsKey('local') ||
            nested.containsKey('visitante') ||
            nested.containsKey('total'))) {
      final projection = _projectionFromMap(key, nested);
      if (projection != null) projections.add(projection);
      continue;
    }

    final split = _projectionMetricAndSide(key);
    if (split == null) {
      final range = _tryRange(nested);
      if (range != null) {
        projections.add(AiProjection(metric: _humanize(key), total: range));
      }
      continue;
    }
    final range = _tryRange(nested);
    if (range == null) continue;
    final parts = loose.putIfAbsent(split.$1, _ProjectionParts.new);
    switch (split.$2) {
      case 'home':
        parts.home = range;
        break;
      case 'away':
        parts.away = range;
        break;
      case 'total':
        parts.total = range;
        break;
    }
  }
  for (final entry in loose.entries) {
    projections.add(
      AiProjection(
        metric: _humanize(entry.key),
        home: entry.value.home,
        away: entry.value.away,
        total: entry.value.total,
      ),
    );
  }
  return projections.take(8).toList(growable: false);
}

AiProjection? _projectionFromListItem(Object? value) {
  if (value is! Map) return null;
  final metric = _text(value['metric'] ?? value['name'] ?? value['metrica']);
  if (metric == null) return null;
  return _projectionFromMap(metric, value);
}

AiProjection? _projectionFromMap(String metric, Map<dynamic, dynamic> value) {
  final home = _tryRange(value['home'] ?? value['local']);
  final away = _tryRange(value['away'] ?? value['visitante']);
  final total = _tryRange(value['total']);
  if (home == null && away == null && total == null) return null;
  return AiProjection(
    metric: _humanize(metric),
    home: home,
    away: away,
    total: total,
  );
}

AiProjectionRange? _tryRange(Object? value) {
  try {
    return AiProjectionRange.fromJson(value);
  } on FormatException {
    return null;
  }
}

(String, String)? _projectionMetricAndSide(String rawKey) {
  final key = rawKey.toLowerCase();
  const prefixes = {
    'home_': 'home',
    'away_': 'away',
    'total_': 'total',
    'local_': 'home',
    'visitante_': 'away',
  };
  for (final entry in prefixes.entries) {
    if (key.startsWith(entry.key)) {
      return (key.substring(entry.key.length), entry.value);
    }
  }
  const suffixes = {
    '_home': 'home',
    '_away': 'away',
    '_total': 'total',
    '_local': 'home',
    '_visitante': 'away',
    '_totales': 'total',
  };
  for (final entry in suffixes.entries) {
    if (key.endsWith(entry.key)) {
      return (key.substring(0, key.length - entry.key.length), entry.value);
    }
  }
  return null;
}

class _ProjectionParts {
  AiProjectionRange? home;
  AiProjectionRange? away;
  AiProjectionRange? total;
}

List<String> _stringList(Object? value) {
  if (value is String) {
    final text = value.trim();
    return text.isEmpty ? const [] : [text];
  }
  if (value is! List) return const [];
  return value
      .map(_readableValue)
      .whereType<String>()
      .where((item) => item.isNotEmpty)
      .take(12)
      .toList(growable: false);
}

String? _readableValue(Object? value) {
  if (value == null) return null;
  if (value is String) {
    final text = _text(value);
    return text == null ? null : _localizedAiValue(text);
  }
  if (value is num || value is bool) return value.toString();
  if (value is List) {
    final values = value.map(_readableValue).whereType<String>().toList();
    return values.isEmpty ? null : values.join(' · ');
  }
  if (value is Map) {
    final values = value.entries
        .map((entry) {
          final nested = _readableValue(entry.value);
          return nested == null
              ? null
              : '${_humanize(entry.key.toString())}: $nested';
        })
        .whereType<String>()
        .toList();
    return values.isEmpty ? null : values.join(' · ');
  }
  return null;
}

String _localizedAiValue(String value) => switch (value.toLowerCase()) {
  'home' => 'local',
  'away' => 'visitante',
  'neither' => 'ninguno',
  'balanced' => 'equilibrado',
  'high' => 'alta',
  'medium' => 'media',
  'low' => 'baja',
  'fixture_metadata' => 'datos del partido',
  'base_prediction' => 'modelo estadístico base',
  'model_metadata' => 'metadatos del modelo',
  'feature_snapshot' => 'variables del modelo',
  'team_history_summary' => 'historial reciente de los equipos',
  'team_statistics_summary' => 'estadísticas recientes de los equipos',
  'lineup_snapshot' => 'alineaciones',
  'injury_snapshot' => 'ausencias registradas',
  'odds_snapshot' => 'cuotas recientes',
  _ => value,
};

String _humanize(String value) {
  final known = switch (value.toLowerCase()) {
    'goals' || 'goles' => 'Goles',
    'corners' || 'corner' => 'Córners',
    'shots' || 'remates' => 'Remates',
    'shots_on_target' || 'remates_al_arco' => 'Remates al arco',
    'home' || 'local' => 'Local',
    'away' || 'visitante' => 'Visitante',
    'total' || 'totales' => 'Total',
    'advantage' || 'ventaja' => 'Ventaja',
    'explanation' || 'explicacion' => 'Explicación',
    'evidence_keys' || 'evidencia' => 'Evidencia',
    'estimated_performance_change_pct' => 'Cambio estimado',
    'confidence' || 'confianza' => 'Confianza',
    _ => null,
  };
  if (known != null) return known;
  final normalized = value.replaceAll('_', ' ').trim();
  if (normalized.isEmpty) return 'Dato';
  return '${normalized[0].toUpperCase()}${normalized.substring(1)}';
}

String? _text(Object? value) {
  final text = value?.toString().trim();
  return text == null || text.isEmpty || text == 'no_disponible' ? null : text;
}

double? _number(Object? value) {
  if (value is num) {
    final number = value.toDouble();
    return number.isFinite ? number : null;
  }
  if (value is String) {
    final number = double.tryParse(value.replaceAll(',', '.'));
    return number != null && number.isFinite ? number : null;
  }
  return null;
}

bool? _boolean(Object? value) {
  if (value is bool) return value;
  if (value is num) return value != 0;
  return switch (value?.toString().toLowerCase()) {
    'true' || 'yes' || 'si' || 'sí' => true,
    'false' || 'no' => false,
    _ => null,
  };
}

DateTime? _dateTime(Object? value) {
  final parsed = DateTime.tryParse(value?.toString() ?? '');
  return parsed?.toLocal();
}
