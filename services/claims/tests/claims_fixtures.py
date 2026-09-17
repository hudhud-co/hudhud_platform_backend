"""Shared builders for the Claims test suite."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from claims.application.claim_service import ClaimService
from claims.application.incident_service import IncidentService
from claims.domain.money import Currency, Money
from claims.domain.value_objects import EvidenceMediaRef
from claims.infrastructure.memory import InMemoryUnitOfWork
from claims.ports.authorization import ClaimsActor, ClaimsRole


def iqd(minor_units: int) -> Money:
    return Money(minor_units=minor_units, currency=Currency.IQD)


def photo(key: str = "damage.jpg") -> EvidenceMediaRef:
    return EvidenceMediaRef(bucket="claims-evidence", key=key)


def actor(*roles: ClaimsRole, principal_id: UUID | None = None) -> ClaimsActor:
    return ClaimsActor(
        principal_id=principal_id or uuid4(), roles=frozenset(roles)
    )


def support() -> ClaimsActor:
    return actor(ClaimsRole.SUPPORT)


def operations() -> ClaimsActor:
    return actor(ClaimsRole.OPERATIONS)


def accountant() -> ClaimsActor:
    return actor(ClaimsRole.ACCOUNTANT)


def driver(principal_id: UUID | None = None) -> ClaimsActor:
    return actor(ClaimsRole.LAST_MILE_DRIVER, principal_id=principal_id)


def customer(principal_id: UUID | None = None) -> ClaimsActor:
    return actor(ClaimsRole.CUSTOMER, principal_id=principal_id)


@dataclass
class Lab:
    uow: InMemoryUnitOfWork
    claims: ClaimService
    incidents: IncidentService
    sender_id: UUID
    receiver_id: UUID
    driver_id: UUID


def build_lab() -> Lab:
    uow = InMemoryUnitOfWork()
    return Lab(
        uow=uow,
        claims=ClaimService(uow),
        incidents=IncidentService(uow),
        sender_id=uuid4(),
        receiver_id=uuid4(),
        driver_id=uuid4(),
    )


_SEQUENCE = {"n": 0}


def next_tracking_code() -> str:
    _SEQUENCE["n"] += 1
    return f"SHP-20260915-{_SEQUENCE['n']:06d}"
