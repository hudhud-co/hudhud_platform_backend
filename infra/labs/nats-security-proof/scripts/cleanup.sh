#!/usr/bin/env sh
# Dedicated cleanup for hudhud-nats-security-proof-lab Compose project.
# Removes only the lab project containers, network, and volumes.
set -eu

# Labs are namespaced per run (see tests/lab_namespace.py) so two can run at once.
# An empty suffix targets the original fixed names, which is what a CI job that runs
# one suite per job wants.
SUFFIX="${HUDHUD_LAB_SUFFIX:-}"

LAB_DIR="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
COMPOSE_FILE="${LAB_DIR}/compose.yaml"
PROJECT="${COMPOSE_PROJECT_NAME:-hudhud-nats-security-proof-lab${SUFFIX}}"

docker compose -f "${COMPOSE_FILE}" -p "${PROJECT}" --profile nats-security-proof down -v --remove-orphans

docker network inspect hudhud_nats_security_proof"${SUFFIX}" >/dev/null 2>&1 && exit 1 || true
docker volume inspect hudhud_nats_security_proof_jetstream"${SUFFIX}" >/dev/null 2>&1 && exit 1 || true
docker volume inspect hudhud_nats_security_proof_generated"${SUFFIX}" >/dev/null 2>&1 && exit 1 || true

echo "HUDHUD_NATS_SECURITY_PROOF_CLEANED"
