"""Domain errors for the Delivery service."""

from __future__ import annotations


class DeliveryError(Exception):
    """Base Delivery domain error."""


# ------------------------------------------------------------------ lookup


class ManifestNotFound(DeliveryError):
    def __init__(self, manifest_id: str) -> None:
        self.manifest_id = manifest_id
        super().__init__(f"delivery manifest not found: {manifest_id}")


class StopNotFound(DeliveryError):
    def __init__(self, stop_id: str) -> None:
        self.stop_id = stop_id
        super().__init__(f"delivery stop not found: {stop_id}")


class FailedAttemptNotFound(DeliveryError):
    def __init__(self, attempt_id: str) -> None:
        self.attempt_id = attempt_id
        super().__init__(f"failed attempt not found: {attempt_id}")


class ParcelAlreadyOnAManifest(DeliveryError):
    def __init__(self, tracking_code: str) -> None:
        self.tracking_code = tracking_code
        super().__init__(f"parcel is already on a manifest: {tracking_code}")


# ------------------------------------------------------------------ lifecycle


class StopTransitionNotAllowed(DeliveryError):
    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"cannot move a {current} delivery stop to {target}")


class NotThisDriversStop(DeliveryError):
    """Custody belongs to one driver; another cannot act on their parcel."""

    def __init__(self, tracking_code: str) -> None:
        self.tracking_code = tracking_code
        super().__init__(f"this parcel is in another driver's custody: {tracking_code}")


class StopAlreadyClosed(DeliveryError):
    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"this stop is already {status}")


# ------------------------------------------------------------------ the wait


class WaitNotStarted(DeliveryError):
    def __init__(self) -> None:
        super().__init__("the ten-minute wait has not been started at this door")


class WaitNotOver(DeliveryError):
    """v6.3 p.26 — a failed attempt is recordable only once the wait expires.

    Driver App v8 `lmWait` disables the button with "Available when the 10 minutes are
    up", so recording one early would contradict what the driver is being shown.
    """

    def __init__(self, remaining_seconds: int) -> None:
        self.remaining_seconds = remaining_seconds
        super().__init__(
            "the courier waits ten minutes before an absence can be recorded — "
            f"{remaining_seconds}s remain"
        )


# ------------------------------------------------------------------ verification


class DeliveryCodeNotSet(DeliveryError):
    def __init__(self, tracking_code: str) -> None:
        self.tracking_code = tracking_code
        super().__init__(f"no delivery code is set for {tracking_code}")


class DeliveryCodeIncorrect(DeliveryError):
    """Deliberately says nothing about the code itself."""

    def __init__(self, attempts_remaining: int) -> None:
        self.attempts_remaining = attempts_remaining
        super().__init__("that delivery code is not correct")


class TooManyCodeAttempts(DeliveryError):
    def __init__(self) -> None:
        super().__init__(
            "too many incorrect delivery codes — complete the attempt another way"
        )


class IdFallbackIsOnlyForTheNamedReceiver(DeliveryError):
    """v6.3 p.26 — "Anyone else cannot take this parcel without the code"."""

    def __init__(self) -> None:
        super().__init__(
            "the ID fallback works only for the receiver the merchant named; anyone "
            "else needs the delivery code"
        )


class NoNamedReceiverOnThisParcel(DeliveryError):
    """v6.3 p.12 makes the receiver's name optional, so the fallback may not exist."""

    def __init__(self, tracking_code: str) -> None:
        self.tracking_code = tracking_code
        super().__init__(
            f"{tracking_code} names no receiver, so there is no ID to fall back to"
        )


class NotVerified(DeliveryError):
    def __init__(self) -> None:
        super().__init__("the person at the door has not been verified")


# ------------------------------------------------------------------ seal / inspection


class SealMustBeCheckedFirst(DeliveryError):
    def __init__(self) -> None:
        super().__init__("this parcel carries a seal that has not been checked")


class BrokenSealStopsTheHandover(DeliveryError):
    """Driver App v8 `lmSeal`: "Stop and report — the parcel may have been opened"."""

    def __init__(self) -> None:
        super().__init__(
            "the parcel seal is broken — stop and report it; the parcel stays in "
            "HUDHUD custody"
        )


class OpenBoxNotAllowed(DeliveryError):
    """v6.3 p.35 — the receiver inspects only when the merchant enabled it."""

    def __init__(self) -> None:
        super().__init__(
            "the sender did not enable open-box; the receiver takes the parcel sealed"
        )


class PhotoRequiredBeforeOpening(DeliveryError):
    def __init__(self) -> None:
        super().__init__(
            "photo documentation is on — the sealed parcel is photographed before it "
            "is opened"
        )


class PhotoRequiredAfterInspection(DeliveryError):
    def __init__(self) -> None:
        super().__init__(
            "photo documentation is on — the parcel is photographed after the inspection"
        )


class PhotographyNotEnabled(DeliveryError):
    """v6.3 p.14 — without the add-on, no photo is taken at any stage."""

    def __init__(self) -> None:
        super().__init__(
            "couriers do not photograph parcels unless the sender enabled photo "
            "documentation"
        )


# ------------------------------------------------------------------ payment


class PaymentRequiredBeforeHandover(DeliveryError):
    """v6.3 p.31 — a COD parcel is not Delivered unless payment was collected."""

    def __init__(self, tracking_code: str) -> None:
        self.tracking_code = tracking_code
        super().__init__(
            f"{tracking_code} is cash on delivery and has not been paid — it cannot be "
            "handed over"
        )


class PosProofRequired(DeliveryError):
    """Driver App v8 `lmPayApproved`: "PROOF OF PAYMENT — ONE IS REQUIRED"."""

    def __init__(self) -> None:
        super().__init__(
            "a card payment needs its transaction number or a photo of the receipt "
            "before the parcel is handed over"
        )


class NothingToCollect(DeliveryError):
    def __init__(self) -> None:
        super().__init__("this shipment was paid in advance — collect nothing")


class PaymentAlreadyRecorded(DeliveryError):
    def __init__(self) -> None:
        super().__init__("payment has already been recorded for this stop")


class InvalidPosReference(DeliveryError):
    def __init__(self, raw: str) -> None:
        super().__init__(f"not a usable POS reference: {raw}")


# ------------------------------------------------------------------ outcomes


class OnlyOperationsDecidesTheNextAttempt(DeliveryError):
    """OPS-08 — "Held for next attempt — decided by operations"."""

    def __init__(self) -> None:
        super().__init__(
            "what happens after a failed delivery is decided by operations, not by the "
            "driver"
        )


class NextAttemptAlreadyDecided(DeliveryError):
    def __init__(self, decision: str) -> None:
        self.decision = decision
        super().__init__(f"the next attempt is already decided: {decision}")


# ------------------------------------------------------------------ receiver


class RatingOutOfRange(DeliveryError):
    def __init__(self) -> None:
        super().__init__("a courier rating is between 1 and 5")


class RatingNotYetPossible(DeliveryError):
    """There is nothing to rate until the courier has handed the parcel over."""

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"this parcel has not been delivered yet: {status}")


class RatingAlreadyGiven(DeliveryError):
    def __init__(self, tracking_code: str) -> None:
        self.tracking_code = tracking_code
        super().__init__(f"this parcel has already been rated: {tracking_code}")


class InvalidHandoverWindow(DeliveryError):
    def __init__(self, detail: str) -> None:
        super().__init__(f"invalid handover window: {detail}")


# ------------------------------------------------------------------ concurrency


class StaleDeliveryRecord(DeliveryError):
    def __init__(self, table: str) -> None:
        self.table = table
        super().__init__(f"{table} changed concurrently")
