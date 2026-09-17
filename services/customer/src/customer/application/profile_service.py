"""Customer profile, legal acceptance and notification preferences."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from customer.domain.entities import CustomerProfile, LegalAcceptance
from customer.domain.errors import (
    CustomerProfileNotFound,
    DisplayNameRequired,
    LegalDocumentNotAccepted,
)
from customer.domain.value_objects import (
    LegalDocumentKind,
    NotificationChannel,
    ProfileCompletionState,
)
from customer.ports.repository import CustomerUnitOfWork

MAX_DISPLAY_NAME = 120


@dataclass(frozen=True, slots=True)
class LegalPolicy:
    """The document versions currently in force.

    Held as configuration rather than code so publishing new terms is a deployment
    setting, not a release. Enforcement is version-scoped: accepting v1 does not accept v2.
    """

    terms_version: str = "1.0"
    privacy_version: str = "1.0"

    def required_version(self, kind: LegalDocumentKind) -> str:
        if kind is LegalDocumentKind.TERMS_OF_SERVICE:
            return self.terms_version
        return self.privacy_version


@dataclass(frozen=True, slots=True)
class UpsertProfileCommand:
    principal_id: UUID
    display_name: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class AcceptLegalCommand:
    principal_id: UUID
    kind: LegalDocumentKind | str
    document_version: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ProfileView:
    profile: CustomerProfile
    completion_state: ProfileCompletionState
    outstanding_documents: tuple[tuple[LegalDocumentKind, str], ...]
    idempotent_replay: bool = False

    @property
    def can_use_the_app(self) -> bool:
        """Profile complete and nothing left to accept."""
        return (
            self.completion_state is ProfileCompletionState.COMPLETE
            and not self.outstanding_documents
        )


class CustomerProfileService:
    def __init__(
        self, unit_of_work: CustomerUnitOfWork, *, legal_policy: LegalPolicy | None = None
    ) -> None:
        self._uow = unit_of_work
        self._legal = legal_policy or LegalPolicy()

    def ensure_profile(
        self, *, principal_id: UUID, occurred_at: datetime
    ) -> ProfileView:
        """Return the profile, creating an empty one on first contact.

        A principal that has just verified a code has no profile yet; that is the normal
        first-run state, so reading it must not be an error.
        """
        self._uow.begin()
        try:
            profile = self._uow.profiles.get(principal_id)
            created = profile is None
            if profile is None:
                profile = CustomerProfile(
                    principal_id=principal_id,
                    display_name=None,
                    created_at=occurred_at,
                )
                self._uow.profiles.save(profile)
            view = self._view(profile, replay=not created)
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        return view

    def set_display_name(self, command: UpsertProfileCommand) -> ProfileView:
        name = (command.display_name or "").strip()
        if not name:
            raise DisplayNameRequired()
        if len(name) > MAX_DISPLAY_NAME:
            name = name[:MAX_DISPLAY_NAME]

        self._uow.begin()
        try:
            profile = self._uow.profiles.get(command.principal_id)
            if profile is None:
                profile = CustomerProfile(
                    principal_id=command.principal_id,
                    display_name=name,
                    created_at=command.occurred_at,
                )
            else:
                if profile.display_name == name:
                    view = self._view(profile, replay=True)
                    self._uow.commit()
                    return view
                profile.display_name = name
                profile.updated_at = command.occurred_at
                profile.version += 1
            self._uow.profiles.save(profile)
            view = self._view(profile)
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        return view

    def set_notification_channels(
        self,
        *,
        principal_id: UUID,
        channels: frozenset[NotificationChannel],
        occurred_at: datetime,
    ) -> ProfileView:
        self._uow.begin()
        try:
            profile = self._uow.profiles.get(principal_id)
            if profile is None:
                raise CustomerProfileNotFound(str(principal_id))
            if profile.notification_channels == channels:
                view = self._view(profile, replay=True)
                self._uow.commit()
                return view
            profile.notification_channels = channels
            profile.updated_at = occurred_at
            profile.version += 1
            self._uow.profiles.save(profile)
            view = self._view(profile)
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        return view

    def accept_legal_document(self, command: AcceptLegalCommand) -> ProfileView:
        kind = _coerce_kind(command.kind)
        version = (command.document_version or "").strip()
        required = self._legal.required_version(kind)
        if version != required:
            # Accepting a version that is not in force must not satisfy the requirement,
            # or a stale client could accept an old document forever.
            raise LegalDocumentNotAccepted(kind=kind.value, required_version=required)

        self._uow.begin()
        try:
            profile = self._uow.profiles.get(command.principal_id)
            if profile is None:
                profile = CustomerProfile(
                    principal_id=command.principal_id,
                    display_name=None,
                    created_at=command.occurred_at,
                )
                self._uow.profiles.save(profile)
            existing = self._uow.legal_acceptances.find(
                command.principal_id, kind.value, version
            )
            replay = existing is not None
            if existing is None:
                self._uow.legal_acceptances.save(
                    LegalAcceptance(
                        acceptance_id=uuid4(),
                        principal_id=command.principal_id,
                        kind=kind,
                        document_version=version,
                        accepted_at=command.occurred_at,
                    )
                )
            view = self._view(profile, replay=replay)
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        return view

    def read_profile(self, principal_id: UUID) -> ProfileView:
        self._uow.begin()
        try:
            profile = self._uow.profiles.get(principal_id)
            if profile is None:
                raise CustomerProfileNotFound(str(principal_id))
            view = self._view(profile)
        finally:
            self._uow.rollback()
        return view

    def assert_legal_current(self, principal_id: UUID) -> None:
        """Raise when a document version in force has not been accepted."""
        outstanding = self._outstanding(principal_id)
        if outstanding:
            kind, version = outstanding[0]
            raise LegalDocumentNotAccepted(kind=kind.value, required_version=version)

    def _view(self, profile: CustomerProfile, *, replay: bool = False) -> ProfileView:
        return ProfileView(
            profile=profile,
            completion_state=profile.completion_state,
            outstanding_documents=self._outstanding(profile.principal_id),
            idempotent_replay=replay,
        )

    def _outstanding(
        self, principal_id: UUID
    ) -> tuple[tuple[LegalDocumentKind, str], ...]:
        accepted = {
            (a.kind, a.document_version)
            for a in self._uow.legal_acceptances.list_for_principal(principal_id)
        }
        missing = []
        for kind in LegalDocumentKind:
            required = self._legal.required_version(kind)
            if (kind, required) not in accepted:
                missing.append((kind, required))
        return tuple(missing)


def _coerce_kind(value: LegalDocumentKind | str) -> LegalDocumentKind:
    if isinstance(value, LegalDocumentKind):
        return value
    try:
        return LegalDocumentKind(str(value).strip().upper())
    except ValueError as exc:
        raise LegalDocumentNotAccepted(
            kind=str(value), required_version="unknown"
        ) from exc
