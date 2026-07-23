import 'package:flutter/material.dart';

abstract final class AppColors {
  static const background = Color(0xFF06172D);
  static const surface = Color(0xFF0B2340);
  static const surfaceStrong = Color(0xFF102C4D);
  static const border = Color(0xFF284563);
  static const text = Color(0xFFF7F9FC);
  static const muted = Color(0xFF9CADBF);
  static const primary = Color(0xFF28C45D);
  static const blue = Color(0xFF4D84E8);
  static const violet = Color(0xFF6F4FD2);
  static const amber = Color(0xFFF2B51D);
  static const danger = Color(0xFFFF6B6B);
}

ThemeData buildAppTheme() {
  final scheme =
      ColorScheme.fromSeed(
        seedColor: AppColors.primary,
        brightness: Brightness.dark,
        surface: AppColors.surface,
      ).copyWith(
        primary: AppColors.primary,
        secondary: AppColors.blue,
        error: AppColors.danger,
        onSurface: AppColors.text,
        outline: AppColors.border,
      );

  return ThemeData(
    useMaterial3: true,
    brightness: Brightness.dark,
    colorScheme: scheme,
    scaffoldBackgroundColor: AppColors.background,
    dividerColor: AppColors.border,
    textTheme: const TextTheme(
      headlineLarge: TextStyle(
        color: AppColors.text,
        fontSize: 34,
        height: 1.08,
        fontWeight: FontWeight.w800,
        letterSpacing: -0.8,
      ),
      headlineSmall: TextStyle(
        color: AppColors.text,
        fontSize: 24,
        height: 1.15,
        fontWeight: FontWeight.w800,
        letterSpacing: -0.3,
      ),
      titleLarge: TextStyle(
        color: AppColors.text,
        fontSize: 20,
        height: 1.2,
        fontWeight: FontWeight.w700,
      ),
      titleMedium: TextStyle(
        color: AppColors.text,
        fontSize: 16,
        height: 1.25,
        fontWeight: FontWeight.w700,
      ),
      bodyLarge: TextStyle(color: AppColors.text, fontSize: 16, height: 1.45),
      bodyMedium: TextStyle(color: AppColors.muted, fontSize: 14, height: 1.4),
      labelLarge: TextStyle(
        color: AppColors.text,
        fontSize: 15,
        height: 1.2,
        fontWeight: FontWeight.w700,
      ),
    ),
    appBarTheme: const AppBarTheme(
      backgroundColor: AppColors.background,
      foregroundColor: AppColors.text,
      elevation: 0,
      centerTitle: true,
    ),
    cardTheme: CardThemeData(
      color: AppColors.surface,
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(16),
        side: const BorderSide(color: AppColors.border),
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: AppColors.primary,
        foregroundColor: const Color(0xFF03150A),
        minimumSize: const Size(48, 48),
        padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14),
        textStyle: const TextStyle(fontSize: 15, fontWeight: FontWeight.w800),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      ),
    ),
    iconButtonTheme: IconButtonThemeData(
      style: IconButton.styleFrom(foregroundColor: AppColors.text),
    ),
    progressIndicatorTheme: const ProgressIndicatorThemeData(
      color: AppColors.primary,
    ),
  );
}
