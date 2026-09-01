#!/bin/sh
set -eu

CONFIG_RENDERED="/etc/nats/nats-server.conf"
GENERATED="${GENERATED_DIR:-/generated}"

if [ ! -f "${GENERATED}/nats-server.conf" ]; then
  echo "missing generated NATS config at ${GENERATED}/nats-server.conf" >&2
  exit 1
fi

cp "${GENERATED}/nats-server.conf" "${CONFIG_RENDERED}"
exec nats-server -c "${CONFIG_RENDERED}"
