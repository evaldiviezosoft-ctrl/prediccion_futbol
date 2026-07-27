class AppConfig {
  const AppConfig._();

  static const demoMode = bool.fromEnvironment('DEMO_MODE');

  static const backendUrl = String.fromEnvironment(
    'BACKEND_URL',
    defaultValue: 'https://api-production-1d96.up.railway.app',
  );

  static String get normalizedBackendUrl => backendUrl.endsWith('/')
      ? backendUrl.substring(0, backendUrl.length - 1)
      : backendUrl;

  static String? validationError() {
    if (demoMode) return null;

    final missing = <String>[if (backendUrl.trim().isEmpty) 'BACKEND_URL'];

    if (missing.isNotEmpty) {
      return 'Falta configurar: ${missing.join(', ')}.';
    }

    final parsedBackend = Uri.tryParse(backendUrl);
    if (parsedBackend == null ||
        !parsedBackend.hasScheme ||
        !parsedBackend.hasAuthority) {
      return 'BACKEND_URL no es una URL válida.';
    }
    return null;
  }
}
