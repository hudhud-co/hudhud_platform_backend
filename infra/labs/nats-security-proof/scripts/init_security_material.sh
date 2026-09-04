#!/bin/sh
# Generate disposable TLS material and NATS operator/account/user JWT identities.
# Outputs to GENERATED_DIR (default /generated). No secrets are committed to git.
set -eu

GENERATED="${GENERATED_DIR:-/generated}"
NSC_HOME="${NSC_HOME:-${GENERATED}/nsc-home}"
NKEYS_PATH="${NKEYS_PATH:-${GENERATED}/nsc-home/keys}"

if [ -f "${GENERATED}/nats-server.conf" ] && [ -f "${GENERATED}/creds/hudhud-eventing-bootstrap.creds" ]; then
  echo "HUDHUD_NATS_SECURITY_MATERIAL_ALREADY_PRESENT"
  exit 0
fi

mkdir -p "${GENERATED}/ca" "${GENERATED}/tls" "${GENERATED}/jwt/accounts" "${GENERATED}/creds" "${NSC_HOME}" "${NKEYS_PATH}"

# --- TLS CA and server certificate (runtime only) ---
openssl genrsa -out "${GENERATED}/ca/ca-key.pem" 4096 2>/dev/null
openssl req -new -x509 -days 1 -key "${GENERATED}/ca/ca-key.pem" -out "${GENERATED}/ca/ca.pem" \
  -subj "/CN=HUDHUD-NATS-SECURITY-PROOF-CA"

openssl genrsa -out "${GENERATED}/tls/server-key.pem" 2048 2>/dev/null
openssl req -new -key "${GENERATED}/tls/server-key.pem" -out "${GENERATED}/tls/server.csr" \
  -subj "/CN=hudhud-nats-security-proof" \
  -addext "subjectAltName=DNS:hudhud-nats-security-proof,DNS:nats,IP:127.0.0.1"

openssl x509 -req -in "${GENERATED}/tls/server.csr" \
  -CA "${GENERATED}/ca/ca.pem" \
  -CAkey "${GENERATED}/ca/ca-key.pem" \
  -CAcreateserial \
  -out "${GENERATED}/tls/server.pem" \
  -days 1 \
  -copy_extensions copy

# --- NATS operator/account/users via nsc ---
rm -rf "${NSC_HOME}"
mkdir -p "${NSC_HOME}" "${NKEYS_PATH}"
export NSC_HOME
export NKEYS_PATH

nsc env -s "${NSC_HOME}"
nsc add operator --name HUDHUD --sys
nsc add account --name HUDHUD
nsc edit account --name HUDHUD --js-enable 0
nsc edit account --name HUDHUD --js-disk-storage 512MB --js-mem-storage 64MB

add_user() {
  name="$1"
  shift
  nsc add user --account HUDHUD --name "${name}" "$@"
  nsc generate creds --account HUDHUD --name "${name}" \
    --output-file "${GENERATED}/creds/${name}.creds"
}

# Bootstrap — topology admin only
add_user hudhud-eventing-bootstrap \
  --allow-pub '$JS.API.INFO' \
  --allow-pub '$JS.API.STREAM.CREATE.>' \
  --allow-pub '$JS.API.STREAM.UPDATE.>' \
  --allow-pub '$JS.API.STREAM.INFO.>' \
  --allow-pub '$JS.API.STREAM.NAMES' \
  --allow-pub '$JS.API.STREAM.LIST' \
  --allow-pub '$JS.API.CONSUMER.CREATE.>' \
  --allow-pub '$JS.API.CONSUMER.DURABLE.CREATE.>' \
  --allow-pub '$JS.API.CONSUMER.INFO.>' \
  --allow-sub '_INBOX.>'

# Bridge publishers (rotation overlap v1/v2)
for version in v1 v2; do
  add_user "legacy-event-bridge-${version}" \
    --allow-pub 'hudhud.shipment.legacy_bridge.observation.shipment_timeline_entry.v1' \
    --allow-pub 'hudhud.audit.legacy_bridge.observation.audit_entry.v1' \
    --allow-sub '_INBOX.>'
done

