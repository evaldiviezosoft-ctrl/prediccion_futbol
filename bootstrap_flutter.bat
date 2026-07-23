@echo off
cd /d %~dp0prediccion_futbol
flutter pub get
if errorlevel 1 exit /b 1
echo.
echo Proyecto preparado. Para probar sin claves:
echo flutter run -d chrome --web-hostname localhost --web-port 5173 --dart-define-from-file=config/demo.json
echo.
echo Para usar el backend local en Web:
echo flutter run -d chrome --web-hostname localhost --web-port 5173 --dart-define-from-file=config/dev.web.json
pause
