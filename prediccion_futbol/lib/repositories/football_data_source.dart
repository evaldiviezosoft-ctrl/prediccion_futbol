import '../models/backend_health.dart';
import '../models/fixture_summary.dart';
import '../models/ai_calibration.dart';
import '../models/prediction.dart';

abstract interface class FootballDataSource {
  Future<BackendHealth> checkHealth();

  Future<List<FixtureSummary>> upcomingFixtures({int days = 14});

  Stream<Prediction?> watchPrediction(int fixtureId);

  Stream<AiCalibrationResult> watchAiCalibration(int fixtureId);

  void dispose();
}
