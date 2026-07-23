import 'package:flutter/material.dart';

import 'core/app_config.dart';
import 'repositories/demo_football_repository.dart';
import 'repositories/football_data_source.dart';
import 'repositories/football_repository.dart';
import 'screens/configuration_error_screen.dart';
import 'screens/home_screen.dart';
import 'theme/app_theme.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  FootballDataSource? repository;
  String? startupError = AppConfig.validationError();

  if (startupError == null) {
    if (AppConfig.demoMode) {
      repository = DemoFootballRepository();
    } else {
      repository = FootballRepository();
    }
  }

  runApp(
    FootballPredictorApp(repository: repository, startupError: startupError),
  );
}

class FootballPredictorApp extends StatelessWidget {
  const FootballPredictorApp({
    super.key,
    required this.repository,
    required this.startupError,
  });

  final FootballDataSource? repository;
  final String? startupError;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'Predicción Fútbol',
      theme: buildAppTheme(),
      home: startupError == null && repository != null
          ? HomeScreen(repository: repository!)
          : ConfigurationErrorScreen(
              message: startupError ?? 'Configuración incompleta.',
            ),
    );
  }
}
