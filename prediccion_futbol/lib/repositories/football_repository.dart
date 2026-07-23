import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import '../core/app_config.dart';
import '../models/backend_health.dart';
import '../models/fixture_summary.dart';
import '../models/prediction.dart';
import 'football_data_source.dart';

enum RepositoryErrorKind {
  offline,
  timeout,
  configuration,
  server,
  invalidData,
}

class FootballRepositoryException implements Exception {
  const FootballRepositoryException(this.message, this.kind);

  final String message;
  final RepositoryErrorKind kind;

  @override
  String toString() => message;
}

class FootballRepository implements FootballDataSource {
  FootballRepository({
    http.Client? client,
    Duration pollInterval = const Duration(seconds: 15),
    Duration retryBaseDelay = const Duration(seconds: 2),
    int maxTransientFailures = 3,
  }) : _client = client ?? http.Client(),
       _pollInterval = pollInterval,
       _retryBaseDelay = retryBaseDelay,
       _maxTransientFailures = maxTransientFailures;

  final http.Client _client;
  final Duration _pollInterval;
  final Duration _retryBaseDelay;
  final int _maxTransientFailures;

  @override
  Future<BackendHealth> checkHealth() async {
    final response = await _get(
      '/health/ready',
      timeout: const Duration(seconds: 6),
    );
    try {
      return BackendHealth.fromJson(
        Map<String, dynamic>.from(jsonDecode(response.body) as Map),
      );
    } catch (_) {
      throw const FootballRepositoryException(
        'El backend respondió con un estado que la app no reconoce.',
        RepositoryErrorKind.invalidData,
      );
    }
  }

  @override
  Future<List<FixtureSummary>> upcomingFixtures({int days = 14}) async {
    final response = await _get('/fixtures/upcoming?days=$days');
    if (response.statusCode != 200) {
      throw FootballRepositoryException(
        _messageFromResponse(
          response,
          fallback: 'No se pudieron cargar los partidos.',
        ),
        response.statusCode == 503
            ? RepositoryErrorKind.configuration
            : RepositoryErrorKind.server,
      );
    }

    try {
      final list = jsonDecode(response.body) as List<dynamic>;
      return list.map((item) {
        final json = Map<String, dynamic>.from(item as Map);
        _preferBackendAsset(
          json,
          proxyKey: 'home_team_logo_proxy_path',
          targetKey: 'home_team_logo_url',
        );
        _preferBackendAsset(
          json,
          proxyKey: 'away_team_logo_proxy_path',
          targetKey: 'away_team_logo_url',
        );
        return FixtureSummary.fromJson(json);
      }).toList();
    } catch (_) {
      throw const FootballRepositoryException(
        'Los partidos llegaron con un formato inesperado.',
        RepositoryErrorKind.invalidData,
      );
    }
  }

  @override
  Stream<Prediction?> watchPrediction(int fixtureId) {
    return _pollPrediction(fixtureId);
  }

  Stream<Prediction?> _pollPrediction(int fixtureId) async* {
    var consecutiveFailures = 0;
    while (true) {
      try {
        final response = await _get(
          '/predictions/$fixtureId',
          timeout: const Duration(seconds: 8),
        );
        if (response.statusCode == 404) {
          yield null;
        } else if (response.statusCode == 200) {
          try {
            yield Prediction.fromJson(
              Map<String, dynamic>.from(jsonDecode(response.body) as Map),
            );
          } catch (_) {
            throw const FootballRepositoryException(
              'La predicción llegó con un formato inesperado.',
              RepositoryErrorKind.invalidData,
            );
          }
        } else {
          throw FootballRepositoryException(
            _messageFromResponse(
              response,
              fallback: 'No se pudo consultar la predicción.',
            ),
            response.statusCode == 503
                ? RepositoryErrorKind.configuration
                : RepositoryErrorKind.server,
          );
        }
        consecutiveFailures = 0;
        await Future<void>.delayed(_pollInterval);
      } on FootballRepositoryException catch (error) {
        consecutiveFailures += 1;
        if (!_isTransient(error.kind) ||
            consecutiveFailures >= _maxTransientFailures) {
          rethrow;
        }
        final multiplier = 1 << (consecutiveFailures - 1);
        await Future<void>.delayed(_retryBaseDelay * multiplier);
      }
    }
  }

  bool _isTransient(RepositoryErrorKind kind) =>
      kind == RepositoryErrorKind.offline ||
      kind == RepositoryErrorKind.timeout ||
      kind == RepositoryErrorKind.server;

  Future<http.Response> _get(
    String path, {
    Duration timeout = const Duration(seconds: 12),
  }) async {
    try {
      return await _client
          .get(Uri.parse('${AppConfig.normalizedBackendUrl}$path'))
          .timeout(timeout);
    } on TimeoutException {
      throw const FootballRepositoryException(
        'El servidor tardó demasiado en responder.',
        RepositoryErrorKind.timeout,
      );
    } on http.ClientException {
      throw const FootballRepositoryException(
        'No se pudo conectar con el servidor de predicciones.',
        RepositoryErrorKind.offline,
      );
    }
  }

  String _messageFromResponse(
    http.Response response, {
    required String fallback,
  }) {
    try {
      final body = jsonDecode(response.body);
      if (body is Map && body['detail'] is String) {
        return body['detail'] as String;
      }
    } catch (_) {
      // The status code is still enough to return a safe message.
    }
    return fallback;
  }

  void _preferBackendAsset(
    Map<String, dynamic> json, {
    required String proxyKey,
    required String targetKey,
  }) {
    final path = json[proxyKey];
    if (path is String && path.startsWith('/')) {
      json[targetKey] = '${AppConfig.normalizedBackendUrl}$path';
    }
  }

  @override
  void dispose() => _client.close();
}
