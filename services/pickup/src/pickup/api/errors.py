"""Stable HTTP mapping for Pickup recovery domain errors."""

from __future__ import annotations

import logging
from typing import NoReturn

from fastapi import HTTPException

from pickup.domain.errors import (
    ConflictingIdempotencyKey,
    CustodyAlreadyStarted,
    InvalidRescheduleInput,
    MissingReassignmentDriver,
    PickupError,
    PickupTaskAlreadyAccepted,
    PickupTaskNotFound,
    PickupTaskNotRecoverable,
    StalePickupTaskVersion,
)
from pickup.domain.sanitize import sanitize_error_message
from pickup.ports.recovery_authorizer import AuthorizerUnavailableError

logger = logging.getLogger("pickup.api")

_ERROR_STATUS: dict[type[PickupError], int] = {
    PickupTaskNotFound: 404,
    PickupTaskAlreadyAccepted: 409,
    CustodyAlreadyStarted: 409,
    PickupTaskNotRecoverable: 409,
    ConflictingIdempotencyKey: 409,
    StalePickupTaskVersion: 409,
    InvalidRescheduleInput: 422,
    MissingReassignmentDriver: 422,
}

_ERROR_CODE: dict[type[PickupError], str] = {
    PickupTaskNotFound: "pickup_task_not_found",
    PickupTaskAlreadyAccepted: "pickup_task_already_accepted",
    CustodyAlreadyStarted: "custody_already_started",
    PickupTaskNotRecoverable: "pickup_task_not_recoverable",
    ConflictingIdempotencyKey: "conflicting_idempotency_key",
    StalePickupTaskVersion: "stale_pickup_task_version",
    InvalidRescheduleInput: "invalid_reschedule_input",
    MissingReassignmentDriver: "missing_reassignment_driver",
}


def raise_http_for_domain_error(exc: Exception) -> NoReturn:
    """Map domain/auth failures to HTTPException; never re-raise raw secrets."""
    if isinstance(exc, AuthorizerUnavailableError):
        raise HTTPException(status_code=503, detail="authorization unavailable") from None

    if isinstance(exc, PickupError):
        status = _ERROR_STATUS.get(type(exc), 400)
        code = _ERROR_CODE.get(type(exc), "pickup_error")
        detail = {"code": code, "message": sanitize_error_message(str(exc))}
        logger.info("recovery_rejected code=%s status=%s", code, status)
        raise HTTPException(status_code=status, detail=detail) from None

    logger.exception("recovery_unexpected error=%s", sanitize_error_message(str(exc)))
    raise HTTPException(status_code=500, detail="internal error") from None
