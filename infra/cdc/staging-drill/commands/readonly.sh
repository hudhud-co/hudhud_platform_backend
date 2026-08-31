#!/usr/bin/env sh
# CDC staging drill — READ-ONLY command templates (default tier).
# Safe by default: CDC_DRILL_DRY_RUN=1 prints intended commands without executing.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
CONFIG="${CDC_DRILL_CONFIG:-${ROOT}/config.example.env}"

if [ -f "${CONFIG}" ]; then
  # shellcheck disable=SC1090
  . "${CONFIG}"
fi

DRY_RUN="${CDC_DRILL_DRY_RUN:-1}"

log() {
  printf '[cdc-drill-readonly] %s\n' "$*"
}

run_or_print() {
  if [ "${DRY_RUN}" = "1" ]; then
    log "DRY-RUN: $*"
  else
    log "EXEC: $*"
    "$@"
  fi
}

cmd="${1:-help}"

case "${cmd}" in
  preflight)
    PREFLIGHT_SQL="${ROOT}/sql/preflight-readonly.sql"
    if [ -z "${CDC_DRILL_PG_HOST:-}" ] || [ -z "${CDC_DRILL_PG_DATABASE:-}" ]; then
      log "DRY-RUN: psql -h <host> -p <port> -d <database> -U <readonly_user> -f ${PREFLIGHT_SQL}"
      log "Set CDC_DRILL_PG_* in local config before execution."
      exit 0
    fi
    run_or_print psql \
      "host=${CDC_DRILL_PG_HOST} port=${CDC_DRILL_PG_PORT:-5432} dbname=${CDC_DRILL_PG_DATABASE} user=${CDC_DRILL_PG_USER_READONLY:-} sslmode=${CDC_DRILL_PG_SSLMODE:-verify-full}" \
      -v ON_ERROR_STOP=1 \
      -f "${PREFLIGHT_SQL}"
    ;;
  slot-inspect)
    INSPECT_SQL="SELECT slot_name, plugin, active, restart_lsn::text, confirmed_flush_lsn::text, wal_status, pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn) AS lag_bytes FROM pg_replication_slots ORDER BY slot_name;"
    if [ -z "${CDC_DRILL_PG_HOST:-}" ]; then
      log "DRY-RUN: psql ... -c \"${INSPECT_SQL}\""
      exit 0
    fi
    run_or_print psql \
      "host=${CDC_DRILL_PG_HOST} port=${CDC_DRILL_PG_PORT:-5432} dbname=${CDC_DRILL_PG_DATABASE} user=${CDC_DRILL_PG_USER_READONLY:-} sslmode=${CDC_DRILL_PG_SSLMODE:-verify-full}" \
      -v ON_ERROR_STOP=1 \
      -c "${INSPECT_SQL}"
    ;;
  validate-manifest)
    MANIFEST="${2:-${ROOT}/templates/evidence-manifest.yaml}"
    run_or_print uv run python "${ROOT}/validate.py" --config "${CONFIG}" --manifest "${MANIFEST}"
    ;;
  help|*)
    log "Usage: readonly.sh preflight|slot-inspect|validate-manifest [manifest-path]"
    log "Default CDC_DRILL_DRY_RUN=1 — read-only tier never mutates PostgreSQL."
    ;;
esac