# Audit consumers (rotation overlap v1/v2)
for version in v1 v2; do
  add_user "audit-${version}" \
    --allow-pub '$JS.API.CONSUMER.INFO.HUDHUD_AUDIT.audit_bridge_entry_v1' \
    --allow-pub '$JS.API.CONSUMER.MSG.NEXT.HUDHUD_AUDIT.audit_bridge_entry_v1' \
    --allow-pub '$JS.ACK.HUDHUD_AUDIT.audit_bridge_entry_v1.>' \
    --allow-sub '_INBOX.>' \
    --allow-sub '$JS.FC.HUDHUD_AUDIT.>'
done

# Tracking consumers (rotation overlap v1/v2)
for version in v1 v2; do
  add_user "tracking-${version}" \
    --allow-pub '$JS.API.CONSUMER.INFO.HUDHUD_SHIPMENT.tracking_bridge_timeline_v1' \
    --allow-pub '$JS.API.CONSUMER.MSG.NEXT.HUDHUD_SHIPMENT.tracking_bridge_timeline_v1' \
    --allow-pub '$JS.ACK.HUDHUD_SHIPMENT.tracking_bridge_timeline_v1.>' \
    --allow-sub '_INBOX.>' \
    --allow-sub '$JS.FC.HUDHUD_SHIPMENT.>'
done

# Pickup publishers (rotation overlap v1/v2)
for version in v1 v2; do
  add_user "pickup-${version}" \
    --allow-pub 'hudhud.pickup.pickup.fact.accepted.v1' \
    --allow-sub '_INBOX.>'
done

# Shipment consumers (rotation overlap v1/v2)
for version in v1 v2; do
  add_user "shipment-${version}" \
    --allow-pub '$JS.API.CONSUMER.INFO.HUDHUD_PICKUP.shipment_pickup_facts_v1' \
    --allow-pub '$JS.API.CONSUMER.MSG.NEXT.HUDHUD_PICKUP.shipment_pickup_facts_v1' \
    --allow-pub '$JS.ACK.HUDHUD_PICKUP.shipment_pickup_facts_v1.>' \
    --allow-sub '_INBOX.>' \
    --allow-sub '$JS.FC.HUDHUD_PICKUP.>'
done

# Break-glass inspection identity
add_user hudhud-nats-break-glass \
  --allow-pub '$JS.API.STREAM.INFO.>' \
  --allow-pub '$JS.API.CONSUMER.INFO.>' \
  --allow-sub '_INBOX.>'

# Export operator JWT and account JWT for resolver
OPERATOR_JWT_PATH="${NSC_HOME}/HUDHUD/HUDHUD.jwt"
ACCOUNT_JWT_PATH="${NSC_HOME}/HUDHUD/accounts/HUDHUD/HUDHUD.jwt"
SYS_JWT_PATH="${NSC_HOME}/HUDHUD/accounts/SYS/SYS.jwt"
ACCOUNT_ID="$(nsc describe account HUDHUD | awk -F'|' '/Account ID/ {gsub(/^[ \t]+|[ \t]+$/, "", $3); print $3}')"
SYS_ACCOUNT_ID="$(nsc describe account SYS | awk -F'|' '/Account ID/ {gsub(/^[ \t]+|[ \t]+$/, "", $3); print $3}')"

cp "${OPERATOR_JWT_PATH}" "${GENERATED}/jwt/operator.jwt"
cp "${ACCOUNT_JWT_PATH}" "${GENERATED}/jwt/accounts/${ACCOUNT_ID}.jwt"
cp "${SYS_JWT_PATH}" "${GENERATED}/jwt/accounts/${SYS_ACCOUNT_ID}.jwt"

# Render NATS server config (TLS required, JWT resolver)
cat > "${GENERATED}/nats-server.conf" <<EOF
server_name: hudhud-nats-security-proof
listen: 0.0.0.0:4222
http: 127.0.0.1:8222

tls {
  cert_file: "${GENERATED}/tls/server.pem"
  key_file: "${GENERATED}/tls/server-key.pem"
}

operator: ${GENERATED}/jwt/operator.jwt
system_account: ${SYS_ACCOUNT_ID}
resolver: {
  type: full
  dir: ${GENERATED}/jwt/accounts
  interval: "2s"
  allow_delete: true
}

jetstream {
  store_dir: /data/jetstream
  max_file_store: 1GB
  max_memory_store: 256MB
}

max_connections: 64
max_payload: 262144
debug: false
trace: false
logtime: true
EOF

echo "HUDHUD_NATS_SECURITY_MATERIAL_GENERATED"
echo "account_id=${ACCOUNT_ID}"
