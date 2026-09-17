"""Building the driver services for one unit of work.

Lives here rather than in `main` so that `api/driver_dependencies` can import it without
a cycle — `main` imports the routers, and the routers import the dependencies. It is
called once at startup to decide which routers to mount, and then once per request to
build the services over that request's unit of work.
"""

from __future__ import annotations

from dataclasses import dataclass

from pickup.application.acceptance_service import PickupAcceptanceService
from pickup.application.driver_session_service import DriverWorkSessionService
from pickup.application.handover_verification_service import (
    CourierHandoverVerificationService,
    VerificationPolicy,
)
from pickup.application.hub_handover_service import HubHandoverService
from pickup.application.offline_dispatch import OfflineOperationDispatcher
from pickup.application.offline_sync_service import OfflinePolicy, OfflineSyncService
from pickup.application.stop_service import PickupStopService
from pickup.application.task_lifecycle_service import PickupTaskLifecycleService
from pickup.config import PickupSettings
from pickup.ports.repository import RecoveryUnitOfWork


@dataclass(frozen=True, slots=True)
class DriverServices:
    """Composed driver, handover, and offline services for one unit of work."""

    work_session_service: DriverWorkSessionService
    task_lifecycle_service: PickupTaskLifecycleService
    stop_service: PickupStopService
    acceptance_service: PickupAcceptanceService | None
    verification_service: CourierHandoverVerificationService | None
    hub_handover_service: HubHandoverService
    offline_sync_service: OfflineSyncService | None


def build_driver_services(
    settings: PickupSettings,
    unit_of_work: RecoveryUnitOfWork | None,
) -> DriverServices | None:
    """Compose driver features; handover and offline need a configured signing key."""
    if unit_of_work is None:
        return None

    work_session_service = DriverWorkSessionService(unit_of_work)  # type: ignore[arg-type]
    task_lifecycle_service = PickupTaskLifecycleService(unit_of_work)  # type: ignore[arg-type]
    stop_service = PickupStopService(unit_of_work)  # type: ignore[arg-type]
    hub_handover_service = HubHandoverService(unit_of_work)  # type: ignore[arg-type]

    verification_service: CourierHandoverVerificationService | None = None
    offline_sync_service: OfflineSyncService | None = None
    if settings.driver_features_enabled:
        assert settings.signing_key is not None
        verification_service = CourierHandoverVerificationService(
            unit_of_work,  # type: ignore[arg-type]
            signing_key=settings.signing_key,
            policy=VerificationPolicy(
                challenge_ttl_seconds=settings.courier_challenge_ttl_seconds,
                verification_valid_seconds=settings.courier_verification_valid_seconds,
                confirmation_valid_seconds=settings.courier_confirmation_valid_seconds,
                max_failed_attempts=settings.courier_max_failed_attempts,
                lockout_seconds=settings.courier_lockout_seconds,
            ),
        )
        offline_sync_service = OfflineSyncService(
            unit_of_work,  # type: ignore[arg-type]
            OfflineOperationDispatcher(task_lifecycle_service.for_replay()),
            signing_key=settings.signing_key,
            policy=OfflinePolicy(
                authorization_ttl_minutes=settings.offline_authorization_ttl_minutes,
                sync_grace_days=settings.offline_sync_grace_days,
                max_sync_events=settings.offline_sync_max_events,
            ),
        )

    # Acceptance is gated on the sender ceremony whenever verification is required.
    # If verification is required but the gate could not be composed (no signing key),
    # acceptance stays closed rather than silently degrading to a driver-unilateral
    # custody start.
    acceptance_service: PickupAcceptanceService | None
    if settings.require_courier_verification:
        acceptance_service = (
            PickupAcceptanceService(
                unit_of_work,  # type: ignore[arg-type]
                verification_gate=verification_service,
            )
            if verification_service is not None
            else None
        )
    else:
        acceptance_service = PickupAcceptanceService(unit_of_work)  # type: ignore[arg-type]
    return DriverServices(
        work_session_service=work_session_service,
        task_lifecycle_service=task_lifecycle_service,
        stop_service=stop_service,
        acceptance_service=acceptance_service,
        verification_service=verification_service,
        hub_handover_service=hub_handover_service,
        offline_sync_service=offline_sync_service,
    )
