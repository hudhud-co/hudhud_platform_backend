"""Money at the door (DRV-L12 … DRV-L16, PAY-01).

Three rules from v6.3 p.31 and the Driver App decide everything here:

* **a COD parcel is not Delivered unless payment was actually collected**;
* **a POS decline is never a dead end** — the driver falls back to cash;
* **card payments go straight to HUDHUD** and never enter the driver's cash custody,
  while cash does and must be settled before the end of the shift.

A card payment also needs proof before the parcel changes hands: Driver App v8
`lmPayApproved` labels it "PROOF OF PAYMENT — ONE IS REQUIRED" and will not complete the
delivery without a transaction number or a photograph of the receipt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from delivery.domain.entities import DeliveryStop, PaymentRecord
from delivery.domain.errors import (
    InvalidPosReference,
    NothingToCollect,
    PaymentAlreadyRecorded,
    PaymentRequiredBeforeHandover,
    PosProofRequired,
    StopNotFound,
)
from delivery.domain.money import Money
from delivery.domain.value_objects import (
    EvidenceMediaRef,
    PaymentMethod,
    PaymentOutcome,
    normalize_pos_reference,
)
from delivery.ports.repository import DeliveryUnitOfWork


def _now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class PaymentResult:
    payment: PaymentRecord
    #: How much the driver's cash custody grew. Zero for prepaid and for card.
    cash_custody_delta: Money


class PaymentService:
    def __init__(self, unit_of_work: DeliveryUnitOfWork) -> None:
        self._uow = unit_of_work

    def confirm_prepaid(
        self, *, stop_id: UUID, driver_principal_id: UUID
    ) -> PaymentResult:
        """v6.3 p.31 — prepaid means collect nothing at the door."""
        self._uow.begin()
        try:
            stop = self._load(stop_id)
            self._refuse_second_payment(stop_id)
            if stop.payment_method_expected is not PaymentMethod.PREPAID:
                raise PaymentRequiredBeforeHandover(stop.tracking_code)
            payment = PaymentRecord(
                payment_id=uuid4(),
                stop_id=stop_id,
                method=PaymentMethod.PREPAID,
                outcome=PaymentOutcome.NOTHING_TO_COLLECT,
                amount=None,
                recorded_at=_now(),
                recorded_by_actor_id=driver_principal_id,
            )
            self._uow.payments.save(payment)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return PaymentResult(payment=payment, cash_custody_delta=Money.zero())

    def collect_cash(
        self, *, stop_id: UUID, driver_principal_id: UUID
    ) -> PaymentResult:
        """Cash becomes the driver's custody and must be settled before the shift ends."""
        self._uow.begin()
        try:
            stop = self._load(stop_id)
            self._refuse_second_payment(stop_id)
            amount = self._amount_due(stop)
            payment = PaymentRecord(
                payment_id=uuid4(),
                stop_id=stop_id,
                method=PaymentMethod.CASH,
                outcome=PaymentOutcome.COLLECTED,
                amount=amount,
                recorded_at=_now(),
                recorded_by_actor_id=driver_principal_id,
            )
            self._uow.payments.save(payment)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return PaymentResult(payment=payment, cash_custody_delta=amount)

    def record_card_approval(
        self,
        *,
        stop_id: UUID,
        driver_principal_id: UUID,
        pos_reference: str | None = None,
        pos_receipt: EvidenceMediaRef | None = None,
    ) -> PaymentResult:
        """DRV-L14, DRV-L15 — proof before handover, and never into driver custody."""
        if not pos_reference and pos_receipt is None:
            raise PosProofRequired()
        reference = None
        if pos_reference:
            try:
                reference = normalize_pos_reference(pos_reference)
            except ValueError as exc:
                raise InvalidPosReference(pos_reference) from exc

        self._uow.begin()
        try:
            stop = self._load(stop_id)
            self._refuse_second_payment(stop_id)
            amount = self._amount_due(stop)
            payment = PaymentRecord(
                payment_id=uuid4(),
                stop_id=stop_id,
                method=PaymentMethod.POS_CARD,
                outcome=PaymentOutcome.COLLECTED,
                amount=amount,
                pos_reference=reference,
                pos_receipt=pos_receipt,
                recorded_at=_now(),
                recorded_by_actor_id=driver_principal_id,
            )
            self._uow.payments.save(payment)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        # Card money goes straight to HUDHUD; the driver never held it.
        return PaymentResult(payment=payment, cash_custody_delta=Money.zero())

    def record_card_decline(
        self, *, stop_id: UUID, driver_principal_id: UUID
    ) -> PaymentRecord:
        """DRV-L13 — a decline is recorded, and the driver falls back to cash.

        Deliberately does not close the stop or fail the attempt: the parcel is still at
        the door and still payable.
        """
        self._uow.begin()
        try:
            stop = self._load(stop_id)
            payment = PaymentRecord(
                payment_id=uuid4(),
                stop_id=stop_id,
                method=PaymentMethod.POS_CARD,
                outcome=PaymentOutcome.DECLINED,
                amount=self._amount_due(stop),
                recorded_at=_now(),
                recorded_by_actor_id=driver_principal_id,
            )
            # A decline is not *the* payment: it is deliberately not saved into the
            # one payment slot, so the fallback to cash can still fill it.
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return payment

    def payment_for(self, *, stop_id: UUID) -> PaymentRecord | None:
        self._uow.begin()
        try:
            found = self._uow.payments.find_for_stop(stop_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    def assert_paid_if_due(self, *, stop: DeliveryStop) -> PaymentRecord | None:
        """v6.3 p.31 — the rule that keeps a COD parcel from being handed over unpaid."""
        return check_payment_satisfies(
            stop=stop, payment=self.payment_for(stop_id=stop.stop_id)
        )

    # ------------------------------------------------------------- internals

    def _load(self, stop_id: UUID) -> DeliveryStop:
        stop = self._uow.stops.get(stop_id)
        if stop is None:
            raise StopNotFound(str(stop_id))
        return stop

    def _refuse_second_payment(self, stop_id: UUID) -> None:
        existing = self._uow.payments.find_for_stop(stop_id)
        if existing is not None and existing.outcome is not PaymentOutcome.DECLINED:
            raise PaymentAlreadyRecorded()

    @staticmethod
    def _amount_due(stop: DeliveryStop) -> Money:
        if stop.payment_method_expected is PaymentMethod.PREPAID:
            raise NothingToCollect()
        if stop.cod_amount is None:
            raise PaymentRequiredBeforeHandover(stop.tracking_code)
        return stop.cod_amount


def check_payment_satisfies(
    *, stop: DeliveryStop, payment: PaymentRecord | None
) -> PaymentRecord | None:
    """The p.31 rule as a pure decision, so a caller inside a transaction can use it.

    :meth:`PaymentService.assert_paid_if_due` reads the payment and calls this; the
    handover calls it with a payment it has already read, keeping the check and the
    state change in one transaction.
    """
    if not stop.requires_payment_at_door:
        return payment
    if payment is None or payment.outcome is not PaymentOutcome.COLLECTED:
        raise PaymentRequiredBeforeHandover(stop.tracking_code)
    if payment.method is PaymentMethod.POS_CARD and not payment.has_pos_proof:
        raise PosProofRequired()
    return payment
