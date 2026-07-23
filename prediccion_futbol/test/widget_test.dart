import 'package:flutter_test/flutter_test.dart';
import 'package:prediccion_futbol/main.dart';
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
    expect(find.text('Predicción disponible'), findsAtLeastNWidgets(1));
    expect(find.text('Ver predicción'), findsOneWidget);
  });

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
    expect(find.text('Predicción 1X2 (probabilidad)'), findsOneWidget);
    expect(find.text('58%'), findsAtLeastNWidgets(1));
    expect(find.text('24%'), findsAtLeastNWidgets(1));
    expect(find.text('18%'), findsAtLeastNWidgets(1));

    await tester.scrollUntilVisible(find.text('Goles totales'), 300);
    expect(find.text('Goles totales'), findsOneWidget);
    expect(find.text('Más de 0.5 goles'), findsOneWidget);
    expect(find.text('Más de 1.5 goles'), findsOneWidget);
    expect(find.text('Más de 2.5 goles'), findsOneWidget);
    expect(find.text('Más de 3.5 goles'), findsOneWidget);
    expect(find.text('Más de 4.5 goles'), findsOneWidget);

    await tester.scrollUntilVisible(find.text('Estadísticas por equipo'), 300);
    expect(find.text('Estadísticas por equipo'), findsOneWidget);
    expect(find.text('Goles'), findsNothing);
    expect(find.text('Córners'), findsOneWidget);
    expect(find.text('Remates'), findsOneWidget);
    expect(find.text('Remates al arco'), findsOneWidget);

    await tester.scrollUntilVisible(find.text('Goleadores probables'), 300);
    expect(find.text('Goleadores probables'), findsOneWidget);
    expect(find.text('Vinícius Júnior'), findsOneWidget);

    await tester.scrollUntilVisible(find.text('Asistidores probables'), 300);
    expect(find.text('Asistidores probables'), findsOneWidget);
    expect(find.text('Jude Bellingham'), findsOneWidget);
    expect(find.text('Marcadores probables'), findsNothing);
  });
}
