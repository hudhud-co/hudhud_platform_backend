#!/usr/bin/env sh
# Dedicated cleanup for hudhud-pickup-acceptance-eventing-proof-lab Compose project.
# Removes only the lab project containers, network hudhud_pickup_acceptance_eventing_proof,
# and volumes hudhud_pickup_acceptance_eventing_proof_pgdata / _jetstream.
set -eu

# Labs are namespaced per run (see tests/lab_namespace.py) so two can run at once.
# An empty suffix targets the original fixed names, which is what a CI job that runs
# one suite per job wants.
SUFFIX="${HUDHUD_LAB_SUFFIX:-}"

LAB_DIR="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
COMPOSE_FILE="${LAB_DIR}/compose.yaml"
PROJECT="${COMPOSE_PROJECT_NAME:-hudhud-pickup-acceptance-eventing-proof-lab${SUFFIX}}"

docker compose -f "${COMPOSE_FILE}" -p "${PROJECT}" --profile pickup-acceptance-eventing-proof down -v --remove-orphans

docker network inspect hudhud_pickup_acceptance_eventing_proof"${SUFFIX}" >/dev/null 2>&1 && exit 1 || true
docker volume inspect hudhud_pickup_acceptance_eventing_proof_pgdata"${SUFFIX}" >/dev/null 2>&1 && exit 1 || true
docker volume inspect hudhud_pickup_acceptance_eventing_proof_jetstream"${SUFFIX}" >/dev/null 2>&1 && exit 1 || true

echo "HUDHUD_PICKUP_ACCEPTANCE_EVENTING_PROOF_CLEANED"
