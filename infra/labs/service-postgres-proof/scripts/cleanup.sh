#!/usr/bin/env sh
# Dedicated cleanup for hudhud-service-postgres-proof-lab Compose project.
# Removes only the lab project containers, network hudhud_service_postgres_proof,
# and volume hudhud_service_postgres_proof_pgdata.
set -eu

# Labs are namespaced per run (see tests/lab_namespace.py) so two can run at once.
# An empty suffix targets the original fixed names, which is what a CI job that runs
# one suite per job wants.
SUFFIX="${HUDHUD_LAB_SUFFIX:-}"

LAB_DIR="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
COMPOSE_FILE="${LAB_DIR}/compose.yaml"
PROJECT="${COMPOSE_PROJECT_NAME:-hudhud-service-postgres-proof-lab${SUFFIX}}"

docker compose -f "${COMPOSE_FILE}" -p "${PROJECT}" --profile service-postgres-proof down -v --remove-orphans

# Prove dedicated resources removed (ignore errors if already absent).
docker network inspect hudhud_service_postgres_proof"${SUFFIX}" >/dev/null 2>&1 && exit 1 || true
docker volume inspect hudhud_service_postgres_proof_pgdata"${SUFFIX}" >/dev/null 2>&1 && exit 1 || true

echo "HUDHUD_SERVICE_POSTGRES_PROOF_CLEANED"
