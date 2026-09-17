"""Store team membership (MER-20, SEC-05 store-membership half).

Customer App v3 is precise about the security model and this service enforces it rather
than restating it: an invitation is addressed to a phone number and grants nothing until
the invitee accepts in their own account; a member sees only the branches they are
assigned to; and a warehouse keeper can never author a shipment or see money.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from merchant.domain.entities import Merchant, StoreAccess, TeamMembership
from merchant.domain.errors import (
    ForbiddenTeamCapability,
    InvalidPhoneNumber,
    MembershipAlreadyExists,
    MembershipNotFound,
    MembershipNotPending,
    MembershipStoresRequired,
    MerchantNotActive,
    MerchantNotFound,
    StoreArchived,
    StoreNotFound,
)
from merchant.domain.messaging import OutboxRecord, OutboxStatus
from merchant.domain.value_objects import (
    FORBIDDEN_TEAM_CAPABILITIES,
    ROLE_PERMISSIONS,
    MembershipStatus,
    StorePermission,
    TeamRole,
)
from merchant.infrastructure.contracts.envelopes import (
    build_team_membership_changed_envelope,
)
from merchant.ports.repository import MerchantUnitOfWork

_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
_LIVE_STATUSES = frozenset({MembershipStatus.PENDING, MembershipStatus.ACTIVE})


def normalize_phone(raw: str) -> str:
    """Normalise to E.164. Iraqi local forms (07XX…) are accepted and expanded."""
    digits = re.sub(r"[^\d+]", "", raw.strip())
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if digits.startswith("0") and len(digits) >= 10:
        digits = "+964" + digits[1:]
    if not digits.startswith("+"):
        digits = "+" + digits
    if not _E164.match(digits):
        raise InvalidPhoneNumber()
    return digits


def phone_last4(phone: str) -> str:
    return phone[-4:]


def _now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class InviteDraft:
    phone: str
    store_ids: tuple[UUID, ...]
    display_name: str | None = None
    role: TeamRole = TeamRole.WAREHOUSE_KEEPER


@dataclass(frozen=True, slots=True)
class MerchantAffiliation:
    """One row of "which stores may I act for", for the signed-in principal."""

    merchant: Merchant
    relationship: str
    role: str | None = None
    permissions: frozenset[str] = frozenset()
    store_ids: tuple[UUID, ...] = ()


class TeamService:
    def __init__(
        self, unit_of_work: MerchantUnitOfWork, *, outbox_max_attempts: int = 5
    ) -> None:
        self._uow = unit_of_work
        self._outbox_max_attempts = outbox_max_attempts

    # ------------------------------------------------------------- invite

    def invite(self, *, merchant_id: UUID, draft: InviteDraft) -> TeamMembership:
        self._uow.begin()
        try:
            merchant = self._require_active_merchant(merchant_id)
            phone = normalize_phone(draft.phone)
            store_ids = self._validate_stores(merchant_id, draft.store_ids)

            existing = self._uow.memberships.find_live_for_phone(merchant_id, phone)
            if existing is not None:
                raise MembershipAlreadyExists(phone_last4(phone))

            membership = TeamMembership(
                membership_id=uuid4(),
                merchant_id=merchant_id,
                invited_phone=phone,
                role=draft.role,
                status=MembershipStatus.PENDING,
                store_ids=store_ids,
                display_name=(draft.display_name or "").strip() or None,
                invited_at=_now(),
            )
            self._uow.memberships.save(membership)
            self._publish_membership(
                membership, merchant_version=self._advance_merchant(merchant)
            )
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return membership

    def update_branches(
        self, *, membership_id: UUID, store_ids: tuple[UUID, ...]
    ) -> TeamMembership:
        self._uow.begin()
        try:
            membership = self._load(membership_id)
            merchant = self._require_active_merchant(membership.merchant_id)
            membership.store_ids = self._validate_stores(
                membership.merchant_id, store_ids
            )
            membership.version += 1
            self._uow.memberships.save(membership)
            self._publish_membership(
                membership, merchant_version=self._advance_merchant(merchant)
            )
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return membership

    # ------------------------------------------------------------- invitee

    def accept(
        self, *, membership_id: UUID, member_principal_id: UUID
    ) -> TeamMembership:
        """The invitee accepts in their own account — the moment any permission exists."""
        return self._respond(
            membership_id=membership_id,
            member_principal_id=member_principal_id,
            status=MembershipStatus.ACTIVE,
        )

    def decline(self, *, membership_id: UUID, member_principal_id: UUID) -> TeamMembership:
        return self._respond(
            membership_id=membership_id,
            member_principal_id=member_principal_id,
            status=MembershipStatus.DECLINED,
        )

    def _respond(
        self,
        *,
        membership_id: UUID,
        member_principal_id: UUID,
        status: MembershipStatus,
    ) -> TeamMembership:
        self._uow.begin()
        try:
            membership = self._load(membership_id)
            if membership.status is not MembershipStatus.PENDING:
                raise MembershipNotPending(membership.status.value)
            merchant = self._require_active_merchant(membership.merchant_id)
            moment = _now()
            membership.status = status
            membership.member_principal_id = member_principal_id
            if status is MembershipStatus.ACTIVE:
                membership.accepted_at = moment
            else:
                membership.ended_at = moment
            membership.version += 1
            self._uow.memberships.save(membership)
            self._publish_membership(
                membership, merchant_version=self._advance_merchant(merchant)
            )
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return membership

    # ------------------------------------------------------------- remove

    def remove(self, *, membership_id: UUID) -> TeamMembership:
        self._uow.begin()
        try:
            membership = self._load(membership_id)
            merchant = self._require_active_merchant(membership.merchant_id)
            membership.status = MembershipStatus.REMOVED
            membership.ended_at = _now()
            membership.version += 1
            self._uow.memberships.save(membership)
            self._publish_membership(
                membership, merchant_version=self._advance_merchant(merchant)
            )
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return membership

    # ------------------------------------------------------------- queries

    def list_for_merchant(self, *, merchant_id: UUID) -> tuple[TeamMembership, ...]:
        self._uow.begin()
        try:
            found = self._uow.memberships.list_for_merchant(merchant_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    def list_pending_invitations(self, *, phone: str) -> tuple[TeamMembership, ...]:
        normalized = normalize_phone(phone)
        self._uow.begin()
        try:
            found = self._uow.memberships.list_pending_for_phone(normalized)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    def store_access(self, *, principal_id: UUID) -> tuple[StoreAccess, ...]:
        """What this principal may do, per merchant.

        This is the read model Ordering and Finance consume over HTTP instead of reading
        Merchant's tables. Inactive memberships contribute nothing at all.
        """
        self._uow.begin()
        try:
            memberships = self._uow.memberships.list_for_principal(principal_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return tuple(
            StoreAccess(
                merchant_id=membership.merchant_id,
                principal_id=principal_id,
                role=membership.role,
                permissions=membership.permissions,
                store_ids=membership.store_ids,
            )
            for membership in memberships
            if membership.is_active
        )

    def my_merchants(self, *, principal_id: UUID) -> tuple[MerchantAffiliation, ...]:
        """Every merchant this principal may act for, and in what capacity.

        Two different relationships, deliberately kept apart rather than flattened:

        * **OWNER** — the principal owns the merchant. Their reach comes from ownership,
          so there is no role and no permission list to report.
        * **MEMBER** — an *active* team membership. The role and its exact permissions are
          reported so the client gates on capabilities rather than on a role label.

        A pending, declined or removed membership contributes nothing (Customer App v3
        `teamSent`: "until then the member stays pending and sees nothing of your store").
        """
        self._uow.begin()
        try:
            owned = self._uow.merchants.list_for_owner(principal_id)
            memberships = self._uow.memberships.list_for_principal(principal_id)
            by_id = {merchant.merchant_id: merchant for merchant in owned}
            for membership in memberships:
                if not membership.is_active:
                    continue
                if membership.merchant_id in by_id:
                    continue
                merchant = self._uow.merchants.get(membership.merchant_id)
                if merchant is not None:
                    by_id[merchant.merchant_id] = merchant
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise

        owned_ids = {merchant.merchant_id for merchant in owned}
        active = {
            membership.merchant_id: membership
            for membership in memberships
            if membership.is_active
        }

        affiliations: list[MerchantAffiliation] = []
        for merchant_id, merchant in by_id.items():
            if merchant_id in owned_ids:
                affiliations.append(
                    MerchantAffiliation(merchant=merchant, relationship="OWNER")
                )
                continue
            membership = active[merchant_id]
            affiliations.append(
                MerchantAffiliation(
                    merchant=merchant,
                    relationship="MEMBER",
                    role=membership.role,
                    permissions=membership.permissions,
                    store_ids=membership.store_ids,
                )
            )
        return tuple(affiliations)

    @staticmethod
    def assert_capability_permitted(capability: str) -> None:
        """Guard against a future role quietly acquiring shipment or money reach."""
        if capability in FORBIDDEN_TEAM_CAPABILITIES:
            raise ForbiddenTeamCapability(capability)

    @staticmethod
    def permissions_for(role: TeamRole) -> frozenset[StorePermission]:
        return ROLE_PERMISSIONS[role]

    # ------------------------------------------------------------- internals

    def _load(self, membership_id: UUID) -> TeamMembership:
        membership = self._uow.memberships.get(membership_id)
        if membership is None:
            raise MembershipNotFound(str(membership_id))
        return membership

    def _require_active_merchant(self, merchant_id: UUID) -> Merchant:
        merchant = self._uow.merchants.get(merchant_id)
        if merchant is None:
            raise MerchantNotFound(str(merchant_id))
        if not merchant.is_active:
            raise MerchantNotActive(str(merchant_id))
        return merchant

    def _advance_merchant(self, merchant: Merchant) -> int:
        """The team belongs to the merchant aggregate, so a membership change advances it.

        This is what makes ``(aggregate_id, aggregate_version)`` a usable ordering key for
        consumers: two membership changes to one merchant can never claim the same
        version, and a consumer can tell which happened first.
        """
        merchant.version += 1
        self._uow.merchants.save(merchant)
        return merchant.version

    def _validate_stores(
        self, merchant_id: UUID, store_ids: tuple[UUID, ...]
    ) -> tuple[UUID, ...]:
        unique = tuple(dict.fromkeys(store_ids))
        if not unique:
            raise MembershipStoresRequired()
        for store_id in unique:
            store = self._uow.stores.get(store_id)
            if store is None or store.merchant_id != merchant_id:
                raise StoreNotFound(str(store_id))
            if not store.is_active:
                raise StoreArchived(str(store_id))
        return unique

    def _publish_membership(
        self, membership: TeamMembership, *, merchant_version: int
    ) -> UUID:
        event_id = uuid4()
        moment = _now()
        payload_json, subject = build_team_membership_changed_envelope(
            membership=membership,
            merchant_aggregate_version=merchant_version,
            changed_at=moment,
            event_id=event_id,
            correlation_id=uuid4(),
        )
        self._uow.outbox.insert(
            OutboxRecord(
                id=uuid4(),
                event_id=event_id,
                subject=subject,
                event_type=payload_json["event_type"],
                event_version=int(payload_json["event_version"]),
                aggregate_id=membership.merchant_id,
                aggregate_version=merchant_version,
                payload_json=payload_json,
                status=OutboxStatus.PENDING,
                attempt_count=0,
                max_attempts=self._outbox_max_attempts,
                next_attempt_at=moment,
                created_at=moment,
            )
        )
        return event_id
