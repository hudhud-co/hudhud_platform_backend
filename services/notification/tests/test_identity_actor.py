"""`build_actor` against the body Identity actually returns.

Every other test builds an actor through `FakeNotificationAuthorizer`, so the real
translation from an introspection body was never executed. It raised TypeError on the
first authenticated request, which every route reported as 500.

The body below is a verbatim capture from `POST /identity/tokens/introspect` at
platform SHA 573eac12d5f70b829667c00a3f724a3b623ac3eb.
"""

from __future__ import annotations

from uuid import UUID

from notification.infrastructure.authorizers.identity import build_actor
from notification.ports.authorization import NotificationRole

PRINCIPAL = "86aa7e7d-13d3-4a97-a116-24ff31b4b038"

IDENTITY_INTROSPECTION_BODY: dict[str, object] = {
    "active": True,
    "principal_id": PRINCIPAL,
    "status": "ACTIVE",
    "roles": ["CUSTOMER", "DELIVERY_DRIVER", "PICKUP_DRIVER"],
    "grants": [
        {"role": "CUSTOMER", "scope_kind": "GLOBAL", "scope_id": None},
        {"role": "PICKUP_DRIVER", "scope_kind": "GLOBAL", "scope_id": None},
        {"role": "DELIVERY_DRIVER", "scope_kind": "GLOBAL", "scope_id": None},
    ],
    "session_id": "c0256244-38fd-44ce-80c5-b1a08e90c1d8",
    "expires_at": "2026-10-15T16:01:27.425796Z",
}


def test_build_actor_accepts_the_real_introspection_body() -> None:
    actor = build_actor(IDENTITY_INTROSPECTION_BODY)

    assert actor is not None
    assert actor.principal_id == UUID(PRINCIPAL)
    assert actor.owns(UUID(PRINCIPAL))


def test_a_driver_is_not_granted_operations_or_support() -> None:
    actor = build_actor(IDENTITY_INTROSPECTION_BODY)

    assert actor is not None
    assert not actor.is_operations
    assert not actor.is_support
    assert NotificationRole.OPERATIONS not in actor.roles


def test_an_inactive_or_unparsable_body_yields_no_actor() -> None:
    assert build_actor({**IDENTITY_INTROSPECTION_BODY, "active": False}) is None
    assert build_actor({**IDENTITY_INTROSPECTION_BODY, "status": "SUSPENDED"}) is None
    assert build_actor({**IDENTITY_INTROSPECTION_BODY, "principal_id": "not-a-uuid"}) is None
    assert build_actor({}) is None
