import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:prediccion_futbol/widgets/team_mark.dart';

void main() {
  testWidgets('TeamMark usa el escudo cuando existe una URL', (tester) async {
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(
          body: TeamMark(
            team: 'Flamengo',
            logoUrl: 'https://example.test/flamengo.png',
          ),
        ),
      ),
    );

    expect(find.byType(Image), findsOneWidget);
  });

  testWidgets('TeamMark conserva iniciales cuando no hay escudo', (
    tester,
  ) async {
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(body: TeamMark(team: 'Sporting Cristal')),
      ),
    );

    expect(find.text('SC'), findsOneWidget);
    expect(find.byType(Image), findsNothing);
  });
}
