#!/usr/bin/env sh
# Dedicated cleanup for hudhud-service-postgres-proof-lab Compose project.
# Removes only the lab project containers, network hudhud_service_postgres_proof,
# and volume hudhud_service_postgres_proof_pgdata.
set -eu

LAB_DIR="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
COMPOSE_FILE="${LAB_DIR}/compose.yaml"
PROJECT="${COMPOSE_PROJECT_NAME:-hudhud-service-postgres-proof-lab}"

docker compose -f "${COMPOSE_FILE}" -p "${PROJECT}" --profile service-postgres-proof down -v --remove-orphans

# Prove dedicated resources removed (ignore errors if already absent).
docker network inspect hudhud_service_postgres_proof >/dev/null 2>&1 && exit 1 || true
docker volume inspect hudhud_service_postgres_proof_pgdata >/dev/null 2>&1 && exit 1 || true

echo "HUDHUD_SERVICE_POSTGRES_PROOF_CLEANED"
