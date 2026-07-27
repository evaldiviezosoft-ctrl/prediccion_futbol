import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:prediccion_futbol/main.dart';
import 'package:prediccion_futbol/models/fixture_summary.dart';
import 'package:prediccion_futbol/repositories/demo_football_repository.dart';

void main() {
  testWidgets('muestra los próximos partidos con datos demo', (tester) async {
    await tester.pumpWidget(
      FootballPredictorApp(
        repository: DemoFootballRepository(),
        startupError: null,
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Próximos partidos'), findsOneWidget);
    expect(find.text('DESTACADO'), findsOneWidget);
    expect(find.text('Real Madrid'), findsOneWidget);
    expect(find.text('Valencia'), findsOneWidget);
    expect(find.text('España'), findsNWidgets(2));
    expect(find.text('Predicción disponible'), findsAtLeastNWidgets(1));
    expect(find.text('Ver predicción'), findsOneWidget);
  });

  testWidgets(
    'muestra los países bajo cada club en una fila normal y pantalla angosta',
    (tester) async {
      tester.view.physicalSize = const Size(320, 800);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      final repository = DemoFootballRepository();
      final original = repository.fixtures[1];
      repository.fixtures[1] = FixtureSummary(
        id: original.id,
        homeTeam: original.homeTeam,
        awayTeam: original.awayTeam,
        homeTeamCountry: 'Inglaterra',
        awayTeamCountry: 'Gales',
        kickoff: original.kickoff,
        displayKickoff: original.displayKickoff,
        leagueId: original.leagueId,
        predictionAvailable: original.predictionAvailable,
        predictionStage: original.predictionStage,
      );

      await tester.pumpWidget(
        FootballPredictorApp(repository: repository, startupError: null),
      );
      await tester.pumpAndSettle();

      final homeCountry = find.byKey(
        const ValueKey('fixture-1002-home-country'),
      );
      final awayCountry = find.byKey(
        const ValueKey('fixture-1002-away-country'),
      );

      expect(homeCountry, findsOneWidget);
      expect(awayCountry, findsOneWidget);
      expect(tester.widget<Text>(homeCountry).data, 'Inglaterra');
      expect(tester.widget<Text>(awayCountry).data, 'Gales');
    },
  );

  testWidgets('abre el destacado y muestra líneas, estadísticas y jugadores', (
    tester,
  ) async {
    await tester.pumpWidget(
      FootballPredictorApp(
        repository: DemoFootballRepository(),
        startupError: null,
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.text('Ver predicción'));
    await tester.pumpAndSettle();

    expect(find.text('Predicción'), findsOneWidget);
    expect(find.text('España'), findsNWidgets(2));
    expect(find.text('Predicción 1X2 (probabilidad)'), findsOneWidget);
    expect(find.text('58%'), findsAtLeastNWidgets(1));
    expect(find.text('24%'), findsAtLeastNWidgets(1));
    expect(find.text('18%'), findsAtLeastNWidgets(1));
    await tester.scrollUntilVisible(
      find.byKey(const ValueKey('market-goals')),
      300,
    );
    expect(find.text('Goles totales'), findsAtLeastNWidgets(1));
    expect(find.text('Más de 0.5'), findsOneWidget);
    expect(find.text('Más de 1.5'), findsOneWidget);
    expect(find.text('Línea 2.5'), findsOneWidget);
    expect(find.text('Menos de 3.5'), findsOneWidget);
    expect(find.text('Menos de 4.5'), findsOneWidget);

    await tester.scrollUntilVisible(find.text('Estadísticas por equipo'), 300);
    expect(find.text('Estadísticas por equipo'), findsOneWidget);
    expect(find.text('Goles'), findsNothing);
    expect(find.text('Córners'), findsAtLeastNWidgets(1));
    expect(find.text('Remates'), findsAtLeastNWidgets(1));
    expect(find.text('Remates al arco'), findsAtLeastNWidgets(1));

    await tester.scrollUntilVisible(find.text('Totales de ambos equipos'), 300);
    expect(find.byKey(const ValueKey('market-corners')), findsOneWidget);

    await tester.scrollUntilVisible(find.text('Goleadores probables'), 300);
    expect(find.text('Goleadores probables'), findsOneWidget);
    expect(find.text('Vinícius Júnior'), findsOneWidget);

    await tester.scrollUntilVisible(find.text('Asistidores probables'), 300);
    expect(find.text('Asistidores probables'), findsOneWidget);
    expect(find.text('Jude Bellingham'), findsOneWidget);
    expect(find.text('Marcadores probables'), findsNothing);
  });
}
