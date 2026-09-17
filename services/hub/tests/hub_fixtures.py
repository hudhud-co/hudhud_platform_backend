"""Shared builders for Hub tests."""

from __future__ import annotations

from datetime import UTC, datetime, time
from uuid import UUID, uuid4

from hub.application.drop_off_service import DropOffService
from hub.application.hub_admin_service import HubAdminService
from hub.application.inbox_service import InboxService
from hub.application.linehaul_service import LinehaulService
from hub.application.processing_service import HubProcessingService
from hub.domain.entities import Hub
from hub.infrastructure.memory import InMemoryHubUnitOfWork
from hub.ports.authorization import HubActor, HubRole

OPERATOR = UUID("11111111-1111-4111-8111-111111111111")
OPERATIONS = UUID("22222222-2222-4222-8222-222222222222")
LINEHAUL_DRIVER = UUID("33333333-3333-4333-8333-333333333333")
CUSTOMER = UUID("44444444-4444-4444-8444-444444444444")

TRACKING = "SHP-20260914-000101"
OTHER_TRACKING = "SHP-20260914-000202"


def build_store() -> InMemoryHubUnitOfWork:
    return InMemoryHubUnitOfWork()


def admin_service(uow: InMemoryHubUnitOfWork) -> HubAdminService:
    return HubAdminService(uow)


def drop_off_service(uow: InMemoryHubUnitOfWork, **kwargs) -> DropOffService:
    return DropOffService(uow, **kwargs)


def processing_service(uow: InMemoryHubUnitOfWork) -> HubProcessingService:
    return HubProcessingService(uow)


def linehaul_service(uow: InMemoryHubUnitOfWork) -> LinehaulService:
    return LinehaulService(uow)


def inbox_service(uow: InMemoryHubUnitOfWork, **kwargs) -> InboxService:
    return InboxService(uow, **kwargs)


def karbala_hub(uow: InMemoryHubUnitOfWork, *, cut_off: time = time(18, 0)) -> Hub:
    return admin_service(uow).register_hub(
        code="KRB",
        name="Karbala Hub",
        governorate="KARBALA",
        cut_off_local_time=cut_off,
    )


def baghdad_hub(uow: InMemoryHubUnitOfWork, *, cut_off: time = time(20, 30)) -> Hub:
    return admin_service(uow).register_hub(
        code="BGW",
        name="Baghdad Hub",
        governorate="BAGHDAD",
        cut_off_local_time=cut_off,
    )


def walk_in_details(**overrides) -> dict[str, str]:
    return {
        "receiver_phone": "+9647701234567",
        "destination_governorate": "BAGHDAD",
        **overrides,
    }


def accepted_drop_off(uow: InMemoryHubUnitOfWork, hub: Hub, **kwargs):
    """Walk the real drop-off path — never hand-build an accepted parcel."""
    service = drop_off_service(uow, **kwargs)
    drop_off = service.capture_details(
        hub_id=hub.hub_id,
        tracking_code=TRACKING,
        details=walk_in_details(),
    )
    service.apply_label(
        drop_off_id=drop_off.drop_off_id,
        label_code="HH-000001",
        weight_grams=1400,
        operator_principal_id=OPERATOR,
        actor_is_hub_staff=True,
    )
    return service.accept(
        drop_off_id=drop_off.drop_off_id,
        operator_principal_id=OPERATOR,
        destination_governorate="BAGHDAD",
    )


def sorted_parcel(
    uow: InMemoryHubUnitOfWork,
    hub: Hub,
    *,
    tracking_code: str = TRACKING,
    destination: str = "BAGHDAD",
):
    service = processing_service(uow)
    service.scan_in(
        hub_id=hub.hub_id,
        tracking_code=tracking_code,
        destination_governorate=destination,
    )
    return service.sort(hub_id=hub.hub_id, tracking_code=tracking_code)


def at(hour: int = 19, minute: int = 0, day: int = 14) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=UTC)


def operator_actor(hub_ids: frozenset[UUID] | None = None) -> HubActor:
    return HubActor(
        principal_id=OPERATOR,
        roles=frozenset({HubRole.HUB_OPERATOR}),
        hub_ids=hub_ids or frozenset(),
    )


def operations_actor() -> HubActor:
    return HubActor(principal_id=OPERATIONS, roles=frozenset({HubRole.OPERATIONS}))


def customer_actor() -> HubActor:
    return HubActor(principal_id=CUSTOMER, roles=frozenset())


def new_id() -> UUID:
    return uuid4()
