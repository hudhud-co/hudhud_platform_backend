#!/bin/sh
# Verify NATS JetStream health and observability endpoints.
set -eu

MONITOR_URL="${NATS_MONITOR_URL:-http://127.0.0.1:8222}"

health="$(wget -qO- "${MONITOR_URL}/healthz" 2>/dev/null || true)"
if [ "${health}" != "ok" ] && [ "${health}" != '{"status":"ok"}' ]; then
  echo "NATS health check failed: ${health:-<empty>}" >&2
  exit 1
fi

jsz="$(wget -qO- "${MONITOR_URL}/jsz?streams=1" 2>/dev/null || true)"
if [ -z "${jsz}" ]; then
  echo "JetStream metrics endpoint unavailable" >&2
  exit 1
fi

echo "HUDHUD_EVENTING_HEALTH_OK"
echo "${jsz}"
