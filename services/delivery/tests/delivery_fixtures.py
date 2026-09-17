"""Shared builders for the Delivery test suite.

The code length is pinned to six *in tests only* and by explicit construction, never by
a default in the service. The point of the fixture is to prove the rest of the doorstep
works once the business decides; it is not a decision about DRV-L05.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from delivery.application.doorstep_service import DoorstepService, ParcelForManifest
from delivery.application.outcome_service import OutcomeService
from delivery.application.payment_service import PaymentService
from delivery.application.receiver_service import ReceiverService
from delivery.domain.delivery_code import DeliveryCodePolicy
from delivery.domain.entities import DeliveryStop, ParcelSettings
from delivery.domain.id_evidence import IdEvidencePolicy
from delivery.domain.money import Currency, Money
from delivery.domain.value_objects import InspectionOutcome, PaymentMethod
from delivery.infrastructure.memory import InMemoryUnitOfWork

#: A length chosen by the test, not by the service. See DRV-L05.
TEST_CODE_LENGTH = 6
TEST_CODE = "482913"
TEST_HMAC_KEY = "test-delivery-code-key"


def iqd(minor_units: int) -> Money:
    return Money(minor_units=minor_units, currency=Currency.IQD)


@dataclass
class Lab:
    """One driver, one open manifest and the four services wired to one unit of work."""

    uow: InMemoryUnitOfWork
    doorstep: DoorstepService
    payments: PaymentService
    outcomes: OutcomeService
    receiver: ReceiverService
    driver_id: UUID
    hub_id: UUID
    manifest_id: UUID


def build_lab(
    *,
    code_length: int | None = TEST_CODE_LENGTH,
    retention_decided: bool = False,
    driver_id: UUID | None = None,
) -> Lab:
    uow = InMemoryUnitOfWork()
    doorstep = DoorstepService(
        uow,
        code_policy=DeliveryCodePolicy(length=code_length),
        id_policy=IdEvidencePolicy(retention_decided=retention_decided),
        delivery_code_key=TEST_HMAC_KEY,
    )
    payments = PaymentService(uow)
    outcomes = OutcomeService(
        uow, payment_service=payments, doorstep_service=doorstep
    )
    receiver = ReceiverService(uow)
    driver = driver_id or uuid4()
    hub = uuid4()
    manifest = doorstep.open_manifest(driver_principal_id=driver, hub_id=hub)
    return Lab(
        uow=uow,
        doorstep=doorstep,
        payments=payments,
        outcomes=outcomes,
        receiver=receiver,
        driver_id=driver,
        hub_id=hub,
        manifest_id=manifest.manifest_id,
    )


_SEQUENCE = {"n": 0}


def next_tracking_code() -> str:
    _SEQUENCE["n"] += 1
    return f"SHP-20260915-{_SEQUENCE['n']:06d}"


def scan_parcel(
    lab: Lab,
    *,
    tracking_code: str | None = None,
    code: str | None = TEST_CODE,
    named_receiver: str | None = None,
    cod_amount: Money | None = None,
    payment_method_expected: PaymentMethod = PaymentMethod.PREPAID,
    open_box_allowed: bool = False,
    photo_documentation: bool = False,
    packaging_seal_code: str | None = None,
) -> DeliveryStop:
    return lab.doorstep.scan_onto_manifest(
        manifest_id=lab.manifest_id,
        parcel=ParcelForManifest(
            tracking_code=tracking_code or next_tracking_code(),
            settings=ParcelSettings(
                open_box_allowed=open_box_allowed,
                photo_documentation=photo_documentation,
                packaging_seal_code=packaging_seal_code,
            ),
            delivery_code=code,
            named_receiver=named_receiver,
            cod_amount=cod_amount,
            payment_method_expected=payment_method_expected,
        ),
    )


def drive_to_the_door(lab: Lab, stop: DeliveryStop) -> DeliveryStop:
    """Depart and arrive — everything before verification."""
    lab.outcomes.announce_departure(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    return lab.doorstep.record_arrival(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )


def authorize_at_the_door(lab: Lab, stop: DeliveryStop) -> DeliveryStop:
    """Arrive, verify with the code, and record a sealed acceptance."""
    drive_to_the_door(lab, stop)
    result = lab.doorstep.verify_with_code(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id, code=TEST_CODE
    )
    return lab.doorstep.record_inspection(
        stop_id=result.stop.stop_id,
        driver_principal_id=lab.driver_id,
        outcome=InspectionOutcome.SEALED_ACCEPTED,
    )
