"""Translating an Identity introspection body into a Merchant actor.

Merchant's other tests use a fake authorizer, so this translation was uncovered. Against
the real dev stack a merchant owner holding a `MERCHANT`-scoped grant arrived with no
merchant reach at all, so every merchant-scoped route refused them.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from merchant.infrastructure.authorizers.identity import build_actor
from merchant.ports.authorization import MerchantRole

PRINCIPAL = "cc2a1f63-af18-43c1-a969-04a842695bd1"


def _body(**overrides) -> dict:
    """An introspection body exactly as Identity returns it (captured live)."""
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


def test_a_merchant_scoped_grant_becomes_merchant_reach() -> None:
    """Regression: this returned an empty set, so an owner was refused their own store."""
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
    assert actor.merchant_ids == frozenset({merchant})


def test_owning_several_stores_keeps_every_scope() -> None:
    """Customer App v3 `yourStores` lists more than one owned store."""
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
    assert actor.merchant_ids == frozenset({first, second})


def test_a_plain_customer_has_no_merchant_reach() -> None:
    actor = build_actor(_body())
    assert actor is not None
    assert actor.principal_id == UUID(PRINCIPAL)
    assert actor.merchant_ids == frozenset()


def test_a_non_merchant_scope_is_not_merchant_reach() -> None:
    actor = build_actor(
        _body(
            grants=[
                {"role": "OPERATIONS", "scope_kind": "HUB", "scope_id": str(uuid4())}
            ]
        )
    )
    assert actor is not None
    assert actor.merchant_ids == frozenset()


def test_an_unparseable_scope_is_dropped() -> None:
    actor = build_actor(
        _body(
            grants=[
                {"role": "MERCHANT_OWNER", "scope_kind": "MERCHANT", "scope_id": "nope"}
            ]
        )
    )
    assert actor is not None
    assert actor.merchant_ids == frozenset()


def test_inactive_and_refused_statuses_yield_no_actor() -> None:
    assert build_actor(_body(active=False)) is None
    assert build_actor(_body(status="SUSPENDED")) is None


def test_an_unknown_role_is_dropped_not_trusted() -> None:
    actor = build_actor(_body(roles=["CUSTOMER", "GALACTIC_OVERSEER"]))
    assert actor is not None
    assert actor.roles == frozenset({MerchantRole.CUSTOMER})
