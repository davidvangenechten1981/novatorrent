@echo off
rem Build and smoke-test the NovaTorrent image locally. Requires Docker Desktop to be running.
cd /d "%~dp0"

docker info >nul 2>&1
if errorlevel 1 (
  echo Docker draait niet. Start Docker Desktop en probeer opnieuw.
  pause
  exit /b 1
)

echo === Bouwen ===
docker build -t novatorrent:dev . || goto fail

if not exist "_test\cfg" mkdir "_test\cfg"
if not exist "_test\dat" mkdir "_test\dat"

echo === Starten (met de veilige opties) ===
docker rm -f novatorrent-test >nul 2>&1
set "TESTPW=t%RANDOM%%RANDOM%%RANDOM%x"
docker run -d --name novatorrent-test -p 8080:8080 ^
  -e NOVA_ADMIN_PASSWORD=%TESTPW% ^
  -v "%cd%\_test\cfg:/config" -v "%cd%\_test\dat:/data" ^
  --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true ^
  novatorrent:dev || goto fail

echo Wachten op healthcheck...
for /l %%i in (1,1,12) do (
  timeout /t 5 /nobreak >nul
  for /f %%s in ('docker inspect -f "{{.State.Health.Status}}" novatorrent-test') do (
    echo   status: %%s
    if "%%s"=="healthy" goto ok
  )
)
echo Niet healthy. Logs:
docker logs --tail 50 novatorrent-test
goto fail

:ok
echo.
echo OK: open http://localhost:8080 (gebruiker admin, wachtwoord: %TESTPW%).
echo Opruimen: docker rm -f novatorrent-test
pause
exit /b 0

:fail
echo MISLUKT. Zie de uitvoer hierboven.
pause
exit /b 1
