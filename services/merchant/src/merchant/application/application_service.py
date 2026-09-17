"""Merchant-status application service (MER-01, MER-02).

Mirrors v6.3 Figure 2 exactly: a regular customer requests merchant status, Hudhud
reviews, and the outcome is either an upgrade to merchant — "now receives pre-printed
labels" — or the applicant stays a regular customer and may re-apply later.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from merchant.domain.entities import (
    Merchant,
    MerchantApplication,
    StandingShipmentPolicy,
)
from merchant.domain.errors import (
    ApplicationAlreadyOpen,
    ApplicationAttributesMissing,
    ApplicationDataSetNotDefined,
    ApplicationNotFound,
    ApplicationTransitionNotAllowed,
    DecisionReasonRequired,
    MerchantCodeAlreadyIssued,
)
from merchant.domain.messaging import OutboxRecord, OutboxStatus
from merchant.domain.value_objects import (
    ApplicationStatus,
    MerchantStatus,
    validate_merchant_code,
)
from merchant.infrastructure.contracts.envelopes import (
    build_application_decided_envelope,
)
from merchant.ports.repository import MerchantUnitOfWork

_MERCHANT_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_MERCHANT_CODE_LENGTH = 8
_MAX_CODE_ATTEMPTS = 12


@dataclass(frozen=True, slots=True)
class ApplicationPolicy:
    """Which attributes an application must carry before it may be submitted.

    Empty is the shipped state and means the decision has not been made (MER-02).
    """

    required_attributes: tuple[str, ...] = ()

    @property
    def submission_enabled(self) -> bool:
        return bool(self.required_attributes)


@dataclass(frozen=True, slots=True)
class ApprovalResult:
    application: MerchantApplication
    merchant: Merchant
    policy: StandingShipmentPolicy
    event_id: UUID


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _generate_reference(moment: datetime) -> str:
    """Human-quotable application id — Customer App v3 shows one on ``storeSubmitted``."""
    suffix = "".join(secrets.choice(_MERCHANT_CODE_ALPHABET) for _ in range(6))
    return f"MAP-{moment:%Y%m%d}-{suffix}"


class MerchantApplicationService:
    def __init__(
        self,
        unit_of_work: MerchantUnitOfWork,
        *,
        policy: ApplicationPolicy | None = None,
        outbox_max_attempts: int = 5,
    ) -> None:
        self._uow = unit_of_work
        self._policy = policy or ApplicationPolicy()
        self._outbox_max_attempts = outbox_max_attempts

    # ------------------------------------------------------------- applicant

    def start_application(
        self, *, applicant_principal_id: UUID, attributes: dict[str, Any] | None = None
    ) -> MerchantApplication:
        """Open a draft. Refuses a second one — "One application at a time"."""
        self._uow.begin()
        try:
            existing = self._uow.applications.find_open_for_applicant(
                applicant_principal_id
            )
            if existing is not None:
                raise ApplicationAlreadyOpen(str(existing.application_id))
            moment = _now()
            application = MerchantApplication(
                application_id=uuid4(),
                applicant_principal_id=applicant_principal_id,
                reference=_generate_reference(moment),
                status=ApplicationStatus.DRAFT,
                attributes=dict(attributes or {}),
                created_at=moment,
            )
            self._uow.applications.save(application)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return application

    def update_attributes(
        self, *, application_id: UUID, attributes: dict[str, Any]
    ) -> MerchantApplication:
        """Save whatever the applicant has typed so far.

        Drafting is always allowed, even while MER-02 is unresolved: keeping answers is
        what makes "Edit and resubmit" work after a changes-requested decision.
        """
        self._uow.begin()
        try:
            application = self._load(application_id)
            if application.status not in {
                ApplicationStatus.DRAFT,
                ApplicationStatus.CHANGES_REQUESTED,
            }:
                raise ApplicationTransitionNotAllowed(
                    application.status.value, "edited"
                )
            application.attributes = {**application.attributes, **attributes}
            application.version += 1
            self._uow.applications.save(application)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return application

    def submit(self, *, application_id: UUID) -> MerchantApplication:
        """Submit for review.

        This is the single operation MER-02 blocks. It refuses while the required-field
        list is unconfigured, and refuses again if the applicant has not supplied every
        configured field.
        """
        if not self._policy.submission_enabled:
            raise ApplicationDataSetNotDefined()

        self._uow.begin()
        try:
            application = self._load(application_id)
            if not application.can_transition_to(ApplicationStatus.SUBMITTED):
                raise ApplicationTransitionNotAllowed(
                    application.status.value, ApplicationStatus.SUBMITTED.value
                )
            missing = tuple(
                name
                for name in self._policy.required_attributes
                if not str(application.attributes.get(name, "")).strip()
            )
            if missing:
                raise ApplicationAttributesMissing(missing)

            application.status = ApplicationStatus.SUBMITTED
            application.submitted_at = _now()
            application.decision_reason = None
            application.version += 1
            self._uow.applications.save(application)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return application

    def withdraw(self, *, application_id: UUID) -> MerchantApplication:
        self._uow.begin()
        try:
            application = self._load(application_id)
            if not application.can_transition_to(ApplicationStatus.WITHDRAWN):
                raise ApplicationTransitionNotAllowed(
                    application.status.value, ApplicationStatus.WITHDRAWN.value
                )
            application.status = ApplicationStatus.WITHDRAWN
            application.decided_at = _now()
            application.version += 1
            self._uow.applications.save(application)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return application

    # ------------------------------------------------------------- reviewer

    def request_changes(
        self, *, application_id: UUID, reviewer_principal_id: UUID, reason: str
    ) -> MerchantApplication:
        """Decline for now, with a reason the applicant can act on.

        This is not a final rejection: v6.3 p.9 keeps the applicant a regular customer who
        "may re-apply later", and the app keeps their answers for an edit-and-resubmit.
        """
        if not reason.strip():
            raise DecisionReasonRequired()

        self._uow.begin()
        try:
            application = self._load(application_id)
            if not application.can_transition_to(ApplicationStatus.CHANGES_REQUESTED):
                raise ApplicationTransitionNotAllowed(
                    application.status.value, ApplicationStatus.CHANGES_REQUESTED.value
                )
            moment = _now()
            application.status = ApplicationStatus.CHANGES_REQUESTED
            application.decided_at = moment
            application.decided_by_principal_id = reviewer_principal_id
            application.decision_reason = reason.strip()
            application.version += 1
            self._uow.applications.save(application)
            self._publish_decision(application)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return application

    def approve(
        self,
        *,
        application_id: UUID,
        reviewer_principal_id: UUID,
        display_name: str,
        merchant_code: str | None = None,
    ) -> ApprovalResult:
        """Approve and create the merchant, its standing policy and its merchant code.

        Approval is the only way a Merchant row comes into existence, which keeps
        "upgraded to merchant" a single auditable transition rather than two.
        """
        self._uow.begin()
        try:
            application = self._load(application_id)
            if not application.can_transition_to(ApplicationStatus.APPROVED):
                raise ApplicationTransitionNotAllowed(
                    application.status.value, ApplicationStatus.APPROVED.value
                )
            code = self._resolve_merchant_code(merchant_code)
            moment = _now()

            merchant = Merchant(
                merchant_id=uuid4(),
                owner_principal_id=application.applicant_principal_id,
                merchant_code=code,
                display_name=display_name.strip(),
                status=MerchantStatus.ACTIVE,
                application_id=application.application_id,
                activated_at=moment,
            )
            # Every add-on starts off (v6.3 p.13, p.14). A merchant opts in later.
            policy = StandingShipmentPolicy(
                merchant_id=merchant.merchant_id, updated_at=moment
            )

            application.status = ApplicationStatus.APPROVED
            application.decided_at = moment
            application.decided_by_principal_id = reviewer_principal_id
            application.decision_reason = None
            application.version += 1

            self._uow.merchants.save(merchant)
            self._uow.policies.save(policy)
            self._uow.applications.save(application)
            event_id = self._publish_decision(
                application, merchant_id=merchant.merchant_id, merchant_code=code
            )
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return ApprovalResult(
            application=application, merchant=merchant, policy=policy, event_id=event_id
        )

    # ------------------------------------------------------------- queries

    def get(self, application_id: UUID) -> MerchantApplication:
        self._uow.begin()
        try:
            application = self._load(application_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return application

    def list_for_applicant(self, principal_id: UUID) -> tuple[MerchantApplication, ...]:
        self._uow.begin()
        try:
            found = self._uow.applications.list_for_applicant(principal_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    # ------------------------------------------------------------- internals

    def _load(self, application_id: UUID) -> MerchantApplication:
        application = self._uow.applications.get(application_id)
        if application is None:
            raise ApplicationNotFound(str(application_id))
        return application

    def _resolve_merchant_code(self, requested: str | None) -> str:
        if requested is not None:
            code = validate_merchant_code(requested)
            if self._uow.merchants.find_by_code(code) is not None:
                raise MerchantCodeAlreadyIssued(code)
            return code
        for _ in range(_MAX_CODE_ATTEMPTS):
            candidate = "".join(
                secrets.choice(_MERCHANT_CODE_ALPHABET)
                for _ in range(_MERCHANT_CODE_LENGTH)
            )
            if self._uow.merchants.find_by_code(candidate) is None:
                return candidate
        msg = "could not allocate a unique merchant code"
        raise RuntimeError(msg)

    def _publish_decision(
        self,
        application: MerchantApplication,
        *,
        merchant_id: UUID | None = None,
        merchant_code: str | None = None,
    ) -> UUID:
        event_id = uuid4()
        payload_json, subject = build_application_decided_envelope(
            application=application,
            aggregate_version=application.version,
            event_id=event_id,
            correlation_id=uuid4(),
            merchant_id=merchant_id,
            merchant_code=merchant_code,
        )
        moment = _now()
        self._uow.outbox.insert(
            OutboxRecord(
                id=uuid4(),
                event_id=event_id,
                subject=subject,
                event_type=payload_json["event_type"],
                event_version=int(payload_json["event_version"]),
                aggregate_id=application.application_id,
                aggregate_version=application.version,
                payload_json=payload_json,
                status=OutboxStatus.PENDING,
                attempt_count=0,
                max_attempts=self._outbox_max_attempts,
                next_attempt_at=moment,
                created_at=moment,
            )
        )
        return event_id
