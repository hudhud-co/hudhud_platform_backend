"""Customer aggregates: profile, legal acceptance, contacts and addresses."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from customer.domain.value_objects import (
    AddressKind,
    GeoPoint,
    LegalDocumentKind,
    NotificationChannel,
    ProfileCompletionState,
)


@dataclass(slots=True)
class CustomerProfile:
    """Profile data for one principal.

    ``principal_id`` is the identity the Identity service owns. This service stores no
    credential and never authenticates anyone.
    """

    principal_id: UUID
    display_name: str | None
    created_at: datetime
    updated_at: datetime | None = None
    notification_channels: frozenset[NotificationChannel] = field(
        default_factory=lambda: frozenset(
            {NotificationChannel.APP, NotificationChannel.SMS}
        )
    )
    version: int = 1

    @property
    def completion_state(self) -> ProfileCompletionState:
        if self.display_name and self.display_name.strip():
            return ProfileCompletionState.COMPLETE
        return ProfileCompletionState.INCOMPLETE


@dataclass(slots=True)
class LegalAcceptance:
    """A record that one principal accepted one version of one document.

    Acceptance is append-only and version-scoped: publishing a new version of the terms
    must require a fresh acceptance rather than silently inheriting the old one.
    """

    acceptance_id: UUID
    principal_id: UUID
    kind: LegalDocumentKind
    document_version: str
    accepted_at: datetime


@dataclass(slots=True)
class Contact:
    """A saved receiver. Only phone and governorate are required (v6.3 p.12)."""

    contact_id: UUID
    owner_principal_id: UUID
    phone: str
    governorate: str
    display_name: str | None = None
    created_at: datetime | None = None
    archived_at: datetime | None = None
    version: int = 1

    @property
    def is_active(self) -> bool:
        return self.archived_at is None


@dataclass(slots=True)
class Address:
    """One address in the book.

    ``is_default`` is enforced as at-most-one per (owner, kind) by a partial unique index,
    so a customer can never end up with two defaults and an ambiguous pickup point.
    """

    address_id: UUID
    owner_principal_id: UUID
    kind: AddressKind
    governorate: str
    line: str
    contact_id: UUID | None = None
    landmark: str | None = None
    geo: GeoPoint | None = None
    is_default: bool = False
    created_at: datetime | None = None
    archived_at: datetime | None = None
    version: int = 1

    @property
    def is_active(self) -> bool:
        return self.archived_at is None
