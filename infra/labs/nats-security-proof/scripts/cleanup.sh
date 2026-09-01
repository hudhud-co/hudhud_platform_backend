#!/usr/bin/env sh
# Dedicated cleanup for hudhud-nats-security-proof-lab Compose project.
# Removes only the lab project containers, network, and volumes.
set -eu

LAB_DIR="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
COMPOSE_FILE="${LAB_DIR}/compose.yaml"
PROJECT="${COMPOSE_PROJECT_NAME:-hudhud-nats-security-proof-lab}"

docker compose -f "${COMPOSE_FILE}" -p "${PROJECT}" --profile nats-security-proof down -v --remove-orphans

docker network inspect hudhud_nats_security_proof >/dev/null 2>&1 && exit 1 || true
docker volume inspect hudhud_nats_security_proof_jetstream >/dev/null 2>&1 && exit 1 || true
docker volume inspect hudhud_nats_security_proof_generated >/dev/null 2>&1 && exit 1 || true

echo "HUDHUD_NATS_SECURITY_PROOF_CLEANED"
