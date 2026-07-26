import 'package:flutter_test/flutter_test.dart';
import 'package:prediccion_futbol/models/ai_calibration.dart';

Map<String, dynamic> _range(double min, double max) => {
  'status': 'available',
  'min': min,
  'max': max,
  'evidence_keys': ['base_prediction'],
};

Map<String, dynamic> _unavailableRange() => {
  'status': 'no_disponible',
  'min': null,
  'max': null,
  'evidence_keys': <String>[],
};

Map<String, dynamic> aiCalibrationJson() => {
  'fixture_id': 1492292,
  'status': 'updated',
  'retry_after_seconds': null,
  'reason_code': null,
  'safe_message': null,
  'is_stale': false,
  'generated_at': '2026-07-26T03:00:00Z',
  'analysis': {
    'match_type': 'official',
    'base_probabilities': {'home': .39, 'draw': .27, 'away': .34},
    'adjusted_probabilities': {'home': .36, 'draw': .29, 'away': .35},
    'adjustments': [
      {
        'factor': 'preparation',
        'benefited_side': 'away',
        'impact_percentage_points': 2.0,
        'confidence': 'medium',
        'evidence_keys': ['team_history_summary'],
        'explanation': 'El visitante llega con una preparación más estable.',
      },
    ],
    'preparation_comparison': {
      'advantage': 'away',
      'explanation': 'El visitante tiene mejor continuidad.',
      'evidence_keys': ['team_history_summary'],
    },
    'rotation_effect': {
      'home': {
        'estimated_performance_change_pct': -4.0,
        'confidence': 'low',
        'explanation': 'Rotación posible.',
        'evidence_keys': ['lineup_snapshot'],
      },
      'away': {
        'estimated_performance_change_pct': null,
        'confidence': 'low',
        'explanation': 'Sin alineación confirmada.',
        'evidence_keys': ['fixture_metadata'],
      },
    },
    'projections': {
      'goals': {
        'home': _range(0, 2),
        'away': _range(1, 2),
        'total': _range(1, 4),
      },
      'corners': {
        'home': _range(4, 7),
        'away': _range(3, 6),
        'total': _range(8, 12),
      },
      'shots': {
        'home': _unavailableRange(),
        'away': _unavailableRange(),
        'total': _unavailableRange(),
      },
      'shots_on_target': {
        'home': _range(3, 6),
        'away': _range(2, 5),
        'total': _range(6, 10),
      },
    },
    'recommended_market': {
      'market': 'over_1_5',
      'minimum_value_odds': null,
      'confidence': 'medium',
      'estimated_edge_percentage_points': null,
      'justification': 'Es el mercado con mayor respaldo.',
      'evidence_keys': ['base_prediction'],
      'market_data_available': false,
    },
    'conservative_alternative': {
      'market': 'no_bet',
      'minimum_value_odds': null,
      'confidence': 'no_bet',
      'estimated_edge_percentage_points': null,
      'justification': 'No hay evidencia suficiente para otra selección.',
      'evidence_keys': <String>[],
      'market_data_available': false,
    },
    'risks': ['Alineaciones pendientes'],
    'missing_data': ['Cuotas recientes'],
    'possible_model_errors': ['Muestra reducida'],
    'probable_forecast': [
      {
        'category': 'corners',
        'title': 'Córners',
        'prediction': 'Más de 7.5',
        'probability': .71,
        'confidence': 'medium',
      },
    ],
    'forecast_finalized': false,
    'refresh_with_lineups': true,
    'data_quality': 'medium',
    'lineups_considered': false,
    'model_label': 'Calibración contextual IA',
  },
};

