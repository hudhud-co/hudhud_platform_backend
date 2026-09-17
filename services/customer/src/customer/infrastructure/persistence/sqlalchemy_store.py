"""PostgreSQL unit of work for the Customer service."""

from __future__ import annotations

from contextvars import ContextVar
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from customer.domain.entities import Address, Contact, CustomerProfile, LegalAcceptance
from customer.domain.value_objects import (
    AddressKind,
    GeoPoint,
    LegalDocumentKind,
    NotificationChannel,
)
from customer.infrastructure.persistence.models import (
    AddressRow,
    ContactRow,
    CustomerProfileRow,
    LegalAcceptanceRow,
)


class StaleCustomerRecord(RuntimeError):
    """A concurrent writer changed the row since it was read."""


class SqlAlchemyCustomerUnitOfWork:
    """One instance serves every request, so its transaction is request-scoped.

    ``create_app`` builds this once and puts it on ``app.state``. Holding the
    open session on ``self`` meant two requests in flight shared it, and the
    second ``begin()`` raised ``transaction already active`` — a 500 for any two
    simultaneous users. ``_session`` and ``_pending`` are therefore properties
    over :class:`~contextvars.ContextVar`, which FastAPI gives a fresh copy of
    per request, so every existing call site keeps working unchanged.
    """

    def __init__(self, *, session_factory: sessionmaker[SaSession]) -> None:
        self._session_factory = session_factory
        self._session_var: ContextVar[SaSession | None] = ContextVar(
            "customer_session", default=None
        )
        self._pending_var: ContextVar[dict[str, dict] | None] = ContextVar(
            "customer_pending", default=None
        )
        self._profiles = _ProfileRepo(self)
        self._legal = _LegalRepo(self)
        self._contacts = _ContactRepo(self)
        self._addresses = _AddressRepo(self)

    # --------------------------------------------------- request-scoped state

    @property
    def _session(self) -> SaSession | None:
        return self._session_var.get()

    @_session.setter
    def _session(self, value: SaSession | None) -> None:
        self._session_var.set(value)

    @property
    def _pending(self) -> dict[str, dict] | None:
        return self._pending_var.get()

    @_pending.setter
    def _pending(self, value: dict[str, dict] | None) -> None:
        self._pending_var.set(value)

    def begin(self) -> None:
        if self._session is not None:
            msg = "transaction already active"
            raise RuntimeError(msg)
        self._session = self._session_factory()
        self._pending = {"profiles": {}, "legal": {}, "contacts": {}, "addresses": {}}

    def commit(self) -> None:
        if self._session is None or self._pending is None:
            msg = "commit without transaction"
            raise RuntimeError(msg)
        session = self._session
        try:
            for entity, previous in self._pending["profiles"].values():
                _versioned(
                    session,
                    CustomerProfileRow,
                    CustomerProfileRow.principal_id == entity.principal_id,
                    previous=previous,
                    values=_profile_values(entity),
                    new_row=lambda e=entity: CustomerProfileRow(
                        principal_id=e.principal_id, **_profile_values(e)
                    ),
                )
            for entity in self._pending["legal"].values():
                session.add(
                    LegalAcceptanceRow(
                        acceptance_id=entity.acceptance_id,
                        principal_id=entity.principal_id,
                        kind=entity.kind.value,
                        document_version=entity.document_version,
                        accepted_at=entity.accepted_at,
                    )
                )
            for entity, previous in self._pending["contacts"].values():
                _versioned(
                    session,
                    ContactRow,
                    ContactRow.contact_id == entity.contact_id,
                    previous=previous,
                    values=_contact_values(entity),
                    new_row=lambda e=entity: ContactRow(
                        contact_id=e.contact_id, **_contact_values(e)
                    ),
                )
            for entity, previous in self._pending["addresses"].values():
                _versioned(
                    session,
                    AddressRow,
                    AddressRow.address_id == entity.address_id,
                    previous=previous,
                    values=_address_values(entity),
                    new_row=lambda e=entity: AddressRow(
                        address_id=e.address_id, **_address_values(e)
                    ),
                )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            self._session = None
            self._pending = None

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()
            self._session.close()
        self._session = None
        self._pending = None

    def _require(self) -> SaSession:
        if self._session is None:
            msg = "no active transaction"
            raise RuntimeError(msg)
        return self._session

    @property
    def profiles(self) -> _ProfileRepo:
        return self._profiles

    @property
    def legal_acceptances(self) -> _LegalRepo:
        return self._legal

    @property
    def contacts(self) -> _ContactRepo:
        return self._contacts

    @property
    def addresses(self) -> _AddressRepo:
        return self._addresses


