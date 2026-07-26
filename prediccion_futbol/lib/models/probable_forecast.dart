class ProbableForecastPick {
  const ProbableForecastPick({
    required this.category,
    required this.title,
    required this.prediction,
    required this.confidence,
    this.probability,
  });

  final String category;
  final String title;
  final String prediction;
  final double? probability;
  final String confidence;

  factory ProbableForecastPick.fromJson(Map<String, dynamic> json) {
    final category = json['category']?.toString().trim();
    final title = json['title']?.toString().trim();
    final prediction = json['prediction']?.toString().trim();
    if (category == null ||
        category.isEmpty ||
        title == null ||
        title.isEmpty ||
        prediction == null ||
        prediction.isEmpty) {
      throw const FormatException('Invalid probable forecast pick.');
    }
    final probability = (json['probability'] as num?)?.toDouble();
    return ProbableForecastPick(
      category: category,
      title: title,
      prediction: prediction,
      probability:
          probability != null &&
              probability.isFinite &&
              probability >= 0 &&
              probability <= 1
          ? probability
          : null,
      confidence: json['confidence']?.toString().trim() ?? 'low',
    );
  }
}

List<ProbableForecastPick> parseProbableForecast(Object? value) {
  if (value is! List) return const [];
  return value
      .whereType<Map>()
      .map(
        (item) =>
            ProbableForecastPick.fromJson(Map<String, dynamic>.from(item)),
      )
      .take(7)
      .toList(growable: false);
}