void main() {
  test(
    'parsea el contrato público canónico de una calibración actualizada',
    () {
      final result = AiCalibrationResult.fromJson(aiCalibrationJson());
      final analysis = result.analysis!;

      expect(result.status, AiCalibrationStatus.updated);
      expect(result.fixtureId, 1492292);
      expect(analysis.adjustedProbabilities.home, .36);
      expect(analysis.adjustments.single.benefitedSide, 'away');
      expect(analysis.adjustments.single.impactPercentagePoints, 2);
      expect(analysis.probableForecast.single.category, 'corners');
      expect(analysis.probableForecast.single.prediction, 'Más de 7.5');
      expect(analysis.forecastFinalized, isFalse);
      expect(
        analysis.adjustments.single.evidence,
        'historial reciente de los equipos',
      );
      expect(
        analysis.preparationComparison
            .firstWhere((item) => item.label == 'Ventaja')
            .value,
        'visitante',
      );
      expect(
        analysis.rotationEffect
            .firstWhere((item) => item.label == 'Local')
            .value,
        contains('Confianza: baja'),
      );
      expect(analysis.projections, hasLength(3));
      expect(
        analysis.projections
            .firstWhere((item) => item.metric == 'Goles')
            .total!
            .maximum,
        4,
      );
      expect(analysis.recommendedMarket!.market, 'over_1_5');
      expect(analysis.conservativeAlternative!.recommendsNoBet, isTrue);
      expect(analysis.refreshWithLineups, isTrue);
    },
  );

  test('acepta pending sin análisis y conserva retry_after_seconds', () {
    final result = AiCalibrationResult.fromJson({
      'fixture_id': 1492292,
      'status': 'pending',
      'retry_after_seconds': 900,
      'safe_message': 'Análisis en cola.',
    });

    expect(result.status, AiCalibrationStatus.pending);
    expect(result.analysis, isNull);
    expect(result.retryAfterSeconds, 900);
    expect(result.shouldPoll, isTrue);
  });

  test(
    'mantiene el sondeo mientras una calibración publicada está obsoleta',
    () {
      final json = aiCalibrationJson()..['is_stale'] = true;
      final result = AiCalibrationResult.fromJson(json);

      expect(result.status, AiCalibrationStatus.updated);
      expect(result.isStale, isTrue);
      expect(result.shouldPoll, isTrue);
    },
  );

  test('respeta show_1x2=false para perfiles históricos incompletos', () {
    final json = aiCalibrationJson();
    final analysis = Map<String, dynamic>.from(json['analysis'] as Map)
      ..['show_1x2'] = false;
    json['analysis'] = analysis;

    final result = AiCalibrationResult.fromJson(json);

    expect(result.analysis!.showOneXTwo, isFalse);
  });

  test('prioriza y limita las notas del contrato público compacto', () {
    final json = aiCalibrationJson();
    final analysis = Map<String, dynamic>.from(json['analysis'] as Map)
      ..['notes'] = [
        {'kind': 'adjustment', 'text': 'Ajuste uno.'},
        {'kind': 'market', 'text': 'Mercado dos.'},
        {'kind': 'risk', 'text': 'Riesgo tres.'},
        {'kind': 'missing_data', 'text': 'Dato cuatro.'},
        {'kind': 'model_error', 'text': 'Límite cinco.'},
        {'kind': 'risk', 'text': 'Esta nota se descarta.'},
      ];
    json['analysis'] = analysis;

    final result = AiCalibrationResult.fromJson(json);

    expect(result.analysis!.usesCompactNotes, isTrue);
    expect(result.analysis!.notes, hasLength(5));
    expect(result.analysis!.notes!.first.kind, 'adjustment');
    expect(result.analysis!.notes!.last.text, 'Límite cinco.');
  });

  test('conserva el contrato narrativo legado si notes no está presente', () {
    final result = AiCalibrationResult.fromJson(aiCalibrationJson());

    expect(result.analysis!.usesCompactNotes, isFalse);
    expect(result.analysis!.notes, isNull);
    expect(result.analysis!.preparationComparison, isNotEmpty);
    expect(result.analysis!.projections, isNotEmpty);
  });

  test(
    'detiene el sondeo si falló la actualización de una versión anterior',
    () {
      final json = aiCalibrationJson()
        ..['is_stale'] = true
        ..['reason_code'] = 'calibration_refresh_failed';

      final result = AiCalibrationResult.fromJson(json);

      expect(result.status, AiCalibrationStatus.updated);
      expect(result.isStale, isTrue);
      expect(result.shouldPoll, isFalse);
    },
  );

  test('rechaza probabilidades ajustadas que no suman cien por ciento', () {
    final json = aiCalibrationJson();
    final analysis = Map<String, dynamic>.from(json['analysis'] as Map);
    analysis['adjusted_probabilities'] = {'home': .6, 'draw': .3, 'away': .3};
    json['analysis'] = analysis;

    expect(
      () => AiCalibrationResult.fromJson(json),
      throwsA(isA<FormatException>()),
    );
  });
}
