@echo off
REM One-click start with Docker (Windows). Requires Docker Desktop to be running.
cd /d "%~dp0"
docker compose up -d --build
if errorlevel 1 (
  echo.
  echo Failed to start. Is Docker Desktop installed and running?
  pause
  exit /b 1
)
echo.
echo Video Library is running at http://localhost:5000  (login: admin / admin)
echo Stop it with: docker compose down
start "" http://localhost:5000
pause
