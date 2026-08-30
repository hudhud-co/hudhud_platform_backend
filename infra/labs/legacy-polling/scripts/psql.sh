#!/usr/bin/env sh
# psql wrapper via postgres service exec (no host port required).
set -eu

LAB_DIR="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
COMPOSE_FILE="${LAB_DIR}/compose.yaml"
PROJECT="${COMPOSE_PROJECT_NAME:-hudhud-legacy-polling-lab}"

docker compose -f "${COMPOSE_FILE}" -p "${PROJECT}" --profile polling-lab \
  exec -T postgres psql -U polling_lab -d polling_lab -v ON_ERROR_STOP=1 "$@"
