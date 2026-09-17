"""Shared builders for driver, handover, and offline tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pickup.application.driver_session_service import (
    DriverWorkSessionService,
    StartWorkSessionCommand,
)
from pickup.application.handover_verification_service import (
    CourierHandoverVerificationService,
    VerificationPolicy,
)
from pickup.application.hub_handover_service import HubHandoverService
from pickup.application.offline_dispatch import OfflineOperationDispatcher
from pickup.application.offline_sync_service import OfflinePolicy, OfflineSyncService
from pickup.application.recovery_service import (
    PickupRecoveryService,
    RegisterPickupTaskCommand,
)
from pickup.application.task_lifecycle_service import PickupTaskLifecycleService
from pickup.domain.entities import PickupTask
from pickup.domain.value_objects import AssignmentState, PickupTaskStatus
from pickup.infrastructure.fake_shipment_eligibility import (
    InMemoryShipmentEligibilityAdapter,
)
from pickup.infrastructure.memory import InMemoryPickupUnitOfWork
from pickup.ports.authorization import PickupActor, PickupRole

SIGNING_KEY = "unit-test-signing-key-value-32-chars"
DRIVER_ID = "driver-42"
BASE_TIME = datetime(2026, 9, 14, 9, 0, tzinfo=UTC)


def driver_actor(actor_id: str = DRIVER_ID) -> PickupActor:
    return PickupActor(actor_id=actor_id, roles=frozenset({PickupRole.PICKUP_DRIVER}))


def merchant_actor(actor_id: str = "merchant-user-7") -> PickupActor:
    return PickupActor(
        actor_id=actor_id,
        roles=frozenset({PickupRole.MERCHANT_MEMBER}),
        merchant_ids=frozenset({uuid4()}),
    )


def hub_actor(hub_id: UUID, actor_id: str = "hub-operator-7") -> PickupActor:
    return PickupActor(
        actor_id=actor_id,
        roles=frozenset({PickupRole.HUB_OPERATOR}),
        hub_ids=frozenset({hub_id}),
    )


def operations_actor(actor_id: str = "ops-3") -> PickupActor:
    return PickupActor(actor_id=actor_id, roles=frozenset({PickupRole.OPERATIONS}))


def build_store() -> InMemoryPickupUnitOfWork:
    return InMemoryPickupUnitOfWork()


def work_session_service(store: InMemoryPickupUnitOfWork) -> DriverWorkSessionService:
    return DriverWorkSessionService(store)


def lifecycle_service(store: InMemoryPickupUnitOfWork) -> PickupTaskLifecycleService:
    return PickupTaskLifecycleService(store)


def verification_service(
    store: InMemoryPickupUnitOfWork,
    *,
    policy: VerificationPolicy | None = None,
) -> CourierHandoverVerificationService:
    return CourierHandoverVerificationService(
        store,
        signing_key=SIGNING_KEY,
        policy=policy or VerificationPolicy(),
    )


def hub_handover_service(store: InMemoryPickupUnitOfWork) -> HubHandoverService:
    return HubHandoverService(store)


def offline_service(
    store: InMemoryPickupUnitOfWork,
    *,
    policy: OfflinePolicy | None = None,
) -> OfflineSyncService:
    lifecycle = PickupTaskLifecycleService(store)
    return OfflineSyncService(
        store,
        OfflineOperationDispatcher(lifecycle.for_replay()),
        signing_key=SIGNING_KEY,
        policy=policy or OfflinePolicy(),
    )


def register_task(
    store: InMemoryPickupUnitOfWork,
    *,
    driver_user_id: str = DRIVER_ID,
    status: PickupTaskStatus = PickupTaskStatus.PENDING,
    assignment_state: AssignmentState = AssignmentState.OFFERED,
    has_condition_proof: bool = False,
    pickup_task_id: UUID | None = None,
    shipment_id: UUID | None = None,
    assigned_batch_id: UUID | None = None,
) -> PickupTask:
    service = PickupRecoveryService(store, InMemoryShipmentEligibilityAdapter())
    task = service.register_pickup_task(
        RegisterPickupTaskCommand(
            pickup_task_id=pickup_task_id or uuid4(),
            shipment_id=shipment_id or uuid4(),
            assigned_driver_user_id=driver_user_id,
            assigned_batch_id=assigned_batch_id or uuid4(),
            status=status,
            has_pickup_condition_proof=has_condition_proof,
            created_at=BASE_TIME,
            assignment_state=assignment_state,
        )
    )
    return task


def start_session(
    store: InMemoryPickupUnitOfWork,
    *,
    driver_user_id: str = DRIVER_ID,
    at: datetime | None = None,
):
    service = work_session_service(store)
    return service.start(
        StartWorkSessionCommand(
            driver_user_id=driver_user_id,
            occurred_at=at or BASE_TIME,
        ),
        actor=driver_actor(driver_user_id),
    )


def minutes(count: int) -> timedelta:
    return timedelta(minutes=count)
