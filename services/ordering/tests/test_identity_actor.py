"""Translating an Identity introspection body into an Ordering actor.

This is the path every authenticated call takes in a real deployment. The service's
other tests use `FakeOrderingAuthorizer`, so nothing here was covered: the first real
HTTP call against the dev stack answered 500 for every authenticated route.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from ordering.infrastructure.authorizers.identity import build_actor
from ordering.ports.authorization import OrderingRole

PRINCIPAL = "cc2a1f63-af18-43c1-a969-04a842695bd1"


def _body(**overrides) -> dict:
    """An introspection body exactly as Identity returns it.

    Captured live from `POST /identity/tokens/introspect`:
    `{"active":true,"principal_id":...,"status":"ACTIVE","roles":["CUSTOMER"],
      "grants":[{"role":"CUSTOMER","scope_kind":"GLOBAL","scope_id":null}],
      "session_id":...,"expires_at":...}`
    """
    body = {
        "active": True,
        "principal_id": PRINCIPAL,
        "status": "ACTIVE",
        "roles": ["CUSTOMER"],
        "grants": [{"role": "CUSTOMER", "scope_kind": "GLOBAL", "scope_id": None}],
        "session_id": "39cd478d-2025-4345-94c8-cd168212d914",
        "expires_at": "2026-10-15T16:24:54.401246Z",
    }
    body.update(overrides)
    return body


def test_a_real_introspection_body_builds_an_actor() -> None:
    """Regression: this raised TypeError and surfaced as a 500 on every route."""
    actor = build_actor(_body())
    assert actor is not None
    assert actor.principal_id == UUID(PRINCIPAL)
    assert actor.has_role(OrderingRole.CUSTOMER)
    assert actor.merchant_ids == frozenset()


def test_a_merchant_scoped_grant_becomes_merchant_reach() -> None:
    merchant = uuid4()
    actor = build_actor(
        _body(
            roles=["MERCHANT_OWNER"],
            grants=[
                {
                    "role": "MERCHANT_OWNER",
                    "scope_kind": "MERCHANT",
                    "scope_id": str(merchant),
                }
            ],
        )
    )
    assert actor is not None
    assert actor.acts_for_merchant(merchant)


def test_a_global_grant_is_not_merchant_reach() -> None:
    actor = build_actor(_body())
    assert actor is not None
    assert not actor.acts_for_merchant(uuid4())


def test_two_merchant_scopes_are_both_honoured() -> None:
    """A principal may own more than one store — v3 `yourStores` lists several."""
    first, second = uuid4(), uuid4()
    actor = build_actor(
        _body(
            roles=["MERCHANT_OWNER"],
            grants=[
                {"role": "MERCHANT_OWNER", "scope_kind": "MERCHANT", "scope_id": str(first)},
                {"role": "MERCHANT_OWNER", "scope_kind": "MERCHANT", "scope_id": str(second)},
            ],
        )
    )
    assert actor is not None
    assert actor.acts_for_merchant(first)
    assert actor.acts_for_merchant(second)


def test_an_unparseable_scope_is_no_scope() -> None:
    actor = build_actor(
        _body(
            grants=[
                {"role": "MERCHANT_OWNER", "scope_kind": "MERCHANT", "scope_id": "not-a-uuid"}
            ]
        )
    )
    assert actor is not None
    assert actor.merchant_ids == frozenset()


def test_an_inactive_or_suspended_session_yields_no_actor() -> None:
    assert build_actor(_body(active=False)) is None
    assert build_actor(_body(status="SUSPENDED")) is None
    assert build_actor(_body(status="CLOSED")) is None


def test_an_unknown_role_is_dropped_not_trusted() -> None:
    actor = build_actor(_body(roles=["CUSTOMER", "GALACTIC_OVERSEER"]))
    assert actor is not None
    assert actor.roles == frozenset({OrderingRole.CUSTOMER})


def test_a_malformed_grants_entry_is_ignored() -> None:
    actor = build_actor(_body(grants=["not-a-dict", {"scope_kind": "MERCHANT"}]))
    assert actor is not None
    assert actor.merchant_ids == frozenset()
