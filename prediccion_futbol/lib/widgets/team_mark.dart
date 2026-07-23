import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

class TeamMark extends StatelessWidget {
  const TeamMark({super.key, required this.team, this.logoUrl, this.size = 54});

  final String team;
  final String? logoUrl;
  final double size;

  String get initials {
    final words = team
        .trim()
        .split(RegExp(r'\s+'))
        .where((word) => word.isNotEmpty)
        .toList();
    if (words.isEmpty) {
      return '?';
    }
    if (words.length == 1) {
      return words.first
          .substring(0, words.first.length.clamp(0, 2))
          .toUpperCase();
    }
    return '${words.first[0]}${words.last[0]}'.toUpperCase();
  }

  @override
  Widget build(BuildContext context) {
    final normalizedLogo = logoUrl?.trim();
    return Container(
      width: size,
      height: size,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: AppColors.surfaceStrong,
        shape: BoxShape.circle,
        border: Border.all(color: AppColors.border),
      ),
      child: normalizedLogo == null || normalizedLogo.isEmpty
          ? _initials()
          : Padding(
              padding: EdgeInsets.all(size * 0.10),
              child: Image.network(
                normalizedLogo,
                fit: BoxFit.contain,
                filterQuality: FilterQuality.medium,
                semanticLabel: 'Escudo de $team',
                errorBuilder: (_, _, _) => _initials(),
                loadingBuilder: (context, child, progress) =>
                    progress == null ? child : _initials(),
              ),
            ),
    );
  }

  Widget _initials() {
    return Text(
      initials,
      style: TextStyle(
        color: AppColors.text,
        fontSize: size * 0.31,
        fontWeight: FontWeight.w900,
        letterSpacing: 0.4,
      ),
    );
  }
}
