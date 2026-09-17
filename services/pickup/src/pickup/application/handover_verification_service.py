"""Sender→courier handover ceremony: dynamic challenge and parcel manifest.

The ceremony proves two independent facts before custody may start:

* the sender handed the parcel to the **assigned** courier (challenge), and
* the sender confirmed **which** parcel was handed over (manifest digest).

The assigned courier can never satisfy the sender half, a challenge is single use, and
any change to the assignment invalidates both halves.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from pickup.application.history import record_history
from pickup.domain.entities import PickupTask
from pickup.domain.errors import (
    ActingDriverMismatch,
    CourierChallengeAlreadyUsed,
    CourierChallengeExpired,
    CourierChallengeInvalidated,
    CourierChallengeIssueBlocked,
    CourierChallengeNotFound,
    CourierManifestMismatch,
    CourierManifestNotSubmitted,
    CourierManifestRequired,
    CourierSecretInvalid,
    CourierVerificationRateLimited,
    CourierVerificationRequired,
    PickupTaskAlreadyAccepted,
    PickupTaskNotFound,
    SenderMayNotVerifyOwnCourier,
)
from pickup.domain.handover import (
    CourierChallenge,
    CourierChallengeStatus,
    CourierManifest,
    CourierManifestStatus,
    SenderType,
    VerificationInvalidationReason,
    VerificationMethod,
)
from pickup.domain.security import (
    assignment_fingerprint,
    build_challenge_payload,
    build_manifest_digest,
    extract_challenge_secret,
    generate_challenge_secret,
    hash_challenge_secret,
    verify_challenge_secret,
)
from pickup.domain.value_objects import AssignmentState, PickupTaskStatus
from pickup.ports.authorization import PickupActor, PickupRole
from pickup.ports.repository import HandoverUnitOfWork

CHALLENGE_ELIGIBLE_TASK_STATUSES: frozenset[PickupTaskStatus] = frozenset(
    {
        PickupTaskStatus.PENDING,
        PickupTaskStatus.ARRIVED,
        PickupTaskStatus.SCANNED,
        PickupTaskStatus.PROOF_CAPTURED,
    }
)


@dataclass(frozen=True, slots=True)
class VerificationPolicy:
    """Timing and lockout policy for the handover ceremony."""

    challenge_ttl_seconds: int = 180
    verification_valid_seconds: int = 900
    confirmation_valid_seconds: int = 900
    max_failed_attempts: int = 5
    lockout_seconds: int = 300


@dataclass(frozen=True, slots=True)
class IssueChallengeCommand:
    pickup_task_id: UUID
    acting_driver_user_id: str
    occurred_at: datetime
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class IssuedChallenge:
    challenge_id: UUID
    pickup_task_id: UUID
    payload: str
    expires_at: datetime
    ttl_seconds: int
    status: CourierChallengeStatus
    reissued: bool


@dataclass(frozen=True, slots=True)
class VerifyChallengeCommand:
    pickup_task_id: UUID
    presented_payload: str
    occurred_at: datetime
    challenge_id: UUID | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class VerificationResult:
    challenge_id: UUID
    pickup_task_id: UUID
    status: CourierChallengeStatus
    courier_user_id: str
    verified_at: datetime
    valid_until: datetime
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class SubmitManifestCommand:
    pickup_task_id: UUID
    acting_driver_user_id: str
    scanned_identifier: str
    occurred_at: datetime
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class ConfirmManifestCommand:
    pickup_task_id: UUID
    occurred_at: datetime
    expected_manifest_digest: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class ManifestResult:
    manifest: CourierManifest
    replayed: bool = False


class CourierHandoverVerificationService:
    """Issue, verify, submit, and confirm the sender handover ceremony."""

    def __init__(
        self,
        unit_of_work: HandoverUnitOfWork,
        *,
        signing_key: str,
        policy: VerificationPolicy | None = None,
    ) -> None:
        self._uow = unit_of_work
        self._signing_key = signing_key
        self._policy = policy or VerificationPolicy()

    # ---------------------------------------------------------------- challenge

    def issue_challenge(
        self, command: IssueChallengeCommand, *, actor: PickupActor
    ) -> IssuedChallenge:
        self._uow.begin()
        try:
            task = self._load_task(command.pickup_task_id)
            if task.assigned_driver_user_id != command.acting_driver_user_id:
                raise ActingDriverMismatch(
                    pickup_task_id=str(task.pickup_task_id),
                    acting_driver_user_id=command.acting_driver_user_id,
                )
            self._assert_ceremony_open(task)
            if task.status not in CHALLENGE_ELIGIBLE_TASK_STATUSES:
                raise CourierChallengeIssueBlocked(
                    pickup_task_id=str(task.pickup_task_id),
                    detail=f"task status {task.status.value} is not eligible",
                )
            if task.assignment_state is not AssignmentState.ACKNOWLEDGED:
                raise CourierChallengeIssueBlocked(
                    pickup_task_id=str(task.pickup_task_id),
                    detail="assignment must be acknowledged first",
                )

            open_challenges = self._uow.challenges.list_open_for_task(task.pickup_task_id)
            live_verified = [item for item in open_challenges if item.is_live_verified]
            if live_verified:
                # Reissue must never let a courier silently restart a completed ceremony.
                raise CourierChallengeIssueBlocked(
                    pickup_task_id=str(task.pickup_task_id),
                    detail="sender verification is already complete",
                )

            superseded = 0
            for stale in open_challenges:
                if stale.status is not CourierChallengeStatus.ISSUED:
                    continue
                stale.status = CourierChallengeStatus.INVALIDATED
                stale.invalidated_at = command.occurred_at
                stale.invalidation_reason = VerificationInvalidationReason.REISSUED
                self._uow.challenges.save_challenge(stale)
                superseded += 1

            secret = generate_challenge_secret()
            challenge_id = uuid4()
            challenge = CourierChallenge(
                challenge_id=challenge_id,
                pickup_task_id=task.pickup_task_id,
                shipment_id=task.shipment_id,
                assigned_driver_user_id=task.assigned_driver_user_id,
                sender_type=SenderType.MERCHANT,
                assignment_fingerprint=assignment_fingerprint(
                    pickup_task_id=task.pickup_task_id,
                    assigned_driver_user_id=task.assigned_driver_user_id,
                    assigned_batch_id=task.assigned_batch_id,
                ),
                secret_hash=hash_challenge_secret(
                    secret,
                    challenge_id=challenge_id,
                    signing_key=self._signing_key,
                ),
                method=VerificationMethod.DYNAMIC_CHALLENGE_V1,
                status=CourierChallengeStatus.ISSUED,
                issued_by_user_id=actor.actor_id,
                issued_at=command.occurred_at,
                expires_at=command.occurred_at
                + timedelta(seconds=self._policy.challenge_ttl_seconds),
            )
            self._uow.challenges.save_challenge(challenge)
            record_history(
                self._uow.task_history,
                pickup_task_id=task.pickup_task_id,
                action="courier_challenge_reissued" if superseded else "courier_challenge_issued",
                actor=actor,
                previous_status=task.status.value,
                new_status=task.status.value,
                occurred_at=command.occurred_at,
                request_id=command.request_id,
                details={
                    "challenge_id": str(challenge_id),
                    "superseded_challenges": superseded,
                    "expires_at": challenge.expires_at.isoformat(),
                    "method": challenge.method.value,
                },
            )
            issued = IssuedChallenge(
                challenge_id=challenge_id,
                pickup_task_id=task.pickup_task_id,
                payload=build_challenge_payload(secret=secret),
                expires_at=challenge.expires_at,
                ttl_seconds=self._policy.challenge_ttl_seconds,
                status=challenge.status,
                reissued=bool(superseded),
            )
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        return issued

    def verify_challenge(
        self, command: VerifyChallengeCommand, *, actor: PickupActor
    ) -> VerificationResult:
        """Sender-side verification. Attempt counters and expiry persist even on reject."""
        self._uow.begin()
        deferred: Exception | None = None
        result: VerificationResult | None = None
        try:
            task = self._load_task(command.pickup_task_id)
            if actor.actor_id == task.assigned_driver_user_id:
                raise SenderMayNotVerifyOwnCourier(pickup_task_id=str(task.pickup_task_id))
            challenge = self._load_challenge(task, command.challenge_id)

            if challenge.status is CourierChallengeStatus.VERIFIED:
                result = self._replay_verification(challenge, task, actor)
            elif challenge.status is CourierChallengeStatus.CONSUMED:
                raise CourierChallengeAlreadyUsed(challenge_id=str(challenge.challenge_id))
            elif challenge.status is CourierChallengeStatus.INVALIDATED:
                raise CourierChallengeInvalidated(
                    challenge_id=str(challenge.challenge_id),
                    reason=(
                        challenge.invalidation_reason.value
                        if challenge.invalidation_reason
                        else None
                    ),
                )
            elif challenge.status is not CourierChallengeStatus.ISSUED:
                raise CourierChallengeExpired(challenge_id=str(challenge.challenge_id))
            elif (
                challenge.locked_until is not None
                and challenge.locked_until > command.occurred_at
            ):
                raise CourierVerificationRateLimited(
                    challenge_id=str(challenge.challenge_id),
                    locked_until=challenge.locked_until.isoformat(),
                )
            elif challenge.expires_at <= command.occurred_at:
                challenge.status = CourierChallengeStatus.EXPIRED
                self._uow.challenges.save_challenge(challenge)
                deferred = CourierChallengeExpired(challenge_id=str(challenge.challenge_id))
            elif challenge.assigned_driver_user_id != task.assigned_driver_user_id:
                raise CourierChallengeInvalidated(
                    challenge_id=str(challenge.challenge_id),
                    reason=VerificationInvalidationReason.REASSIGNED.value,
                )
            else:
                result = self._attempt_verification(challenge, task, actor, command)
                if result is None:
                    deferred = CourierSecretInvalid(challenge_id=str(challenge.challenge_id))
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        if deferred is not None:
            raise deferred
        assert result is not None
        return result

    def _attempt_verification(
        self,
        challenge: CourierChallenge,
        task: PickupTask,
        actor: PickupActor,
        command: VerifyChallengeCommand,
    ) -> VerificationResult | None:
        """Return the result on success; register the failed attempt and return None."""
        presented = extract_challenge_secret(command.presented_payload)
        verified = presented is not None and verify_challenge_secret(
            presented,
            challenge_id=challenge.challenge_id,
            expected_hash=challenge.secret_hash,
            signing_key=self._signing_key,
        )
        if not verified:
            self._register_failed_attempt(challenge, actor, command)
            return None

        valid_until = command.occurred_at + timedelta(
            seconds=self._policy.verification_valid_seconds
        )
        challenge.status = CourierChallengeStatus.VERIFIED
        challenge.verified_at = command.occurred_at
        challenge.verification_valid_until = valid_until
        challenge.verifier_user_id = actor.actor_id
        challenge.sender_type = _sender_type_for(actor)
        challenge.failed_attempt_count = 0
        challenge.locked_until = None
        self._uow.challenges.save_challenge(challenge)
        record_history(
            self._uow.task_history,
            pickup_task_id=task.pickup_task_id,
            action="courier_verified",
            actor=actor,
            previous_status=task.status.value,
            new_status=task.status.value,
            occurred_at=command.occurred_at,
            request_id=command.request_id,
            details={
                "challenge_id": str(challenge.challenge_id),
                "courier_user_id": challenge.assigned_driver_user_id,
                "sender_type": challenge.sender_type.value,
                "valid_until": valid_until.isoformat(),
            },
        )
        return VerificationResult(
            challenge_id=challenge.challenge_id,
            pickup_task_id=task.pickup_task_id,
            status=challenge.status,
            courier_user_id=challenge.assigned_driver_user_id,
            verified_at=command.occurred_at,
            valid_until=valid_until,
        )

    # ----------------------------------------------------------------- manifest

    def submit_manifest(
        self, command: SubmitManifestCommand, *, actor: PickupActor
    ) -> ManifestResult:
        identifier = (command.scanned_identifier or "").strip()
        if not identifier:
            raise CourierManifestMismatch(pickup_task_id=str(command.pickup_task_id))
        self._uow.begin()
        try:
            task = self._load_task(command.pickup_task_id)
            if task.assigned_driver_user_id != command.acting_driver_user_id:
                raise ActingDriverMismatch(
                    pickup_task_id=str(task.pickup_task_id),
                    acting_driver_user_id=command.acting_driver_user_id,
                )
            self._assert_ceremony_open(task)
            if task.scanned_identifier and task.scanned_identifier != identifier:
                raise CourierManifestMismatch(pickup_task_id=str(task.pickup_task_id))

            digest = build_manifest_digest(
                shipment_id=task.shipment_id,
                scanned_identifier=identifier,
            )
            open_manifests = self._uow.courier_manifests.list_open_for_task(task.pickup_task_id)
            for existing in open_manifests:
                if (
                    existing.manifest_digest == digest
                    and existing.status is CourierManifestStatus.SUBMITTED
                ):
                    result = ManifestResult(manifest=existing, replayed=True)
                    self._uow.commit()
                    return result

            invalidated = 0
            for existing in open_manifests:
                existing.status = CourierManifestStatus.INVALIDATED
                existing.invalidated_at = command.occurred_at
                existing.invalidation_reason = VerificationInvalidationReason.MANIFEST_MUTATED
                self._uow.courier_manifests.save_manifest(existing)
                invalidated += 1

            manifest = CourierManifest(
                manifest_id=uuid4(),
                pickup_task_id=task.pickup_task_id,
                shipment_id=task.shipment_id,
                manifest_digest=digest,
                status=CourierManifestStatus.SUBMITTED,
                submitted_by_user_id=actor.actor_id,
                submitted_at=command.occurred_at,
            )
            self._uow.courier_manifests.save_manifest(manifest)
            record_history(
                self._uow.task_history,
                pickup_task_id=task.pickup_task_id,
                action="courier_manifest_submitted",
                actor=actor,
                previous_status=task.status.value,
                new_status=task.status.value,
                occurred_at=command.occurred_at,
                request_id=command.request_id,
                details={
                    "manifest_id": str(manifest.manifest_id),
                    "manifest_digest": digest,
                    "invalidated_manifests": invalidated,
                },
            )
            result = ManifestResult(manifest=manifest)
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        return result

    def confirm_manifest(
        self, command: ConfirmManifestCommand, *, actor: PickupActor
    ) -> ManifestResult:
        self._uow.begin()
        try:
            task = self._load_task(command.pickup_task_id)
            if actor.actor_id == task.assigned_driver_user_id:
                raise SenderMayNotVerifyOwnCourier(pickup_task_id=str(task.pickup_task_id))
            self._assert_ceremony_open(task)

            challenge = self._live_verified_challenge(task, command.occurred_at)
            if challenge is None:
                raise CourierVerificationRequired(
                    pickup_task_id=str(task.pickup_task_id),
                    detail="no live verified challenge",
                )

            open_manifests = self._uow.courier_manifests.list_open_for_task(task.pickup_task_id)
            confirmed = [
                item
                for item in open_manifests
                if item.status is CourierManifestStatus.CONFIRMED
            ]
            submitted = [
                item
                for item in open_manifests
                if item.status is CourierManifestStatus.SUBMITTED
            ]
            if confirmed:
                manifest = confirmed[0]
                if (
                    command.expected_manifest_digest
                    and command.expected_manifest_digest != manifest.manifest_digest
                ):
                    raise CourierManifestMismatch(pickup_task_id=str(task.pickup_task_id))
                result = ManifestResult(manifest=manifest, replayed=True)
                self._uow.commit()
                return result
            if not submitted:
                raise CourierManifestNotSubmitted(pickup_task_id=str(task.pickup_task_id))

            manifest = submitted[0]
            if (
                command.expected_manifest_digest
                and command.expected_manifest_digest != manifest.manifest_digest
            ):
                raise CourierManifestMismatch(pickup_task_id=str(task.pickup_task_id))

            manifest.status = CourierManifestStatus.CONFIRMED
            manifest.confirmed_by_user_id = actor.actor_id
            manifest.confirmed_at = command.occurred_at
            manifest.confirmation_valid_until = command.occurred_at + timedelta(
                seconds=self._policy.confirmation_valid_seconds
            )
            self._uow.courier_manifests.save_manifest(manifest)
            record_history(
                self._uow.task_history,
                pickup_task_id=task.pickup_task_id,
                action="courier_manifest_confirmed",
                actor=actor,
                previous_status=task.status.value,
                new_status=task.status.value,
                occurred_at=command.occurred_at,
                request_id=command.request_id,
                details={
                    "manifest_id": str(manifest.manifest_id),
                    "manifest_digest": manifest.manifest_digest,
                    "challenge_id": str(challenge.challenge_id),
                },
            )
            result = ManifestResult(manifest=manifest)
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        return result

    # ------------------------------------------------------------ acceptance gate

    def assert_ready_for_acceptance(
        self,
        *,
        task: PickupTask,
        scanned_identifier: str,
        now: datetime,
    ) -> tuple[CourierChallenge, CourierManifest]:
        """Fail closed unless a live verified challenge and confirmed manifest exist."""
        challenge = self._live_verified_challenge(task, now)
        if challenge is None:
            raise CourierVerificationRequired(
                pickup_task_id=str(task.pickup_task_id),
                detail="no live verified challenge",
            )
        if challenge.assigned_driver_user_id != task.assigned_driver_user_id:
            raise CourierVerificationRequired(
                pickup_task_id=str(task.pickup_task_id),
                detail="assignment changed after verification",
            )

        expected_digest = build_manifest_digest(
            shipment_id=task.shipment_id,
            scanned_identifier=scanned_identifier,
        )
        manifest = next(
            (
                item
                for item in self._uow.courier_manifests.list_open_for_task(task.pickup_task_id)
                if item.status is CourierManifestStatus.CONFIRMED
                and item.consumed_at is None
                and item.invalidated_at is None
            ),
            None,
        )
        if manifest is None:
            raise CourierManifestRequired(
                pickup_task_id=str(task.pickup_task_id),
                detail="no confirmed manifest",
            )
        if manifest.manifest_digest != expected_digest or manifest.shipment_id != task.shipment_id:
            raise CourierManifestMismatch(pickup_task_id=str(task.pickup_task_id))
        if (
            manifest.confirmation_valid_until is not None
            and manifest.confirmation_valid_until <= now
        ):
            raise CourierManifestRequired(
                pickup_task_id=str(task.pickup_task_id),
                detail="manifest confirmation expired",
            )
        return challenge, manifest

    def consume_for_acceptance(
        self,
        *,
        challenge: CourierChallenge,
        manifest: CourierManifest,
        now: datetime,
    ) -> None:
        """Burn both halves so one ceremony can authorize exactly one acceptance."""
        challenge.status = CourierChallengeStatus.CONSUMED
        challenge.consumed_at = now
        self._uow.challenges.save_challenge(challenge)
        manifest.status = CourierManifestStatus.CONSUMED
        manifest.consumed_at = now
        self._uow.courier_manifests.save_manifest(manifest)

    def invalidate_for_task(
        self,
        *,
        pickup_task_id: UUID,
        reason: VerificationInvalidationReason,
        now: datetime,
    ) -> tuple[tuple[UUID, ...], tuple[UUID, ...]]:
        """Invalidate the ceremony when the assignment behind it stops being true."""
        challenge_ids: list[UUID] = []
        for challenge in self._uow.challenges.list_open_for_task(pickup_task_id):
            if challenge.consumed_at is not None:
                continue
            challenge.status = CourierChallengeStatus.INVALIDATED
            challenge.invalidated_at = now
            challenge.invalidation_reason = reason
            self._uow.challenges.save_challenge(challenge)
            challenge_ids.append(challenge.challenge_id)

        manifest_ids: list[UUID] = []
        for manifest in self._uow.courier_manifests.list_open_for_task(pickup_task_id):
            if manifest.consumed_at is not None:
                continue
            manifest.status = CourierManifestStatus.INVALIDATED
            manifest.invalidated_at = now
            manifest.invalidation_reason = reason
            self._uow.courier_manifests.save_manifest(manifest)
            manifest_ids.append(manifest.manifest_id)
        return tuple(challenge_ids), tuple(manifest_ids)

    # ------------------------------------------------------------------ helpers

    def _load_task(self, pickup_task_id: UUID) -> PickupTask:
        task = self._uow.pickup_tasks.get_pickup_task(pickup_task_id)
        if task is None:
            raise PickupTaskNotFound(str(pickup_task_id))
        return task

    def _assert_ceremony_open(self, task: PickupTask) -> None:
        if task.is_accepted:
            assert task.acceptance_state is not None
            raise PickupTaskAlreadyAccepted(
                pickup_task_id=str(task.pickup_task_id),
                acceptance_state=task.acceptance_state.value,
            )
        if task.is_terminal:
            raise CourierChallengeIssueBlocked(
                pickup_task_id=str(task.pickup_task_id),
                detail=f"task status {task.status.value} is terminal",
            )

    def _load_challenge(
        self, task: PickupTask, challenge_id: UUID | None
    ) -> CourierChallenge:
        if challenge_id is not None:
            challenge = self._uow.challenges.get_challenge(challenge_id)
            if challenge is None or challenge.pickup_task_id != task.pickup_task_id:
                raise CourierChallengeNotFound(pickup_task_id=str(task.pickup_task_id))
            return challenge
        open_challenges = self._uow.challenges.list_open_for_task(task.pickup_task_id)
        issued = [
            item for item in open_challenges if item.status is CourierChallengeStatus.ISSUED
        ]
        if issued:
            return max(issued, key=lambda item: item.issued_at)
        verified = [item for item in open_challenges if item.is_live_verified]
        if verified:
            return max(verified, key=lambda item: item.issued_at)
        raise CourierChallengeNotFound(pickup_task_id=str(task.pickup_task_id))

    def _live_verified_challenge(
        self, task: PickupTask, now: datetime
    ) -> CourierChallenge | None:
        for challenge in self._uow.challenges.list_open_for_task(task.pickup_task_id):
            if not challenge.is_live_verified:
                continue
            if (
                challenge.verification_valid_until is not None
                and challenge.verification_valid_until <= now
            ):
                continue
            return challenge
        return None

    def _replay_verification(
        self,
        challenge: CourierChallenge,
        task: PickupTask,
        actor: PickupActor,
    ) -> VerificationResult:
        if challenge.consumed_at is not None or challenge.invalidated_at is not None:
            raise CourierChallengeAlreadyUsed(challenge_id=str(challenge.challenge_id))
        if challenge.verifier_user_id != actor.actor_id:
            # A second sender must not learn that a challenge is already verified.
            raise CourierChallengeAlreadyUsed(challenge_id=str(challenge.challenge_id))
        assert challenge.verified_at is not None
        assert challenge.verification_valid_until is not None
        return VerificationResult(
            challenge_id=challenge.challenge_id,
            pickup_task_id=task.pickup_task_id,
            status=challenge.status,
            courier_user_id=challenge.assigned_driver_user_id,
            verified_at=challenge.verified_at,
            valid_until=challenge.verification_valid_until,
            replayed=True,
        )

    def _register_failed_attempt(
        self,
        challenge: CourierChallenge,
        actor: PickupActor,
        command: VerifyChallengeCommand,
    ) -> None:
        challenge.failed_attempt_count += 1
        if challenge.failed_attempt_count >= self._policy.max_failed_attempts:
            challenge.locked_until = command.occurred_at + timedelta(
                seconds=self._policy.lockout_seconds
            )
        self._uow.challenges.save_challenge(challenge)
        record_history(
            self._uow.task_history,
            pickup_task_id=challenge.pickup_task_id,
            action="courier_verify_failed",
            actor=actor,
            previous_status=None,
            new_status=None,
            occurred_at=command.occurred_at,
            request_id=command.request_id,
            details={
                "challenge_id": str(challenge.challenge_id),
                "failed_attempt_count": challenge.failed_attempt_count,
                "locked_until": (
                    challenge.locked_until.isoformat() if challenge.locked_until else None
                ),
            },
        )


def _sender_type_for(actor: PickupActor) -> SenderType:
    if actor.has_role(PickupRole.MERCHANT_MEMBER):
        return SenderType.MERCHANT
    return SenderType.CUSTOMER_DIRECT
