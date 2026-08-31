"""Reusable conformance vectors for service-owned adapter implementations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ConformanceVectorId(StrEnum):
    C1 = "C1"
    C2 = "C2"
    C3 = "C3"
    C4 = "C4"
    C5 = "C5"
    C6 = "C6"
    C7 = "C7"
    C8 = "C8"
    C9 = "C9"
    C10 = "C10"


@dataclass(frozen=True, slots=True)
class ConformanceVector:
    """Behavioral vector future service adapters should satisfy."""

    vector_id: ConformanceVectorId
    description: str
    requires_postgresql: bool
    pure_decision_covered: bool


CONFORMANCE_VECTORS: tuple[ConformanceVector, ...] = (
    ConformanceVector(
        vector_id=ConformanceVectorId.C1,
        description="Domain rollback leaves zero outbox rows",
        requires_postgresql=True,
        pure_decision_covered=False,
    ),
    ConformanceVector(
        vector_id=ConformanceVectorId.C2,
        description="Outbox insert shares transaction with domain commit",
        requires_postgresql=True,
        pure_decision_covered=False,
    ),
    ConformanceVector(
        vector_id=ConformanceVectorId.C3,
        description="Two relay replicas do not double-publish without transport dedupe overlap",
        requires_postgresql=True,
        pure_decision_covered=False,
    ),
    ConformanceVector(
        vector_id=ConformanceVectorId.C4,
        description="Stale lease recovery requeues processing rows to pending",
        requires_postgresql=True,
        pure_decision_covered=True,
    ),
    ConformanceVector(
        vector_id=ConformanceVectorId.C5,
        description="Publish broker ACK transitions outbox row to published exactly once in store",
        requires_postgresql=True,
        pure_decision_covered=True,
    ),
    ConformanceVector(
        vector_id=ConformanceVectorId.C6,
        description="Duplicate (consumer_name, event_id) does not double handler side effects",
        requires_postgresql=True,
        pure_decision_covered=True,
    ),
    ConformanceVector(
        vector_id=ConformanceVectorId.C7,
        description="Crash after processed inbox before JetStream ACK redelivers to processed ACK",
        requires_postgresql=True,
        pure_decision_covered=True,
    ),
    ConformanceVector(
        vector_id=ConformanceVectorId.C8,
        description="Poison handler marks inbox quarantined with JetStream ACK semantics",
        requires_postgresql=True,
        pure_decision_covered=True,
    ),
    ConformanceVector(
        vector_id=ConformanceVectorId.C9,
        description="Oversized payload rejected before publish",
        requires_postgresql=False,
        pure_decision_covered=False,
    ),
    ConformanceVector(
        vector_id=ConformanceVectorId.C10,
        description="last_error columns contain no JWT/phone/API-key patterns",
        requires_postgresql=True,
        pure_decision_covered=True,
    ),
)


def vectors_requiring_postgresql() -> tuple[ConformanceVector, ...]:
    return tuple(vector for vector in CONFORMANCE_VECTORS if vector.requires_postgresql)


def vectors_with_pure_decision_coverage() -> tuple[ConformanceVector, ...]:
    return tuple(vector for vector in CONFORMANCE_VECTORS if vector.pure_decision_covered)


def get_vector(vector_id: ConformanceVectorId | str) -> ConformanceVector:
    normalized = ConformanceVectorId(str(vector_id))
    for vector in CONFORMANCE_VECTORS:
        if vector.vector_id is normalized:
            return vector
    msg = f"unknown conformance vector: {vector_id}"
    raise KeyError(msg)
