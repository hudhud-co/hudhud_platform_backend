"""Stores, branches and the standing shipment policy (MER-10, MER-14, MER-15, MER-16)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from merchant.domain.entities import Merchant, StandingShipmentPolicy, Store
from merchant.domain.errors import (
    LastStoreCannotBeArchived,
    MerchantNotActive,
    MerchantNotFound,
    SenderMayNotSetPlatformPolicy,
    StoreArchived,
    StoreNotFound,
    UnknownGovernorate,
)
from merchant.domain.value_objects import (
    DeliveryFeePayer,
    GeoPoint,
    normalize_governorate,
)
from merchant.ports.repository import MerchantUnitOfWork

#: v6.3 p.13 "Role boundary — Sender". These are set once by Hudhud for everyone and no
#: sender can override them, so the API refuses them outright rather than storing a value
#: that a later reader might mistake for authority (MER-15).
PLATFORM_FIXED_POLICY_KEYS: frozenset[str] = frozenset(
    {
        "acceptance_standard",
        "packaging_standard",
        "return_fee_owner",
        "refund_owner",
        "hold_period_days",
        "door_wait_minutes",
        "liability_position",
    }
)

#: The read-only view of those same rules, so a merchant can *see* them without setting
#: them. Values are the v6.3 confirmed decisions, cited where they come from.
PLATFORM_FIXED_POLICY: dict[str, object] = {
    "return_fee_owner": "MERCHANT",  # p.13, p.38 — always the merchant, regardless of reason
    "refund_owner": "HUDHUD_ONLY_WHEN_COLLECTED_IN_ADVANCE",  # p.13, p.39
    "hold_period_days": 3,  # p.29 — the 3-day hold replaced the 3-attempt limit
    "door_wait_minutes": 10,  # p.26
    "liability_position": "HUDHUD_FULL_CUSTODY_LIABILITY",  # p.40
    "acceptance_standard": "HUDHUD_DRIVER_PACKAGING_ASSESSMENT",  # p.16
}


@dataclass(frozen=True, slots=True)
class StoreDraft:
    name: str
    governorate: str
    address_line: str
    area: str | None = None
    landmark: str | None = None
    geo: GeoPoint | None = None
    make_default_pickup: bool = False


def _now() -> datetime:
    return datetime.now(tz=UTC)


class StoreService:
    def __init__(self, unit_of_work: MerchantUnitOfWork) -> None:
        self._uow = unit_of_work

    # ------------------------------------------------------------- stores

    def create_store(self, *, merchant_id: UUID, draft: StoreDraft) -> Store:
        self._uow.begin()
        try:
            self._require_active_merchant(merchant_id)
            governorate = self._normalize(draft.governorate)
            existing = self._uow.stores.list_for_merchant(merchant_id)
            active = [store for store in existing if store.is_active]
            # The first branch is the default pickup point: a merchant with exactly one
            # store and no default would be unable to book a pickup at all.
            make_default = draft.make_default_pickup or not active

            if make_default:
                current = self._uow.stores.find_default_pickup(merchant_id)
                if current is not None:
                    current.is_default_pickup = False
                    current.version += 1
                    self._uow.stores.save(current)

            store = Store(
                store_id=uuid4(),
                merchant_id=merchant_id,
                name=draft.name.strip(),
                governorate=governorate,
                address_line=draft.address_line.strip(),
                area=(draft.area or "").strip() or None,
                landmark=(draft.landmark or "").strip() or None,
                geo=draft.geo,
                is_default_pickup=make_default,
                created_at=_now(),
            )
            self._uow.stores.save(store)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return store

    def update_store(
        self,
        *,
        store_id: UUID,
        name: str | None = None,
        governorate: str | None = None,
        address_line: str | None = None,
        area: str | None = None,
        landmark: str | None = None,
        geo: GeoPoint | None = None,
    ) -> Store:
        self._uow.begin()
        try:
            store = self._load_store(store_id)
            if not store.is_active:
                raise StoreArchived(str(store_id))
            if name is not None:
                store.name = name.strip()
            if governorate is not None:
                store.governorate = self._normalize(governorate)
            if address_line is not None:
                store.address_line = address_line.strip()
            if area is not None:
                store.area = area.strip() or None
            if landmark is not None:
                store.landmark = landmark.strip() or None
            if geo is not None:
                store.geo = geo
            store.version += 1
            self._uow.stores.save(store)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return store

    def set_default_pickup(self, *, store_id: UUID) -> Store:
        self._uow.begin()
        try:
            store = self._load_store(store_id)
            if not store.is_active:
                raise StoreArchived(str(store_id))
            current = self._uow.stores.find_default_pickup(store.merchant_id)
            if current is not None and current.store_id != store.store_id:
                current.is_default_pickup = False
                current.version += 1
                self._uow.stores.save(current)
            store.is_default_pickup = True
            store.version += 1
            self._uow.stores.save(store)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return store

    def archive_store(self, *, store_id: UUID) -> Store:
        self._uow.begin()
        try:
            store = self._load_store(store_id)
            if not store.is_active:
                raise StoreArchived(str(store_id))
            active = [
                candidate
                for candidate in self._uow.stores.list_for_merchant(store.merchant_id)
                if candidate.is_active
            ]
            if len(active) <= 1:
                raise LastStoreCannotBeArchived()

            was_default = store.is_default_pickup
            store.archived_at = _now()
            store.is_default_pickup = False
            store.version += 1
            self._uow.stores.save(store)

            # Archiving the default pickup point would otherwise leave the merchant with
            # branches but nowhere a courier is told to collect from.
            if was_default:
                promoted = next(
                    candidate
                    for candidate in active
                    if candidate.store_id != store.store_id
                )
                promoted.is_default_pickup = True
                promoted.version += 1
                self._uow.stores.save(promoted)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return store

    def list_stores(
        self, *, merchant_id: UUID, include_archived: bool = False
    ) -> tuple[Store, ...]:
        self._uow.begin()
        try:
            found = self._uow.stores.list_for_merchant(
                merchant_id, include_archived=include_archived
            )
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    # ------------------------------------------------------------- policy

    def get_policy(self, *, merchant_id: UUID) -> StandingShipmentPolicy:
        self._uow.begin()
        try:
            policy = self._uow.policies.get(merchant_id)
            if policy is None:
                self._require_active_merchant(merchant_id)
                policy = StandingShipmentPolicy(merchant_id=merchant_id, updated_at=_now())
                self._uow.policies.save(policy)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return policy

    def update_policy(
        self,
        *,
        merchant_id: UUID,
        open_box_allowed: bool | None = None,
        photo_documentation_enabled: bool | None = None,
        hudhud_packaging_enabled: bool | None = None,
        delivery_fee_payer: DeliveryFeePayer | None = None,
        rejected_keys: tuple[str, ...] = (),
    ) -> StandingShipmentPolicy:
        """Update what the sender is allowed to decide, and only that.

        ``rejected_keys`` carries any company-wide rule the caller tried to set. It is
        refused before anything is written, so a request that mixes a legitimate change
        with an illegitimate one changes nothing.
        """
        forbidden = tuple(
            sorted(key for key in rejected_keys if key in PLATFORM_FIXED_POLICY_KEYS)
        )
        if forbidden:
            raise SenderMayNotSetPlatformPolicy(forbidden)

        self._uow.begin()
        try:
            self._require_active_merchant(merchant_id)
            policy = self._uow.policies.get(merchant_id)
            if policy is None:
                policy = StandingShipmentPolicy(merchant_id=merchant_id)
            if open_box_allowed is not None:
                policy.open_box_allowed = open_box_allowed
            if photo_documentation_enabled is not None:
                policy.photo_documentation_enabled = photo_documentation_enabled
            if hudhud_packaging_enabled is not None:
                policy.hudhud_packaging_enabled = hudhud_packaging_enabled
            if delivery_fee_payer is not None:
                policy.delivery_fee_payer = delivery_fee_payer
            policy.updated_at = _now()
            policy.version += 1
            self._uow.policies.save(policy)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return policy

    # ------------------------------------------------------------- internals

    def _normalize(self, governorate: str) -> str:
        try:
            return normalize_governorate(governorate)
        except ValueError as exc:
            raise UnknownGovernorate(governorate) from exc

    def _load_store(self, store_id: UUID) -> Store:
        store = self._uow.stores.get(store_id)
        if store is None:
            raise StoreNotFound(str(store_id))
        return store

    def _require_active_merchant(self, merchant_id: UUID) -> Merchant:
        merchant = self._uow.merchants.get(merchant_id)
        if merchant is None:
            raise MerchantNotFound(str(merchant_id))
        if not merchant.is_active:
            raise MerchantNotActive(str(merchant_id))
        return merchant
