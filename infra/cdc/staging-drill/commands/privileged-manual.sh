#!/usr/bin/env sh
# CDC staging drill — PRIVILEGED / MANUAL command templates.
# Does NOT auto-create slots. Prints replication-protocol steps for operator execution.
# Requires: replication role, change window, preflight PASS.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
CONFIG="${CDC_DRILL_CONFIG:-${ROOT}/config.example.env}"

if [ -f "${CONFIG}" ]; then
  # shellcheck disable=SC1090
  . "${CONFIG}"
fi

DRY_RUN="${CDC_DRILL_DRY_RUN:-1}"
SLOT="${CDC_DRILL_SLOT_NAME:-<SET_CDC_DRILL_SLOT_NAME>}"
PLUGIN="${CDC_DRILL_PLUGIN:-pgoutput}"
DB="${CDC_DRILL_PG_DATABASE:-<database>}"

log() {
  printf '[cdc-drill-privileged] %s\n' "$*"
}

cmd="${1:-help}"

case "${cmd}" in
  coordinated-hwm-template)
    log "MANUAL STEP — replication protocol required (ADR-0007 G4)."
    log "Do NOT substitute ordinary SQL snapshot or row COUNT for this step."
    log ""
    log "Option A — pg_recvlogical (example template; adjust connection flags):"
    log "  pg_recvlogical -d \"${DB}\" --slot=${SLOT} --create-slot --if-not-exists \\"
    log "    --plugin=${PLUGIN} --start"
    log "  # Use client support for EXPORT_SNAPSHOT; capture returned snapshot_id + restart_lsn."
    log ""
    log "Option B — SQL protocol via replication connection (not plain psql session):"
    log "  CREATE_REPLICATION_SLOT \"${SLOT}\" LOGICAL ${PLUGIN} EXPORT_SNAPSHOT;"
    log "  # Returns snapshot_name and consistent_point in replication protocol message."
    log ""
    log "Record in evidence manifest hwm_coordination section."
    if [ "${DRY_RUN}" = "0" ]; then
      log "ERROR: This script does not execute replication-protocol commands."
      log "Set CDC_DRILL_DRY_RUN=1 and run steps manually with approved client."
      exit 1
    fi
    ;;
  peek-changes-template)
    log "MANUAL — peek without advancing (prefer until durable landing ready):"
    log "  SELECT * FROM pg_logical_slot_peek_changes('${SLOT}', NULL, NULL);"
    log "Bridge MUST land durably before pg_logical_slot_get_changes / feedback."
    ;;
  publication-create-template)
    log "MANUAL — privileged DDL (not executed by kit):"
    log "  CREATE PUBLICATION ${CDC_DRILL_PUBLICATION_NAME:-hudhud_bridge_staging_pub}"
    log "    FOR TABLE public.shipment_events, public.audit_logs;"
    log "Requires appropriate DDL privileges and ops approval."
    ;;
  help|*)
    log "Usage: privileged-manual.sh coordinated-hwm-template|peek-changes-template|publication-create-template"
    log "Templates only — never auto-executed. CDC_DRILL_DRY_RUN=${DRY_RUN}"
    ;;
esac
