#!/bin/sh
# One-click start with Docker (Linux / macOS).
set -e
cd "$(dirname "$0")"
mkdir -p data
if docker compose version >/dev/null 2>&1; then
  docker compose up -d --build
else
  docker-compose up -d --build
fi
echo
echo "Video Library is running at http://localhost:${APP_PORT:-5000}  (login: admin / admin)"
echo "Stop it with: docker compose down"
