"""Stable domain-error → HTTP status mapping (secret-safe details)."""

from __future__ import annotations

from fastapi import HTTPException

from shipment.domain.errors import (
    AcceptanceAlreadyRecorded,
    ActingDriverNotAssigned,
    ConflictingIdempotencyKey,
    ExceptionEvidenceRequired,
    InlineMediaNotAllowed,
    OptimisticConcurrencyConflict,
    PickupConditionProofMissing,
    PickupTaskMissingAssignedBatch,
    PickupTaskMissingAssignedDriver,
    PickupTaskNotFound,
    PickupTaskNotProofCaptured,
    ScannedIdentifierMismatch,
    ShipmentError,
    ShipmentNotCreated,
    ShipmentNotFound,
)

_NOT_FOUND = (ShipmentNotFound, PickupTaskNotFound)
_CONFLICT = (
    AcceptanceAlreadyRecorded,
    OptimisticConcurrencyConflict,
    ConflictingIdempotencyKey,
)
_UNPROCESSABLE = (
    ActingDriverNotAssigned,
    ExceptionEvidenceRequired,
    InlineMediaNotAllowed,
    PickupConditionProofMissing,
    PickupTaskMissingAssignedBatch,
    PickupTaskMissingAssignedDriver,
    PickupTaskNotProofCaptured,
    ScannedIdentifierMismatch,
    ShipmentNotCreated,
)


def http_exception_for_domain_error(exc: ShipmentError) -> HTTPException:
    """Map domain errors to stable HTTP responses without leaking secrets."""
    if isinstance(exc, _NOT_FOUND):
        return HTTPException(status_code=404, detail="not found")
    if isinstance(exc, ConflictingIdempotencyKey):
        return HTTPException(status_code=409, detail="idempotency key conflict")
    if isinstance(exc, AcceptanceAlreadyRecorded):
        return HTTPException(status_code=409, detail="acceptance already recorded")
    if isinstance(exc, OptimisticConcurrencyConflict):
        return HTTPException(status_code=409, detail="conflict")
    if isinstance(exc, _UNPROCESSABLE):
        return HTTPException(status_code=422, detail="acceptance prerequisites not met")
    if isinstance(exc, _CONFLICT):
        return HTTPException(status_code=409, detail="conflict")
    return HTTPException(status_code=400, detail="bad request")
