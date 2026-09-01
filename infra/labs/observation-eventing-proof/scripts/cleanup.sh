#!/usr/bin/env sh
# Dedicated cleanup for hudhud-observation-eventing-proof-lab Compose project.
# Removes only the lab project containers, network hudhud_observation_eventing_proof,
# and volumes hudhud_observation_eventing_proof_pgdata / _jetstream.
set -eu

LAB_DIR="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
COMPOSE_FILE="${LAB_DIR}/compose.yaml"
PROJECT="${COMPOSE_PROJECT_NAME:-hudhud-observation-eventing-proof-lab}"

docker compose -f "${COMPOSE_FILE}" -p "${PROJECT}" --profile observation-eventing-proof down -v --remove-orphans

docker network inspect hudhud_observation_eventing_proof >/dev/null 2>&1 && exit 1 || true
docker volume inspect hudhud_observation_eventing_proof_pgdata >/dev/null 2>&1 && exit 1 || true
docker volume inspect hudhud_observation_eventing_proof_jetstream >/dev/null 2>&1 && exit 1 || true

echo "HUDHUD_OBSERVATION_EVENTING_PROOF_CLEANED"
