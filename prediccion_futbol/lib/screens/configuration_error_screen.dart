import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

class ConfigurationErrorScreen extends StatelessWidget {
  const ConfigurationErrorScreen({super.key, required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 480),
            child: Padding(
              padding: const EdgeInsets.all(28),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Container(
                    width: 68,
                    height: 68,
                    decoration: const BoxDecoration(
                      color: AppColors.surface,
                      shape: BoxShape.circle,
                    ),
                    child: const Icon(
                      Icons.settings_outlined,
                      size: 32,
                      color: AppColors.amber,
                    ),
                  ),
                  const SizedBox(height: 22),
                  Text(
                    'Configuración pendiente',
                    textAlign: TextAlign.center,
                    style: Theme.of(context).textTheme.headlineSmall,
                  ),
                  const SizedBox(height: 10),
                  Text(message, textAlign: TextAlign.center),
                  const SizedBox(height: 18),
                  const Text(
                    'Ejecuta Flutter con un archivo de configuración de config/ o usa config/demo.json para revisar la interfaz.',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: AppColors.muted, height: 1.45),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
