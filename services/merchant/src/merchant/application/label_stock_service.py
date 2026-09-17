"""Label stock and printer authorization (MER-03, MER-04).

v6.3 changed this in 6.3 and the change is the whole point of the service: labels are not
printed per parcel. An approved merchant "already keeps a stock of pre-printed barcode
labels on hand (given to them when they were approved as a merchant)" (p.10), and
registering a shipment means taking one of those labels and sticking it on the box.

There is deliberately no "print a label for this shipment" operation anywhere in this
service. Its absence is the requirement, and a test asserts it stays absent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from merchant.domain.entities import LabelStockAllocation, PrinterAuthorization
from merchant.domain.errors import (
    LabelCountInvalid,
    LabelStockExhausted,
    MerchantNotActive,
    MerchantNotFound,
    PrinterAuthorizationNotFound,
    SelfPrintingNotAuthorized,
)
from merchant.domain.value_objects import (
    LabelStockSource,
    PrinterAuthorizationStatus,
    StockKind,
)
from merchant.ports.repository import MerchantUnitOfWork


def _now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class StockSummary:
    """What the app shows as "50 / 50 labels ready" on the store dashboard."""

    merchant_id: UUID
    total_labels: int
    consumed_labels: int
    total_seals: int = 0
    consumed_seals: int = 0

    @property
    def remaining(self) -> int:
        return self.total_labels - self.consumed_labels

    @property
    def remaining_seals(self) -> int:
        return self.total_seals - self.consumed_seals


class LabelStockService:
    def __init__(self, unit_of_work: MerchantUnitOfWork) -> None:
        self._uow = unit_of_work

    # ------------------------------------------------- printer authorization

    def authorize_printer(
        self, *, merchant_id: UUID, printer_serial: str, stock_reference: str
    ) -> PrinterAuthorization:
        """Record that Hudhud supplied this merchant a printer and blank stock (MER-04)."""
        self._uow.begin()
        try:
            self._require_active_merchant(merchant_id)
            authorization = PrinterAuthorization(
                authorization_id=uuid4(),
                merchant_id=merchant_id,
                printer_serial=printer_serial.strip(),
                stock_reference=stock_reference.strip(),
                status=PrinterAuthorizationStatus.ACTIVE,
                issued_at=_now(),
            )
            self._uow.printer_authorizations.save(authorization)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return authorization

    def revoke_printer(self, *, authorization_id: UUID) -> PrinterAuthorization:
        self._uow.begin()
        try:
            authorization = self._uow.printer_authorizations.get(authorization_id)
            if authorization is None:
                raise PrinterAuthorizationNotFound(str(authorization_id))
            authorization.status = PrinterAuthorizationStatus.REVOKED
            authorization.revoked_at = _now()
            authorization.version += 1
            self._uow.printer_authorizations.save(authorization)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return authorization

    # ------------------------------------------------------------- stock

    def issue_stock(
        self,
        *,
        merchant_id: UUID,
        batch_reference: str,
        label_count: int,
        source: LabelStockSource = LabelStockSource.HUDHUD_PREPRINTED,
        printer_authorization_id: UUID | None = None,
        stock_kind: StockKind = StockKind.LABEL,
    ) -> LabelStockAllocation:
        """Issue a batch of barcoded stock to a merchant.

        Self-printed stock is permitted only against a live Hudhud printer authorization.
        v6.3 p.12: "A merchant may not use any other printer or label stock for this."
        Packaging seals are never self-printed: p.14 makes them a Hudhud-supplied,
        per-parcel purchase whose code is scanned again at delivery.
        """
        if label_count < 1:
            raise LabelCountInvalid()
        if (
            stock_kind is StockKind.PACKAGING_SEAL
            and source is LabelStockSource.MERCHANT_SELF_PRINTED
        ):
            raise SelfPrintingNotAuthorized()

        self._uow.begin()
        try:
            self._require_active_merchant(merchant_id)
            if source is LabelStockSource.MERCHANT_SELF_PRINTED:
                self._require_live_printer_authorization(
                    merchant_id, printer_authorization_id
                )
            elif printer_authorization_id is not None:
                # Pre-printed stock comes from Hudhud, so a printer reference on it would
                # misrepresent where the labels came from.
                printer_authorization_id = None

            allocation = LabelStockAllocation(
                allocation_id=uuid4(),
                merchant_id=merchant_id,
                batch_reference=batch_reference.strip(),
                source=source,
                label_count=label_count,
                stock_kind=stock_kind,
                consumed_count=0,
                printer_authorization_id=printer_authorization_id,
                issued_at=_now(),
            )
            self._uow.label_stock.save(allocation)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return allocation

    def consume_labels(self, *, allocation_id: UUID, count: int = 1) -> LabelStockAllocation:
        """Draw labels from an allocation as the merchant uses them.

        Ordering reports consumption when a merchant scans a label onto a parcel; this
        service only ever counts down, and never mints a code for a specific shipment.
        """
        if count < 1:
            raise LabelCountInvalid()

        self._uow.begin()
        try:
            allocation = self._uow.label_stock.get(allocation_id)
            if allocation is None:
                raise LabelStockExhausted(str(allocation_id))
            if allocation.remaining < count:
                raise LabelStockExhausted(str(allocation_id))
            allocation.consumed_count += count
            allocation.version += 1
            self._uow.label_stock.save(allocation)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return allocation

    def summarize(self, *, merchant_id: UUID) -> StockSummary:
        self._uow.begin()
        try:
            allocations = self._uow.label_stock.list_for_merchant(merchant_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        labels = [a for a in allocations if a.stock_kind is StockKind.LABEL]
        seals = [a for a in allocations if a.stock_kind is StockKind.PACKAGING_SEAL]
        return StockSummary(
            merchant_id=merchant_id,
            total_labels=sum(item.label_count for item in labels),
            consumed_labels=sum(item.consumed_count for item in labels),
            total_seals=sum(item.label_count for item in seals),
            consumed_seals=sum(item.consumed_count for item in seals),
        )

    def list_allocations(self, *, merchant_id: UUID) -> tuple[LabelStockAllocation, ...]:
        self._uow.begin()
        try:
            found = self._uow.label_stock.list_for_merchant(merchant_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    # ------------------------------------------------------------- internals

    def _require_active_merchant(self, merchant_id: UUID) -> None:
        merchant = self._uow.merchants.get(merchant_id)
        if merchant is None:
            raise MerchantNotFound(str(merchant_id))
        if not merchant.is_active:
            raise MerchantNotActive(str(merchant_id))

    def _require_live_printer_authorization(
        self, merchant_id: UUID, authorization_id: UUID | None
    ) -> None:
        if authorization_id is None:
            raise SelfPrintingNotAuthorized()
        authorization = self._uow.printer_authorizations.get(authorization_id)
        if (
            authorization is None
            or authorization.merchant_id != merchant_id
            or not authorization.is_active
        ):
            raise SelfPrintingNotAuthorized()
