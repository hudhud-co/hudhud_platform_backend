"""The last mile, from the manifest scan to the door.

This is v6.3 chapter 7 in order, and the order matters — each step is a precondition of
the next, so the service refuses to skip one rather than trusting the app to keep them in
sequence:

    manifest scan (custody transfers)
      → set off       → the receiver is told (NTF-07)
      → arrive
      → wait 10 minutes if nobody answers
      → verify: delivery code, or the named receiver's ID
      → check the parcel seal if the merchant bought one
      → photo before opening, if photo documentation and open-box are both on
      → open-box inspection, or a sealed handover
      → photo after inspection, if photo documentation is on
      → payment
      → handover

Custody is with the driver at every status except `DELIVERED`. A refusal and a failed
attempt both leave the parcel with HUDHUD (v6.3 p.34, p.28).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from delivery.domain.delivery_code import (
    DeliveryCodePolicy,
    hash_delivery_code,
    verify_delivery_code,
)
from delivery.domain.entities import (
    DeliveryManifest,
    DeliveryStop,
    ParcelSettings,
    PhotoEvidence,
    VerificationAttempt,
)
from delivery.domain.errors import (
    BrokenSealStopsTheHandover,
    DeliveryCodeIncorrect,
    DeliveryCodeNotSet,
    IdFallbackIsOnlyForTheNamedReceiver,
    ManifestNotFound,
    NoNamedReceiverOnThisParcel,
    NotThisDriversStop,
    OpenBoxNotAllowed,
    ParcelAlreadyOnAManifest,
    PhotographyNotEnabled,
    PhotoRequiredAfterInspection,
    PhotoRequiredBeforeOpening,
    SealMustBeCheckedFirst,
    StopNotFound,
    StopTransitionNotAllowed,
    TooManyCodeAttempts,
    WaitNotOver,
    WaitNotStarted,
)
from delivery.domain.id_evidence import IdEvidencePolicy
from delivery.domain.money import Money
from delivery.domain.value_objects import (
    DOOR_WAIT_SECONDS,
    EvidenceMediaRef,
    InspectionOutcome,
    PaymentMethod,
    PhotoStage,
    SealCheckOutcome,
    StopStatus,
    VerificationMethod,
    VerificationOutcome,
)
from delivery.ports.repository import DeliveryUnitOfWork


def _now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class ParcelForManifest:
    """One parcel a driver is scanning onto their round."""

    tracking_code: str
    settings: ParcelSettings
    delivery_code: str | None = None
    named_receiver: str | None = None
    cod_amount: Money | None = None
    payment_method_expected: PaymentMethod = PaymentMethod.PREPAID


@dataclass(frozen=True, slots=True)
class VerificationResult:
    stop: DeliveryStop
    attempt: VerificationAttempt
    authorized: bool


class DoorstepService:
    def __init__(
        self,
        unit_of_work: DeliveryUnitOfWork,
        *,
        code_policy: DeliveryCodePolicy | None = None,
        id_policy: IdEvidencePolicy | None = None,
        delivery_code_key: str = "",
    ) -> None:
        self._uow = unit_of_work
        self._code_policy = code_policy or DeliveryCodePolicy()
        self._id_policy = id_policy or IdEvidencePolicy()
        self._code_key = delivery_code_key

    # ------------------------------------------------------------- manifest

    def open_manifest(
        self, *, driver_principal_id: UUID, hub_id: UUID
    ) -> DeliveryManifest:
        self._uow.begin()
        try:
            existing = self._uow.manifests.find_open_for_driver(driver_principal_id)
            if existing is not None:
                self._uow.commit()
                return existing
            manifest = DeliveryManifest(
                manifest_id=uuid4(),
                driver_principal_id=driver_principal_id,
                hub_id=hub_id,
                created_at=_now(),
            )
            self._uow.manifests.save(manifest)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return manifest

    def scan_onto_manifest(
        self, *, manifest_id: UUID, parcel: ParcelForManifest
    ) -> DeliveryStop:
        """The scan that transfers custody (v6.3 p.25, p.26).

        Not a plan: after this the driver is carrying the parcel, so it is recorded as
        custody rather than as an intention.
        """
        self._uow.begin()
        try:
            manifest = self._uow.manifests.get(manifest_id)
            if manifest is None:
                raise ManifestNotFound(str(manifest_id))
            if self._uow.stops.find_live_by_tracking_code(parcel.tracking_code) is not None:
                raise ParcelAlreadyOnAManifest(parcel.tracking_code)

            moment = _now()
            digest = None
            if parcel.delivery_code is not None:
                # Stored as a keyed digest bound to this stop. The code itself is never
                # written down here — it lives in the receiver's SMS.
                digest = hash_delivery_code(
                    parcel.delivery_code,
                    key=self._code_key,
                    stop_reference=parcel.tracking_code,
                )
            stop = DeliveryStop(
                stop_id=uuid4(),
                manifest_id=manifest_id,
                tracking_code=parcel.tracking_code,
                driver_principal_id=manifest.driver_principal_id,
                status=StopStatus.IN_CUSTODY,
                settings=parcel.settings,
                delivery_code_digest=digest,
                named_receiver=parcel.named_receiver,
                cod_amount=parcel.cod_amount,
                payment_method_expected=parcel.payment_method_expected,
                custody_taken_at=moment,
            )
            self._uow.stops.save(stop)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return stop

    # ------------------------------------------------------------- the round

    def set_off(self, *, stop_id: UUID, driver_principal_id: UUID) -> DeliveryStop:
        """DRV-L01 — the receiver is told before the driver sets off, with an ETA."""
        return self._advance(
            stop_id=stop_id,
            driver_principal_id=driver_principal_id,
            target=StopStatus.EN_ROUTE,
            apply=lambda stop, moment: setattr(stop, "departed_at", moment),
        )

    def record_arrival(self, *, stop_id: UUID, driver_principal_id: UUID) -> DeliveryStop:
        return self._advance(
            stop_id=stop_id,
            driver_principal_id=driver_principal_id,
            target=StopStatus.ARRIVED,
            apply=lambda stop, moment: setattr(stop, "arrived_at", moment),
        )

    def start_wait(self, *, stop_id: UUID, driver_principal_id: UUID) -> DeliveryStop:
        """v6.3 p.26 — the ten minutes begin when the driver starts them."""
        return self._advance(
            stop_id=stop_id,
            driver_principal_id=driver_principal_id,
            target=StopStatus.WAITING,
            apply=lambda stop, moment: setattr(stop, "wait_started_at", moment),
        )

    def wait_remaining_seconds(
        self, *, stop_id: UUID, moment: datetime | None = None
    ) -> int:
        stop = self.get_stop(stop_id)
        if stop.wait_started_at is None:
            return DOOR_WAIT_SECONDS
        return max(0, DOOR_WAIT_SECONDS - stop.wait_elapsed_seconds(moment or _now()))

    def assert_wait_is_over(self, *, stop: DeliveryStop, moment: datetime) -> None:
        """The guard behind the disabled button in the app."""
        if stop.wait_started_at is None:
            raise WaitNotStarted()
        if not stop.wait_is_over(moment):
            raise WaitNotOver(
                DOOR_WAIT_SECONDS - stop.wait_elapsed_seconds(moment)
            )

    # ------------------------------------------------------------- verification

    def verify_with_code(
        self, *, stop_id: UUID, driver_principal_id: UUID, code: str
    ) -> VerificationResult:
        """v6.3 p.26 — anyone holding the code may receive the parcel.

        The code is compared as a keyed digest and never stored, logged or echoed. The
        failure message says nothing about the real code.
        """
        length = self._code_policy.assert_decided()

        self._uow.begin()
        try:
            stop = self._load_stop(stop_id, driver_principal_id)
            if stop.delivery_code_digest is None:
                raise DeliveryCodeNotSet(stop.tracking_code)
            if stop.status not in {StopStatus.ARRIVED, StopStatus.WAITING}:
                raise StopTransitionNotAllowed(
                    stop.status.value, StopStatus.AUTHORIZED.value
                )
            if stop.code_attempt_count >= self._code_policy.max_attempts:
                raise TooManyCodeAttempts()

            moment = _now()
            candidate = code.strip()
            correct = len(candidate) == length and candidate.isdigit() and verify_delivery_code(
                candidate=candidate,
                expected_digest=stop.delivery_code_digest,
                key=self._code_key,
                stop_reference=stop.tracking_code,
            )
            stop.code_attempt_count += 1
            attempt = VerificationAttempt(
                attempt_id=uuid4(),
                stop_id=stop.stop_id,
                method=VerificationMethod.DELIVERY_CODE,
                outcome=(
                    VerificationOutcome.VERIFIED
                    if correct
                    else VerificationOutcome.CODE_MISMATCH
                ),
                attempted_at=moment,
                attempted_by_actor_id=driver_principal_id,
            )
            self._uow.verifications.save(attempt)

            if correct:
                stop.status = StopStatus.AUTHORIZED
                stop.verified_at = moment
                stop.verified_by_method = VerificationMethod.DELIVERY_CODE
            stop.version += 1
            self._uow.stops.save(stop)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise

        if not correct:
            # Committed first: the attempt counter must survive the rejection, or the
            # limit could be reset by simply guessing again.
            raise DeliveryCodeIncorrect(
                max(0, self._code_policy.max_attempts - stop.code_attempt_count)
            )
        return VerificationResult(stop=stop, attempt=attempt, authorized=True)

    def verify_with_named_receiver_id(
        self,
        *,
        stop_id: UUID,
        driver_principal_id: UUID,
        id_matches_named_receiver: bool,
        id_photo: EvidenceMediaRef | None = None,
    ) -> VerificationResult:
        """v6.3 p.26 — the fallback, and only for the receiver the merchant named.

        ``id_photo`` is refused while the retention contradiction stands (DRV-L07): the
        Driver App says the ID is checked and not stored, and that is the reversible half
        of the disagreement.
        """
        if id_photo is not None:
            self._id_policy.assert_may_retain_photo()

        self._uow.begin()
        try:
            stop = self._load_stop(stop_id, driver_principal_id)
            if stop.named_receiver is None:
                raise NoNamedReceiverOnThisParcel(stop.tracking_code)
            if stop.status not in {StopStatus.ARRIVED, StopStatus.WAITING}:
                raise StopTransitionNotAllowed(
                    stop.status.value, StopStatus.AUTHORIZED.value
                )

            moment = _now()
            attempt = VerificationAttempt(
                attempt_id=uuid4(),
                stop_id=stop.stop_id,
                method=VerificationMethod.NAMED_RECEIVER_ID,
                outcome=(
                    VerificationOutcome.VERIFIED
                    if id_matches_named_receiver
                    else VerificationOutcome.ID_MISMATCH
                ),
                attempted_at=moment,
                attempted_by_actor_id=driver_principal_id,
                id_matched_named_receiver=id_matches_named_receiver,
            )
            self._uow.verifications.save(attempt)

            if id_matches_named_receiver:
                stop.status = StopStatus.AUTHORIZED
                stop.verified_at = moment
                stop.verified_by_method = VerificationMethod.NAMED_RECEIVER_ID
                stop.version += 1
                self._uow.stops.save(stop)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise

        if not id_matches_named_receiver:
            raise IdFallbackIsOnlyForTheNamedReceiver()
        return VerificationResult(stop=stop, attempt=attempt, authorized=True)

    # ------------------------------------------------------------- seal

    def check_parcel_seal(
        self, *, stop_id: UUID, driver_principal_id: UUID, intact: bool
    ) -> DeliveryStop:
        """The merchant's per-parcel seal, not the hub's batch seal (v6.3 p.14)."""
        self._uow.begin()
        try:
            stop = self._load_stop(stop_id, driver_principal_id)
            stop.seal_outcome = (
                SealCheckOutcome.INTACT if intact else SealCheckOutcome.BROKEN
            )
            stop.version += 1
            self._uow.stops.save(stop)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        if not intact:
            # The parcel stays in HUDHUD custody and the driver reports it.
            raise BrokenSealStopsTheHandover()
        return stop

    # ------------------------------------------------------------- photos

    def attach_photo(
        self,
        *,
        stop_id: UUID,
        driver_principal_id: UUID,
        stage: PhotoStage,
        media: EvidenceMediaRef,
    ) -> PhotoEvidence:
        """v6.3 p.14 — without the add-on, no photo is taken at any stage."""
        self._uow.begin()
        try:
            stop = self._load_stop(stop_id, driver_principal_id)
            if not stop.settings.photo_documentation:
                raise PhotographyNotEnabled()
            photo = PhotoEvidence(
                photo_id=uuid4(),
                stop_id=stop.stop_id,
                stage=stage,
                media=media,
                captured_at=_now(),
                captured_by_actor_id=driver_principal_id,
            )
            self._uow.photos.save(photo)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return photo

    # ------------------------------------------------------------- inspection

    def record_inspection(
        self,
        *,
        stop_id: UUID,
        driver_principal_id: UUID,
        outcome: InspectionOutcome,
    ) -> DeliveryStop:
        """Open-box only when the merchant enabled it (v6.3 p.35, p.36)."""
        self._uow.begin()
        try:
            stop = self._load_stop(stop_id, driver_principal_id)
            if stop.status is not StopStatus.AUTHORIZED:
                raise StopTransitionNotAllowed(stop.status.value, "inspected")
            if (
                outcome
                in {InspectionOutcome.OPEN_BOX_KEPT, InspectionOutcome.OPEN_BOX_REFUSED}
                and not stop.settings.open_box_allowed
            ):
                raise OpenBoxNotAllowed()
            if stop.requires_seal_check and stop.seal_outcome is None:
                raise SealMustBeCheckedFirst()
            if (
                outcome is not InspectionOutcome.SEALED_ACCEPTED
                and stop.requires_photo_before_opening
                and not self._has_photo(stop.stop_id, PhotoStage.BEFORE_OPENING)
            ):
                raise PhotoRequiredBeforeOpening()

            stop.inspection_outcome = outcome
            stop.version += 1
            self._uow.stops.save(stop)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return stop

    def assert_ready_for_payment(self, *, stop: DeliveryStop) -> None:
        """Everything the door requires before money changes hands.

        Opens its own transaction to read the photo evidence. Callers that are already
        inside one pass the photos they have read to :func:`check_ready_for_payment`.
        """
        self._uow.begin()
        try:
            photos = self._uow.photos.list_for_stop(stop.stop_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        check_ready_for_payment(stop=stop, photos=photos)

    # ------------------------------------------------------------- queries

    def manifest(self, manifest_id: UUID) -> DeliveryManifest:
        self._uow.begin()
        try:
            found = self._uow.manifests.get(manifest_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        if found is None:
            raise ManifestNotFound(str(manifest_id))
        return found

    def get_stop(self, stop_id: UUID) -> DeliveryStop:
        self._uow.begin()
        try:
            stop = self._uow.stops.get(stop_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        if stop is None:
            raise StopNotFound(str(stop_id))
        return stop

    def list_for_driver(self, *, driver_principal_id: UUID) -> tuple[DeliveryStop, ...]:
        self._uow.begin()
        try:
            found = self._uow.stops.list_for_driver(driver_principal_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    def verifications_for(self, *, stop_id: UUID) -> tuple[VerificationAttempt, ...]:
        self._uow.begin()
        try:
            found = self._uow.verifications.list_for_stop(stop_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    @property
    def code_policy(self) -> DeliveryCodePolicy:
        return self._code_policy

    @property
    def id_policy(self) -> IdEvidencePolicy:
        return self._id_policy

    # ------------------------------------------------------------- internals

    def _load_stop(self, stop_id: UUID, driver_principal_id: UUID) -> DeliveryStop:
        stop = self._uow.stops.get(stop_id)
        if stop is None:
            raise StopNotFound(str(stop_id))
        if stop.driver_principal_id != driver_principal_id:
            raise NotThisDriversStop(stop.tracking_code)
        return stop

    def _has_photo(self, stop_id: UUID, stage: PhotoStage) -> bool:
        return any(
            photo.stage is stage for photo in self._uow.photos.list_for_stop(stop_id)
        )

    def _advance(self, *, stop_id, driver_principal_id, target, apply) -> DeliveryStop:
        self._uow.begin()
        try:
            stop = self._load_stop(stop_id, driver_principal_id)
            if not stop.can_transition_to(target):
                raise StopTransitionNotAllowed(stop.status.value, target.value)
            moment = _now()
            stop.status = target
            apply(stop, moment)
            stop.version += 1
            self._uow.stops.save(stop)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return stop


def check_ready_for_payment(
    *, stop: DeliveryStop, photos: Sequence[PhotoEvidence]
) -> None:
    """Everything the door requires before money changes hands, as a pure decision.

    Taking the photos as an argument rather than reading them keeps this callable from
    inside an open transaction, which is where the handover needs it.
    """
    if stop.status is not StopStatus.AUTHORIZED:
        raise StopTransitionNotAllowed(stop.status.value, "paid")
    if stop.requires_seal_check and stop.seal_outcome is None:
        raise SealMustBeCheckedFirst()
    if stop.inspection_outcome is None:
        raise StopTransitionNotAllowed(stop.status.value, "paid")
    if (
        stop.inspection_outcome is InspectionOutcome.OPEN_BOX_KEPT
        and stop.requires_photo_after_inspection
        and not any(p.stage is PhotoStage.AFTER_INSPECTION for p in photos)
    ):
        raise PhotoRequiredAfterInspection()
