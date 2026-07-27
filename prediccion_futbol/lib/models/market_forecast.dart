enum MarketLineSelection { over, under, none }

class MarketForecastLine {
  const MarketForecastLine({
    required this.line,
    required this.overProbability,
    required this.underProbability,
    required this.selection,
    this.selectionProbability,
  });

  final double line;
  final double overProbability;
  final double underProbability;
  final MarketLineSelection selection;
  final double? selectionProbability;

  bool get hasRecommendation =>
      selection != MarketLineSelection.none && selectionProbability != null;

  factory MarketForecastLine.fromJson(Map<String, dynamic> json) {
    final line = _requiredFiniteNumber(json, 'line');
    final overProbability = _probability(json, 'over_probability');
    final underProbability = _probability(json, 'under_probability');
    final rawSelection = json['selection']?.toString().trim().toLowerCase();
    final selection = switch (rawSelection) {
      'over' => MarketLineSelection.over,
      'under' => MarketLineSelection.under,
      'none' => MarketLineSelection.none,
      _ => throw const FormatException('Invalid market line selection.'),
    };
    final rawSelectionProbability = json['selection_probability'];
    final selectionProbability = rawSelectionProbability == null
        ? null
        : _validatedProbability(
            rawSelectionProbability,
            'selection_probability',
          );
    if (selection == MarketLineSelection.none && selectionProbability != null) {
      throw const FormatException(
        'A market line without a selection cannot have a probability.',
      );
    }
    if (selection != MarketLineSelection.none && selectionProbability == null) {
      throw const FormatException(
        'A selected market line must include its probability.',
      );
    }

    return MarketForecastLine(
      line: line,
      overProbability: overProbability,
      underProbability: underProbability,
      selection: selection,
      selectionProbability: selectionProbability,
    );
  }
}

class MarketForecastMarket {
  const MarketForecastMarket({
    required this.category,
    required this.title,
    required this.scope,
    required this.expectedTotal,
    required this.confidence,
    required this.lines,
  });

  final String category;
  final String title;
  final String scope;
  final double expectedTotal;
  final String confidence;
  final List<MarketForecastLine> lines;

  factory MarketForecastMarket.fromJson(Map<String, dynamic> json) {
    final category = _requiredText(json, 'category');
    final title = _requiredText(json, 'title');
    final scope = _requiredText(json, 'scope');
    final confidence = _requiredText(json, 'confidence');
    if (scope != 'match_total') {
      throw const FormatException('Unsupported market forecast scope.');
    }
    if (!const {'low', 'medium', 'high'}.contains(confidence)) {
      throw const FormatException('Invalid market forecast confidence.');
    }
    final rawLines = json['lines'];
    if (rawLines is! List) {
      throw const FormatException('Market forecast lines must be a list.');
    }

    return MarketForecastMarket(
      category: category,
      title: title,
      scope: scope,
      expectedTotal: _requiredFiniteNumber(json, 'expected_total'),
      confidence: confidence,
      lines: rawLines
          .whereType<Map>()
          .map(
            (line) =>
                MarketForecastLine.fromJson(Map<String, dynamic>.from(line)),
          )
          .toList(growable: false),
    );
  }
}

class MarketForecast {
  const MarketForecast({
    required this.version,
    required this.method,
    required this.markets,
  });

  final String version;
  final String method;
  final List<MarketForecastMarket> markets;

  MarketForecastMarket? marketFor(String category) {
    for (final market in markets) {
      if (market.category == category) return market;
    }
    return null;
  }

  factory MarketForecast.fromJson(Map<String, dynamic> json) {
    final rawMarkets = json['markets'];
    if (rawMarkets is! List) {
      throw const FormatException('Market forecasts must be a list.');
    }
    return MarketForecast(
      version: _requiredText(json, 'version'),
      method: _requiredText(json, 'method'),
      markets: rawMarkets
          .whereType<Map>()
          .map(
            (market) => MarketForecastMarket.fromJson(
              Map<String, dynamic>.from(market),
            ),
          )
          .toList(growable: false),
    );
  }
}

String _requiredText(Map<String, dynamic> json, String key) {
  final value = json[key]?.toString().trim();
  if (value == null || value.isEmpty) {
    throw FormatException('Missing market forecast field: $key.');
  }
  return value;
}

double _requiredFiniteNumber(Map<String, dynamic> json, String key) {
  final value = (json[key] as num?)?.toDouble();
  if (value == null || !value.isFinite || value < 0) {
    throw FormatException('Invalid market forecast number: $key.');
  }
  return value;
}

double _probability(Map<String, dynamic> json, String key) =>
    _validatedProbability(json[key], key);

double _validatedProbability(Object? rawValue, String key) {
  final value = (rawValue as num?)?.toDouble();
  if (value == null || !value.isFinite || value < 0 || value > 1) {
    throw FormatException('Invalid market forecast probability: $key.');
  }
  return value;
}
