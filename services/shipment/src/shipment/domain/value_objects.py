"""Value objects for order intent and acceptance scan."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Self

from shipment.domain.errors import InlineMediaNotAllowed

_BASE64_LIKE = re.compile(r"^[A-Za-z0-9+/=\s]{256,}$")


class ShipmentStatus(StrEnum):
    """Canonical shipment lifecycle status at the acceptance boundary."""

    CREATED = "CREATED"
    IN_CUSTODY = "IN_CUSTODY"


class CustodyType(StrEnum):
    """Custody holder type at acceptance."""

    DRIVER = "DRIVER"


class PickupTaskStatus(StrEnum):
    """Pickup task status prerequisite for acceptance."""

    PENDING = "PENDING"
    PROOF_CAPTURED = "PROOF_CAPTURED"


class PickupTaskAcceptanceState(StrEnum):
    """Pickup task acceptance outcome recorded with shipment acceptance."""

    ACCEPTED = "ACCEPTED"
    ACCEPTED_WITH_EXCEPTION = "ACCEPTED_WITH_EXCEPTION"
    REJECTED = "REJECTED"


class ShipmentEventType(StrEnum):
    """Immutable shipment timeline event types at acceptance."""

    ACCEPTANCE_SCAN = "ACCEPTANCE_SCAN"


class AcceptanceOutcome(StrEnum):
    """Acceptance scan outcome — source §5 Pickup and Acceptance Scan."""

    ACCEPTED = "accepted"
    ACCEPTED_WITH_EXCEPTION = "accepted_with_exception"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class WaybillIdentity:
    """Shipment/waybill identity recorded at acceptance."""

    waybill_number: str
    shipment_id: str


@dataclass(frozen=True, slots=True)
class PackagingSealAssessment:
    """Packaging and seal assessment captured during acceptance scan."""

    packaging_condition: str
    seal_assessment: str


@dataclass(frozen=True, slots=True)
class ApproximateParcelMetrics:
    """Approximate weight and dimensions when provided at acceptance."""

    weight_kg: Decimal | None = None
    length_cm: Decimal | None = None
    width_cm: Decimal | None = None
    height_cm: Decimal | None = None


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    """External evidence reference — never inline media bytes (source §6, §7)."""

    storage_uri: str
    captured_at: datetime | None
    location_label: str | None
    low_trust: bool
    low_trust_reasons: tuple[str, ...]

    @classmethod
    def from_reference(
        cls,
        storage_uri: str,
        *,
        captured_at: datetime | None = None,
        location_label: str | None = None,
    ) -> Self:
        normalized = _validate_storage_reference(storage_uri)
        reasons: list[str] = []
        if captured_at is None:
            reasons.append("missing_timestamp")
        if location_label is None:
            reasons.append("missing_location")
        return cls(
            storage_uri=normalized,
            captured_at=captured_at,
            location_label=location_label,
            low_trust=bool(reasons),
            low_trust_reasons=tuple(reasons),
        )


def _validate_storage_reference(storage_uri: str | bytes) -> str:
    if isinstance(storage_uri, bytes):
        raise InlineMediaNotAllowed("evidence reference must not contain inline media bytes")
    normalized = storage_uri.strip()
    if not normalized:
        raise InlineMediaNotAllowed("evidence reference must not be empty")
    lowered = normalized.lower()
    if lowered.startswith("data:"):
        raise InlineMediaNotAllowed("data URLs with inline media are not allowed")
    if _BASE64_LIKE.fullmatch(normalized):
        raise InlineMediaNotAllowed("inline base64 media payloads are not allowed")
    return normalized
