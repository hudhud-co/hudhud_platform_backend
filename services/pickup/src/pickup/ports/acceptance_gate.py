"""Acceptance-time gate for the sender handover ceremony."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pickup.domain.entities import PickupTask
from pickup.domain.handover import CourierChallenge, CourierManifest


class AcceptanceVerificationGate(Protocol):
    """Proves the sender handed this parcel to this courier before custody starts."""

    def assert_ready_for_acceptance(
        self,
        *,
        task: PickupTask,
        scanned_identifier: str,
        now: datetime,
    ) -> tuple[CourierChallenge, CourierManifest]:
        """Return the live ceremony, or raise a Pickup domain error. Never returns None."""

    def consume_for_acceptance(
        self,
        *,
        challenge: CourierChallenge,
        manifest: CourierManifest,
        now: datetime,
    ) -> None:
        """Burn the ceremony so it cannot authorize a second acceptance."""
