"""Replay one captured offline command through its owning application service.

The queue never writes domain state itself: every replayed command goes through the
same lifecycle service, validation, and audit path as an online request. Operations
whose authority cannot exist offline are deferred rather than silently applied.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from pickup.application.task_lifecycle_service import (
    CaptureProofCommand,
    FailTaskCommand,
    PickupTaskLifecycleService,
    ReportExceptionCommand,
    ScanTaskCommand,
    TaskCommand,
    TaskLifecycleResult,
)
from pickup.domain.offline import DEFERRED_OFFLINE_OPERATIONS, OfflineOperation


class DeferredOfflineOperation(Exception):
    """The capture is preserved until its owning workflow can authorize it."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class InvalidOfflinePayload(ValueError):
    """The captured payload cannot be turned into a valid command."""


@dataclass(frozen=True, slots=True)
class OfflineCommandContext:
    driver_user_id: str
    operation_id: UUID
    resource_id: UUID
    captured_at: datetime
    payload: dict[str, Any]


class OfflineOperationDispatcher:
    """Translate a preserved capture into the owning Pickup command."""

    def __init__(self, lifecycle: PickupTaskLifecycleService) -> None:
        self._lifecycle = lifecycle

    def dispatch(
        self,
        *,
        operation: OfflineOperation,
        context: OfflineCommandContext,
        actor,
    ) -> TaskLifecycleResult:
        if operation in DEFERRED_OFFLINE_OPERATIONS:
            raise DeferredOfflineOperation(
                "custody acceptance needs the live sender ceremony and cannot be "
                "authorized from an offline capture"
            )

        request_id = str(context.operation_id)
        base = {
            "pickup_task_id": context.resource_id,
            "acting_driver_user_id": context.driver_user_id,
            "occurred_at": context.captured_at,
            "request_id": request_id,
        }
        payload = context.payload or {}

        if operation is OfflineOperation.ARRIVE:
            return self._lifecycle.arrive(TaskCommand(**base), actor=actor)
        if operation is OfflineOperation.SCAN:
            return self._lifecycle.scan(
                ScanTaskCommand(
                    **base,
                    scanned_identifier=_required_str(payload, "scanned_identifier"),
                ),
                actor=actor,
            )
        if operation is OfflineOperation.CAPTURE_PROOF:
            return self._lifecycle.capture_proof(
                CaptureProofCommand(
                    **base,
                    package_condition_status=_required_str(
                        payload, "package_condition_status"
                    ),
                    condition_notes=_optional_str(payload, "condition_notes"),
                    evidence_present=bool(payload.get("evidence_present", False)),
                ),
                actor=actor,
            )
        if operation is OfflineOperation.REPORT_EXCEPTION:
            return self._lifecycle.report_exception(
                ReportExceptionCommand(
                    **base,
                    reason=_required_str(payload, "reason"),
                    notes=_optional_str(payload, "notes"),
                    contact_attempted=bool(payload.get("contact_attempted", False)),
                    contact_attempt_count=int(payload.get("contact_attempt_count", 0) or 0),
                ),
                actor=actor,
            )
        if operation is OfflineOperation.FAIL:
            return self._lifecycle.fail(
                FailTaskCommand(
                    **base,
                    reason=_required_str(payload, "reason"),
                    notes=_optional_str(payload, "notes"),
                ),
                actor=actor,
            )
        msg = f"unsupported offline operation: {operation}"
        raise InvalidOfflinePayload(msg)


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        msg = f"{key} is required"
        raise InvalidOfflinePayload(msg)
    return value.strip()


def _optional_str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        msg = f"{key} must be a string"
        raise InvalidOfflinePayload(msg)
    return value.strip() or None
