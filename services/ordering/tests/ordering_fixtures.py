"""Shared builders for Ordering tests."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from ordering.application.amendment_service import AmendmentService
from ordering.application.catalogue_service import GoodsCatalogueService
from ordering.application.inbox_service import InboxService
from ordering.application.pricing_service import PricingService, ServiceabilityPolicy
from ordering.application.public_tracking_service import PublicTrackingService
from ordering.application.send_service import (
    ReceiverDraft,
    SendService,
    ShipmentDraft,
)
from ordering.domain.entities import Order, SenderRef, ShipmentRequest
from ordering.domain.money import Money
from ordering.domain.value_objects import PaymentTerms, SenderKind
from ordering.infrastructure.memory import InMemoryOrderingUnitOfWork

CUSTOMER = UUID("11111111-1111-4111-8111-111111111111")
MERCHANT_OWNER = UUID("22222222-2222-4222-8222-222222222222")
MERCHANT_ID = UUID("33333333-3333-4333-8333-333333333333")
STORE_ID = UUID("44444444-4444-4444-8444-444444444444")
COURIER = UUID("55555555-5555-4555-8555-555555555555")

SERVICEABLE = frozenset({"BAGHDAD", "KARBALA", "NAJAF", "BASRA", "ERBIL"})


def build_store() -> InMemoryOrderingUnitOfWork:
    return InMemoryOrderingUnitOfWork()


def send_service(uow: InMemoryOrderingUnitOfWork, **kwargs) -> SendService:
    kwargs.setdefault("require_prohibited_acknowledgement", False)
    return SendService(uow, **kwargs)


def amendment_service(uow: InMemoryOrderingUnitOfWork) -> AmendmentService:
    return AmendmentService(uow)


def pricing_service(
    uow: InMemoryOrderingUnitOfWork, *, serviceable: frozenset[str] = SERVICEABLE
) -> PricingService:
    return PricingService(
        uow, serviceability=ServiceabilityPolicy(serviceable_governorates=serviceable)
    )


def catalogue_service(uow: InMemoryOrderingUnitOfWork) -> GoodsCatalogueService:
    return GoodsCatalogueService(uow)


def tracking_service(uow: InMemoryOrderingUnitOfWork) -> PublicTrackingService:
    return PublicTrackingService(uow)


def inbox_service(uow: InMemoryOrderingUnitOfWork, **kwargs) -> InboxService:
    return InboxService(uow, **kwargs)


def customer_sender(principal_id: UUID = CUSTOMER) -> SenderRef:
    return SenderRef(kind=SenderKind.CUSTOMER, principal_id=principal_id)


def merchant_sender(
    principal_id: UUID = MERCHANT_OWNER, merchant_id: UUID = MERCHANT_ID
) -> SenderRef:
    return SenderRef(
        kind=SenderKind.MERCHANT,
        principal_id=principal_id,
        merchant_id=merchant_id,
        store_id=STORE_ID,
    )


def receiver(governorate: str = "BAGHDAD", **kwargs) -> ReceiverDraft:
    return ReceiverDraft(
        phone=kwargs.pop("phone", "+9647701234567"),
        governorate=governorate,
        **kwargs,
    )


def draft(**kwargs) -> ShipmentDraft:
    kwargs.setdefault("receiver", receiver())
    kwargs.setdefault("description", "Cotton bedsheet set, 4 pieces")
    return ShipmentDraft(**kwargs)


def cod_draft(amount: int = 450_000, **kwargs) -> ShipmentDraft:
    kwargs.setdefault("payment_terms", PaymentTerms.CASH_ON_DELIVERY)
    kwargs.setdefault("cod_amount", Money(amount))
    return draft(**kwargs)


def merchant_order(uow: InMemoryOrderingUnitOfWork) -> Order:
    return send_service(uow).open_order(sender=merchant_sender())


def customer_order(uow: InMemoryOrderingUnitOfWork) -> Order:
    return send_service(uow).open_order(sender=customer_sender())


def labelled_merchant_shipment(
    uow: InMemoryOrderingUnitOfWork, *, label: str = "HH-000001", **draft_kwargs
) -> ShipmentRequest:
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())
    request = service.add_shipment(order_id=order.order_id, draft=draft(**draft_kwargs))
    return service.link_label(request_id=request.request_id, label_code=label)


def at(hour: int = 9, day: int = 14) -> datetime:
    return datetime(2026, 9, day, hour, tzinfo=UTC)


def new_id() -> UUID:
    return uuid4()