def _versioned(session, row_cls, where, *, previous, values, new_row) -> None:
    if previous is None:
        session.add(new_row())
        return
    rowcount = session.execute(
        update(row_cls).where(where, row_cls.version == previous).values(**values)
    ).rowcount
    if rowcount == 0:
        raise StaleCustomerRecord(f"{row_cls.__tablename__} changed concurrently")


def _stage(pending: dict, key, entity, version: int) -> None:
    previous = pending[key][1] if key in pending else None
    if previous is None and version > 1:
        previous = version - 1
    pending[key] = (entity, previous)


class _ProfileRepo:
    def __init__(self, uow: SqlAlchemyCustomerUnitOfWork) -> None:
        self._uow = uow

    def save(self, profile: CustomerProfile) -> None:
        _stage(self._uow._pending["profiles"], profile.principal_id, profile, profile.version)

    def get(self, principal_id: UUID) -> CustomerProfile | None:
        row = self._uow._require().get(CustomerProfileRow, principal_id)
        return _profile_entity(row) if row is not None else None


class _LegalRepo:
    def __init__(self, uow: SqlAlchemyCustomerUnitOfWork) -> None:
        self._uow = uow

    def save(self, acceptance: LegalAcceptance) -> None:
        self._uow._pending["legal"][acceptance.acceptance_id] = acceptance

    def list_for_principal(self, principal_id: UUID) -> tuple[LegalAcceptance, ...]:
        rows = self._uow._require().execute(
            select(LegalAcceptanceRow).where(
                LegalAcceptanceRow.principal_id == principal_id
            )
        ).scalars().all()
        staged = [
            a
            for a in self._uow._pending["legal"].values()
            if a.principal_id == principal_id
        ]
        return tuple([_legal_entity(r) for r in rows] + staged)

    def find(
        self, principal_id: UUID, kind: str, document_version: str
    ) -> LegalAcceptance | None:
        row = self._uow._require().execute(
            select(LegalAcceptanceRow).where(
                LegalAcceptanceRow.principal_id == principal_id,
                LegalAcceptanceRow.kind == kind,
                LegalAcceptanceRow.document_version == document_version,
            )
        ).scalar_one_or_none()
        if row is not None:
            return _legal_entity(row)
        for staged in self._uow._pending["legal"].values():
            if (
                staged.principal_id == principal_id
                and staged.kind.value == kind
                and staged.document_version == document_version
            ):
                return staged
        return None


class _ContactRepo:
    def __init__(self, uow: SqlAlchemyCustomerUnitOfWork) -> None:
        self._uow = uow

    def save(self, contact: Contact) -> None:
        _stage(self._uow._pending["contacts"], contact.contact_id, contact, contact.version)

    def get(self, contact_id: UUID) -> Contact | None:
        staged = self._uow._pending["contacts"].get(contact_id)
        if staged is not None:
            return staged[0]
        row = self._uow._require().get(ContactRow, contact_id)
        return _contact_entity(row) if row is not None else None

    def list_for_owner(self, owner_principal_id: UUID) -> tuple[Contact, ...]:
        rows = self._uow._require().execute(
            select(ContactRow).where(ContactRow.owner_principal_id == owner_principal_id)
        ).scalars().all()
        return tuple(_contact_entity(row) for row in rows)


