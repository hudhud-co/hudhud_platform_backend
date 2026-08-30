#!/usr/bin/env sh
# Tear down the isolated legacy CDC lab (containers, dedicated network, lab volume).
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
COMPOSE_FILE="${ROOT}/compose.yaml"

echo "Stopping HUDHUD legacy CDC lab..."
docker compose -f "${COMPOSE_FILE}" --profile cdc-lab down -v --remove-orphans

echo "HUDHUD_CDC_LAB_CLEANUP_COMPLETE"
