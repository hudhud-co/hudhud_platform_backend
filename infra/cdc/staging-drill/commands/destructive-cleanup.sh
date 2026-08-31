#!/usr/bin/env sh
# CDC staging drill — DESTRUCTIVE cleanup (blocked by default).
# Requires CDC_DRILL_CONFIRM_DESTRUCTIVE=1 AND exact CDC_DRILL_SLOT_NAME.
# No recursive/broad cleanup. No automatic execution in default kit mode.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
CONFIG="${CDC_DRILL_CONFIG:-${ROOT}/config.example.env}"

# Preserve explicit environment overrides across config sourcing.
_SAVED_CONFIRM="${CDC_DRILL_CONFIRM_DESTRUCTIVE:-}"
_SAVED_SLOT="${CDC_DRILL_SLOT_NAME:-}"
_SAVED_DRY_RUN="${CDC_DRILL_DRY_RUN:-}"

if [ -f "${CONFIG}" ]; then
  # shellcheck disable=SC1090
  . "${CONFIG}"
fi

[ -n "${_SAVED_CONFIRM}" ] && CDC_DRILL_CONFIRM_DESTRUCTIVE="${_SAVED_CONFIRM}"
[ -n "${_SAVED_SLOT}" ] && CDC_DRILL_SLOT_NAME="${_SAVED_SLOT}"
[ -n "${_SAVED_DRY_RUN}" ] && CDC_DRILL_DRY_RUN="${_SAVED_DRY_RUN}"

DRY_RUN="${CDC_DRILL_DRY_RUN:-1}"
CONFIRM="${CDC_DRILL_CONFIRM_DESTRUCTIVE:-0}"
SLOT="${CDC_DRILL_SLOT_NAME:-}"

log() {
  printf '[cdc-drill-destructive] %s\n' "$*"
}

require_destructive_guard() {
  if [ "${CONFIRM}" != "1" ]; then
    log "BLOCKED: Set CDC_DRILL_CONFIRM_DESTRUCTIVE=1 to acknowledge destructive intent."
    exit 1
  fi
  if [ -z "${SLOT}" ]; then
    log "BLOCKED: Set CDC_DRILL_SLOT_NAME to the exact slot to drop."
    exit 1
  fi
  case "${SLOT}" in
    *'*'*|*'?'*)
      log "BLOCKED: Slot name must be exact literal — no wildcards."
      exit 1
      ;;
  esac
  case "${SLOT}" in
    hudhud_bridge_staging_*|hudhud_cdc_lab_*)
      ;;
    *)
      log "BLOCKED: Slot name must match drill prefix (hudhud_bridge_staging_* or hudhud_cdc_lab_*)."
      exit 1
      ;;
  esac
}

cmd="${1:-help}"

case "${cmd}" in
  drop-slot)
    require_destructive_guard
    DROP_SQL="SELECT pg_drop_replication_slot('${SLOT}');"
    if [ "${DRY_RUN}" = "1" ]; then
      log "DRY-RUN: would execute: ${DROP_SQL}"
      log "Set CDC_DRILL_DRY_RUN=0 to perform drop (still requires CONFIRM=1 and exact SLOT)."
      exit 0
    fi
    if [ -z "${CDC_DRILL_PG_HOST:-}" ] || [ -z "${CDC_DRILL_PG_DATABASE:-}" ]; then
      log "ERROR: Set connection variables in local config before destructive execution."
      exit 1
    fi
    log "EXEC: dropping slot '${SLOT}'"
    psql \
      "host=${CDC_DRILL_PG_HOST} port=${CDC_DRILL_PG_PORT:-5432} dbname=${CDC_DRILL_PG_DATABASE} user=${CDC_DRILL_PG_USER_REPLICATION:-} sslmode=${CDC_DRILL_PG_SSLMODE:-verify-full}" \
      -v ON_ERROR_STOP=1 \
      -c "${DROP_SQL}"
    log "Slot drop requested — verify with readonly.sh slot-inspect"
    ;;
  help|*)
    log "Usage: destructive-cleanup.sh drop-slot"
    log "Defaults: CDC_DRILL_CONFIRM_DESTRUCTIVE=0, CDC_DRILL_DRY_RUN=1 — blocked."
    ;;
esac