class _AddressRepo:
    def __init__(self, uow: SqlAlchemyCustomerUnitOfWork) -> None:
        self._uow = uow

    def save(self, address: Address) -> None:
        _stage(self._uow._pending["addresses"], address.address_id, address, address.version)

    def get(self, address_id: UUID) -> Address | None:
        staged = self._uow._pending["addresses"].get(address_id)
        if staged is not None:
            return staged[0]
        row = self._uow._require().get(AddressRow, address_id)
        return _address_entity(row) if row is not None else None

    def list_for_owner(
        self, owner_principal_id: UUID, kind: AddressKind | None = None
    ) -> tuple[Address, ...]:
        stmt = select(AddressRow).where(
            AddressRow.owner_principal_id == owner_principal_id
        )
        if kind is not None:
            stmt = stmt.where(AddressRow.kind == kind.value)
        rows = self._uow._require().execute(stmt).scalars().all()
        return tuple(_address_entity(row) for row in rows)

    def find_default(
        self, owner_principal_id: UUID, kind: AddressKind
    ) -> Address | None:
        row = self._uow._require().execute(
            select(AddressRow).where(
                AddressRow.owner_principal_id == owner_principal_id,
                AddressRow.kind == kind.value,
                AddressRow.is_default.is_(True),
                AddressRow.archived_at.is_(None),
            )
        ).scalars().first()
        return _address_entity(row) if row is not None else None


# ------------------------------------------------------------------ mappers


def _profile_values(e: CustomerProfile) -> dict[str, object]:
    return {
        "display_name": e.display_name,
        "notification_channels": ",".join(sorted(c.value for c in e.notification_channels)),
        "created_at": e.created_at,
        "updated_at": e.updated_at,
        "version": e.version,
    }


def _profile_entity(row: CustomerProfileRow) -> CustomerProfile:
    channels = frozenset(
        NotificationChannel(part)
        for part in (row.notification_channels or "").split(",")
        if part
    )
    return CustomerProfile(
        principal_id=row.principal_id,  # type: ignore[arg-type]
        display_name=row.display_name,
        created_at=row.created_at,  # type: ignore[arg-type]
        updated_at=row.updated_at,  # type: ignore[arg-type]
        notification_channels=channels,
        version=row.version,
    )


def _legal_entity(row: LegalAcceptanceRow) -> LegalAcceptance:
    return LegalAcceptance(
        acceptance_id=row.acceptance_id,  # type: ignore[arg-type]
        principal_id=row.principal_id,  # type: ignore[arg-type]
        kind=LegalDocumentKind(row.kind),
        document_version=row.document_version,
        accepted_at=row.accepted_at,  # type: ignore[arg-type]
    )


def _contact_values(e: Contact) -> dict[str, object]:
    return {
        "owner_principal_id": e.owner_principal_id,
        "phone": e.phone,
        "governorate": e.governorate,
        "display_name": e.display_name,
        "created_at": e.created_at,
        "archived_at": e.archived_at,
        "version": e.version,
    }


def _contact_entity(row: ContactRow) -> Contact:
    return Contact(
        contact_id=row.contact_id,  # type: ignore[arg-type]
        owner_principal_id=row.owner_principal_id,  # type: ignore[arg-type]
        phone=row.phone,
        governorate=row.governorate,
        display_name=row.display_name,
        created_at=row.created_at,  # type: ignore[arg-type]
        archived_at=row.archived_at,  # type: ignore[arg-type]
        version=row.version,
    )


def _address_values(e: Address) -> dict[str, object]:
    return {
        "owner_principal_id": e.owner_principal_id,
        "kind": e.kind.value,
        "governorate": e.governorate,
        "line": e.line,
        "contact_id": e.contact_id,
        "landmark": e.landmark,
        "latitude": e.geo.latitude if e.geo else None,
        "longitude": e.geo.longitude if e.geo else None,
        "is_default": e.is_default,
        "created_at": e.created_at,
        "archived_at": e.archived_at,
        "version": e.version,
    }


def _address_entity(row: AddressRow) -> Address:
    geo = None
    if row.latitude is not None and row.longitude is not None:
        geo = GeoPoint(
            latitude=Decimal(str(row.latitude)), longitude=Decimal(str(row.longitude))
        )
    return Address(
        address_id=row.address_id,  # type: ignore[arg-type]
        owner_principal_id=row.owner_principal_id,  # type: ignore[arg-type]
        kind=AddressKind(row.kind),
        governorate=row.governorate,
        line=row.line,
        contact_id=row.contact_id,  # type: ignore[arg-type]
        landmark=row.landmark,
        geo=geo,
        is_default=bool(row.is_default),
        created_at=row.created_at,  # type: ignore[arg-type]
        archived_at=row.archived_at,  # type: ignore[arg-type]
        version=row.version,
    )
