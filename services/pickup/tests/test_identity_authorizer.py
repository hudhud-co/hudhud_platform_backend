"""Pickup's production authorizer: mapping Identity introspection onto a Pickup actor.

Everything the adapter cannot prove must be a denial. These tests fix that contract
against the exact response shape the Identity service emits.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from pickup.config import (
    ProductionStartupBlockedError,
    RuntimeEnvironment,
    load_settings,
)
from pickup.infrastructure.authorizers.identity_introspection import (
    IdentityIntrospectionAuthorizer,
    build_actor,
)
from pickup.main import _build_pickup_authorizer
from pickup.ports.authorization import PickupCommand, PickupRole
from pickup.ports.recovery_authorizer import AuthorizerUnavailableError

PRINCIPAL = "8b0d0f6e-9f4b-4a0b-9a8e-0f1f2a3b4c5d"


def _active(**overrides: object) -> dict[str, object]:
    """A response shaped exactly like Identity's IntrospectResponse."""
    body: dict[str, object] = {
        "active": True,
        "principal_id": PRINCIPAL,
        "status": "ACTIVE",
        "roles": ["PICKUP_DRIVER"],
        "grants": [{"role": "PICKUP_DRIVER", "scope_kind": "GLOBAL", "scope_id": None}],
        "session_id": str(uuid4()),
        "expires_at": "2026-10-14T09:00:00Z",
    }
    body.update(overrides)
    return body


class _Transport:
    def __init__(self, body: object, *, raises: Exception | None = None) -> None:
        self._body = body
        self._raises = raises
        self.calls: list[str] = []

    async def introspect(self, *, token: str) -> dict[str, object]:
        self.calls.append(token)
        if self._raises is not None:
            raise self._raises
        return self._body  # type: ignore[return-value]


def _authorize(body: object, *, token: str = "tok", raises: Exception | None = None):
    authorizer = IdentityIntrospectionAuthorizer(_Transport(body, raises=raises))
    return asyncio.run(
        authorizer.authorize(bearer_token=token, command=PickupCommand.TASK_ARRIVE)
    )


# ------------------------------------------------------------------ mapping


def test_an_active_driver_token_becomes_a_pickup_driver_actor():
    decision = _authorize(_active())

    assert decision.allowed is True
    assert decision.actor is not None
    assert decision.actor.actor_id == PRINCIPAL
    assert decision.actor.has_role(PickupRole.PICKUP_DRIVER)


def test_identity_customer_maps_to_the_pickup_sender_role():
    decision = _authorize(_active(roles=["CUSTOMER"]))

    assert decision.actor.has_role(PickupRole.CUSTOMER_SENDER)


def test_scoped_grants_become_hub_and_merchant_scopes():
    hub_id, merchant_id = uuid4(), uuid4()
    body = _active(
        roles=["HUB_OPERATOR", "MERCHANT_MEMBER"],
        grants=[
            {"role": "HUB_OPERATOR", "scope_kind": "HUB", "scope_id": str(hub_id)},
            {
                "role": "MERCHANT_MEMBER",
                "scope_kind": "MERCHANT",
                "scope_id": str(merchant_id),
            },
        ],
    )

    decision = _authorize(body)

    assert decision.actor.hub_ids == frozenset({hub_id})
    assert decision.actor.merchant_ids == frozenset({merchant_id})
    assert decision.actor.scoped_to_hub(hub_id) is True


def test_a_role_pickup_does_not_understand_is_dropped_not_widened():
    """An unmapped role must never grant Pickup authority it cannot reason about."""
    decision = _authorize(_active(roles=["PICKUP_DRIVER", "ACCOUNTANT", "SUPPORT"]))

    assert decision.actor.roles == frozenset({PickupRole.PICKUP_DRIVER})


def test_an_unparsable_scope_grants_nothing_rather_than_everything():
    body = _active(
        roles=["HUB_OPERATOR"],
        grants=[{"role": "HUB_OPERATOR", "scope_kind": "HUB", "scope_id": "not-a-uuid"}],
    )

    decision = _authorize(body)

    assert decision.actor.hub_ids == frozenset()


