#!/bin/sh
set -eu

CONFIG_RENDERED="/etc/nats/nats-server.conf"

: "${JETSTREAM_STORE_DIR:=/data/jetstream}"
: "${JETSTREAM_MAX_FILE_STORE:=2GB}"
: "${JETSTREAM_MAX_MEMORY_STORE:=256MB}"
: "${STREAM_MAX_MSG_SIZE:=262144}"
: "${NATS_AUTH_ENABLED:=false}"

mkdir -p "${JETSTREAM_STORE_DIR}"

cat > "${CONFIG_RENDERED}" <<EOF
server_name: hudhud-eventing-local
listen: 0.0.0.0:4222
http: 0.0.0.0:8222
EOF

if [ "${NATS_AUTH_ENABLED}" = "true" ]; then
  cat >> "${CONFIG_RENDERED}" <<'EOF'
authorization {
  users = [{ user: "dev-eventing", password: "dev-eventing-local-only" }]
}
EOF
fi

cat >> "${CONFIG_RENDERED}" <<EOF
jetstream {
  store_dir: "${JETSTREAM_STORE_DIR}"
  max_file_store: ${JETSTREAM_MAX_FILE_STORE}
  max_memory_store: ${JETSTREAM_MAX_MEMORY_STORE}
}
debug: false
trace: false
logtime: true
max_connections: 256
max_payload: ${STREAM_MAX_MSG_SIZE}
EOF

exec nats-server -c "${CONFIG_RENDERED}"
