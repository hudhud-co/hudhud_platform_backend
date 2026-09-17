"""Unauthenticated lookup by tracking code (CUS-08).

v6.3 p.20 texts the receiver a tracking link, and the app's `track` screen takes a code
with nobody signed in. That makes this the one endpoint in the platform a stranger can
reach with a guessable-looking identifier, so it is built to give away as little as
possible:

* it returns a status and a destination governorate — never a phone number, a name, a
  street address, a COD amount or a label code;
* it answers identically for an unknown code and a cancelled one, so the endpoint cannot
  be used to confirm that a code exists;
* the tracking code itself is random rather than sequential, so the space cannot be
  walked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ordering.domain.entities import ShipmentRequest
from ordering.domain.value_objects import RequestStatus, is_tracking_code
from ordering.ports.repository import OrderingUnitOfWork


@dataclass(frozen=True, slots=True)
class PublicTrackingView:
    """Everything a stranger holding the code may see."""

    tracking_code: str
    status: str
    destination_governorate: str
    created_at: datetime | None
    registered_at: datetime | None

    @staticmethod
    def of(request: ShipmentRequest) -> PublicTrackingView:
        return PublicTrackingView(
            tracking_code=request.tracking_code,
            status=request.status.value,
            destination_governorate=request.receiver.governorate,
            created_at=request.created_at,
            registered_at=request.registered_at,
        )


class PublicTrackingService:
    def __init__(self, unit_of_work: OrderingUnitOfWork) -> None:
        self._uow = unit_of_work

    def lookup(self, tracking_code: str) -> PublicTrackingView | None:
        """Return the public view, or ``None`` for anything that is not visible.

        ``None`` covers three cases on purpose — malformed code, unknown code, cancelled
        shipment — because distinguishing them would leak whether a code was ever issued.
        """
        candidate = tracking_code.strip().upper()
        if not is_tracking_code(candidate):
            return None
        self._uow.begin()
        try:
            request = self._uow.requests.find_by_tracking_code(candidate)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        if request is None or request.status is RequestStatus.CANCELLED:
            return None
        return PublicTrackingView.of(request)