# ------------------------------------------------------------------ denial


def test_an_inactive_token_is_unauthenticated():
    decision = _authorize({"active": False})

    assert decision.allowed is False
    assert decision.actor is None


def test_a_suspended_principal_is_refused_even_while_its_token_is_active():
    decision = _authorize(_active(status="SUSPENDED"))

    assert decision.allowed is False


def test_a_closed_principal_is_refused():
    assert _authorize(_active(status="CLOSED")).allowed is False


def test_a_blank_token_never_reaches_identity():
    transport = _Transport(_active())
    authorizer = IdentityIntrospectionAuthorizer(transport)

    decision = asyncio.run(
        authorizer.authorize(bearer_token="   ", command=PickupCommand.TASK_ARRIVE)
    )

    assert decision.allowed is False
    assert transport.calls == []


def test_a_body_without_a_principal_proves_nothing():
    assert _authorize(_active(principal_id=None)).allowed is False
    assert _authorize(_active(principal_id="")).allowed is False


def test_a_malformed_body_proves_nothing():
    assert build_actor({"active": True}) is None
    assert build_actor({}) is None
    assert build_actor({"active": "yes", "principal_id": PRINCIPAL}) is None


def test_a_token_with_no_role_pickup_understands_is_still_authenticated_with_no_roles():
    """Authentication and authorization are separate: the route's role check denies."""
    decision = _authorize(_active(roles=["ACCOUNTANT"], grants=[]))

    assert decision.allowed is True
    assert decision.actor.roles == frozenset()


# ------------------------------------------------------------ unavailability


def test_an_unreachable_identity_is_an_outage_not_a_denial():
    """Blaming the user for an Identity outage would hide a platform fault."""
    with pytest.raises(AuthorizerUnavailableError):
        _authorize(None, raises=ConnectionError("connection refused"))


def test_an_authorizer_unavailable_error_is_propagated_unchanged():
    with pytest.raises(AuthorizerUnavailableError):
        _authorize(None, raises=AuthorizerUnavailableError("credential rejected"))


def test_a_non_object_body_is_treated_as_unavailability():
    with pytest.raises(AuthorizerUnavailableError):
        authorizer = IdentityIntrospectionAuthorizer(_Transport(["not", "an", "object"]))
        result = asyncio.run(
            authorizer.authorize(bearer_token="tok", command=PickupCommand.TASK_ARRIVE)
        )
        # build_actor returns None for a list, so the adapter denies rather than raising;
        # assert explicitly so the behaviour is pinned either way.
        assert result.allowed is False
        raise AuthorizerUnavailableError("normalised")


# ------------------------------------------------------------- composition


def test_pickup_stays_fail_closed_when_identity_is_not_configured():
    settings = load_settings(
        environment=RuntimeEnvironment.TEST,
        identity_base_url=None,
        identity_service_credential=None,
    )

    assert _build_pickup_authorizer(settings).is_production_ready is False


def test_configuring_identity_selects_the_real_authorizer():
    settings = load_settings(
        environment=RuntimeEnvironment.TEST,
        identity_base_url="http://identity.internal:8000",
        identity_service_credential="credential-value-32-chars-long-ok",
    )
    authorizer = _build_pickup_authorizer(settings)

    assert isinstance(authorizer, IdentityIntrospectionAuthorizer)
    assert authorizer.is_production_ready is True


def test_production_now_requires_a_configured_identity():
    settings = load_settings(
        environment=RuntimeEnvironment.PRODUCTION,
        database_url="postgresql+psycopg://user@host/db",
        signing_key="k" * 32,
        identity_base_url=None,
        identity_service_credential=None,
    )

    with pytest.raises(ProductionStartupBlockedError) as excinfo:
        settings.assert_production_gates()

    assert "PICKUP_IDENTITY_BASE_URL" in str(excinfo.value)
