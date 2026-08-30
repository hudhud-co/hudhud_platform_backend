#!/usr/bin/env sh
# Dedicated cleanup for hudhud-legacy-polling-lab Compose project.
set -eu

LAB_DIR="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
COMPOSE_FILE="${LAB_DIR}/compose.yaml"
PROJECT="${COMPOSE_PROJECT_NAME:-hudhud-legacy-polling-lab}"

docker compose -f "${COMPOSE_FILE}" -p "${PROJECT}" --profile polling-lab down -v --remove-orphans

# Prove dedicated resources removed (ignore errors if already absent).
docker network inspect hudhud_polling_lab >/dev/null 2>&1 && exit 1 || true
docker volume inspect hudhud_polling_lab_pgdata >/dev/null 2>&1 && exit 1 || true

echo "HUDHUD_POLLING_LAB_CLEANED"
