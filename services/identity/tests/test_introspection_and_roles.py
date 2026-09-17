"""Token introspection and role administration.

Introspection is the boundary every other service authenticates through, so its failure
modes matter more than its happy path: a revoked token, an expired token, a token replayed
from another device and a token presented by an unauthenticated caller must all fail.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from identity_fixtures import (
    BASE_TIME,
    OTHER_PHONE,
    PHONE,
    SERVICE_CREDENTIAL,
    SIGNING_KEY,
    auth_service,
    build_delivery,
    build_store,
    introspection_service,
    minutes,
    role_service,
    sign_in,
)

from identity.application.authentication_service import (
    AuthenticationService,
    BootstrapPolicy,
    RequestOtpCommand,
    VerifyOtpCommand,
)
from identity.application.role_service import REQUIRED_SCOPE, GrantRoleCommand
from identity.domain.errors import (
    PrincipalNotFound,
    RoleGrantScopeInvalid,
    ServiceCredentialRejected,
)
from identity.domain.value_objects import (
    PRIVILEGED_ROLES,
    SELF_SERVICE_ROLE,
    PrincipalStatus,
    Role,
    ScopeKind,
)


def _introspect(store, token, *, at=None, credential=SERVICE_CREDENTIAL, device_id=None):
    return introspection_service(store).introspect(
        token=token,
        service_credential=credential,
        now=at or (BASE_TIME + minutes(2)),
        device_id=device_id,
    )


# -------------------------------------------------------------- happy path


def test_a_live_token_resolves_to_its_principal_and_roles():
    store, delivery = build_store(), build_delivery()
    session = sign_in(store, delivery)

    result = _introspect(store, session.token)

    assert result.active is True
    assert result.principal_id == session.principal_id
    assert Role.CUSTOMER in result.roles
    assert result.status is PrincipalStatus.ACTIVE


# ----------------------------------------------------------------- refusal


def test_introspection_refuses_a_caller_without_a_service_credential():
    """An end user must never be able to learn who a token belongs to."""
    store, delivery = build_store(), build_delivery()
    session = sign_in(store, delivery)

    with pytest.raises(ServiceCredentialRejected):
        _introspect(store, session.token, credential="")


def test_introspection_refuses_a_wrong_service_credential():
    store, delivery = build_store(), build_delivery()
    session = sign_in(store, delivery)

    with pytest.raises(ServiceCredentialRejected):
        _introspect(store, session.token, credential="not-the-secret")


def test_a_revoked_token_is_inactive_immediately():
    store, delivery = build_store(), build_delivery()
    session = sign_in(store, delivery)
    auth_service(store, delivery).revoke_session(
        session_id=session.session_id, now=BASE_TIME + minutes(1), reason="signed_out"
    )

    assert _introspect(store, session.token).active is False


def test_an_expired_token_is_inactive():
    store, delivery = build_store(), build_delivery()
    session = sign_in(store, delivery)

    result = _introspect(store, session.token, at=session.expires_at + minutes(1))

    assert result.active is False


def test_an_unknown_token_is_inactive_and_discloses_nothing():
    store = build_store()

    result = _introspect(store, "not-a-real-token")

    assert result.active is False
    assert result.principal_id is None
    assert result.roles == ()


def test_a_blank_token_is_inactive():
    assert _introspect(build_store(), "   ").active is False


def test_suspending_a_principal_makes_its_live_tokens_inactive():
    store, delivery = build_store(), build_delivery()
    session = sign_in(store, delivery)
    role_service(store).set_principal_status(
        principal_id=session.principal_id,
        status=PrincipalStatus.SUSPENDED,
        reason="lateness block",
        occurred_at=BASE_TIME + minutes(1),
    )

    assert _introspect(store, session.token).active is False


# ------------------------------------------------------------ device binding


def test_a_token_bound_to_a_device_is_inactive_from_another_device():
    store, delivery = build_store(), build_delivery()
    session = sign_in(store, delivery, device_id="DEV-77A")

    assert _introspect(store, session.token, device_id="DEV-77A").active is True
    assert _introspect(store, session.token, device_id="DEV-OTHER").active is False


def test_an_unbound_token_is_not_broken_by_a_device_header():
    store, delivery = build_store(), build_delivery()
    session = sign_in(store, delivery)

    assert _introspect(store, session.token, device_id="DEV-ANY").active is True


# ------------------------------------------------------------- role grants


def test_operations_can_grant_a_global_role():
    store, delivery = build_store(), build_delivery()
    session = sign_in(store, delivery)

    result = role_service(store).grant(
        GrantRoleCommand(
            principal_id=session.principal_id,
            role=Role.PICKUP_DRIVER,
            granted_by="ops-1",
            occurred_at=BASE_TIME + minutes(1),
        )
    )

    assert result.grant.role is Role.PICKUP_DRIVER
    assert result.idempotent_replay is False


def test_granting_the_same_role_twice_is_an_idempotent_replay():
    store, delivery = build_store(), build_delivery()
    session = sign_in(store, delivery)
    service = role_service(store)
    command = GrantRoleCommand(
        principal_id=session.principal_id,
        role=Role.PICKUP_DRIVER,
        granted_by="ops-1",
        occurred_at=BASE_TIME + minutes(1),
    )
    first = service.grant(command)

    second = service.grant(command)

    assert second.idempotent_replay is True
    assert second.grant.grant_id == first.grant.grant_id


def test_a_newly_granted_role_appears_on_the_very_next_introspection():
    store, delivery = build_store(), build_delivery()
    session = sign_in(store, delivery)
    role_service(store).grant(
        GrantRoleCommand(
            principal_id=session.principal_id,
            role=Role.PICKUP_DRIVER,
            granted_by="ops-1",
            occurred_at=BASE_TIME + minutes(1),
        )
    )

    result = _introspect(store, session.token)

    assert Role.PICKUP_DRIVER in result.roles


def test_revoking_a_role_removes_it_without_invalidating_the_session():
    store, delivery = build_store(), build_delivery()
    session = sign_in(store, delivery)
    service = role_service(store)
    granted = service.grant(
        GrantRoleCommand(
            principal_id=session.principal_id,
            role=Role.PICKUP_DRIVER,
            granted_by="ops-1",
            occurred_at=BASE_TIME + minutes(1),
        )
    )

    service.revoke(
        grant_id=granted.grant.grant_id,
        revoked_by="ops-1",
        occurred_at=BASE_TIME + minutes(2),
    )
    result = _introspect(store, session.token, at=BASE_TIME + minutes(3))

    assert result.active is True
    assert Role.PICKUP_DRIVER not in result.roles


def test_a_merchant_role_must_carry_a_merchant_scope():
    store, delivery = build_store(), build_delivery()
    session = sign_in(store, delivery)

    with pytest.raises(RoleGrantScopeInvalid):
        role_service(store).grant(
            GrantRoleCommand(
                principal_id=session.principal_id,
                role=Role.MERCHANT_MEMBER,
                granted_by="ops-1",
                occurred_at=BASE_TIME + minutes(1),
            )
        )


def test_a_hub_role_must_carry_a_hub_scope():
    store, delivery = build_store(), build_delivery()
    session = sign_in(store, delivery)

    with pytest.raises(RoleGrantScopeInvalid):
        role_service(store).grant(
            GrantRoleCommand(
                principal_id=session.principal_id,
                role=Role.HUB_OPERATOR,
                granted_by="ops-1",
                occurred_at=BASE_TIME + minutes(1),
                scope_kind=ScopeKind.MERCHANT,
                scope_id=uuid4(),
            )
        )


def test_a_global_role_may_not_be_given_a_scope():
    store, delivery = build_store(), build_delivery()
    session = sign_in(store, delivery)

    with pytest.raises(RoleGrantScopeInvalid):
        role_service(store).grant(
            GrantRoleCommand(
                principal_id=session.principal_id,
                role=Role.OPERATIONS,
                granted_by="ops-1",
                occurred_at=BASE_TIME + minutes(1),
                scope_kind=ScopeKind.HUB,
                scope_id=uuid4(),
            )
        )


def test_scoped_grants_surface_as_merchant_and_hub_scopes():
    store, delivery = build_store(), build_delivery()
    session = sign_in(store, delivery)
    merchant_id, hub_id = uuid4(), uuid4()
    service = role_service(store)
    service.grant(
        GrantRoleCommand(
            principal_id=session.principal_id,
            role=Role.MERCHANT_MEMBER,
            granted_by="ops-1",
            occurred_at=BASE_TIME + minutes(1),
            scope_kind=ScopeKind.MERCHANT,
            scope_id=merchant_id,
        )
    )
    service.grant(
        GrantRoleCommand(
            principal_id=session.principal_id,
            role=Role.HUB_OPERATOR,
            granted_by="ops-1",
            occurred_at=BASE_TIME + minutes(1),
            scope_kind=ScopeKind.HUB,
            scope_id=hub_id,
        )
    )

    result = _introspect(store, session.token)

    assert result.merchant_ids == frozenset({merchant_id})
    assert result.hub_ids == frozenset({hub_id})


def test_two_merchant_grants_are_separate_scopes_not_a_replay():
    store, delivery = build_store(), build_delivery()
    session = sign_in(store, delivery)
    service = role_service(store)
    first, second = uuid4(), uuid4()
    for merchant_id in (first, second):
        service.grant(
            GrantRoleCommand(
                principal_id=session.principal_id,
                role=Role.MERCHANT_MEMBER,
                granted_by="ops-1",
                occurred_at=BASE_TIME + minutes(1),
                scope_kind=ScopeKind.MERCHANT,
                scope_id=merchant_id,
            )
        )

    assert _introspect(store, session.token).merchant_ids == frozenset({first, second})


def test_granting_to_an_unknown_principal_is_refused():
    store = build_store()

    with pytest.raises(PrincipalNotFound):
        role_service(store).grant(
            GrantRoleCommand(
                principal_id=uuid4(),
                role=Role.OPERATIONS,
                granted_by="ops-1",
                occurred_at=BASE_TIME,
            )
        )


# --------------------------------------------------------------- sessions


def test_revoking_every_session_signs_a_principal_out_everywhere():
    store, delivery = build_store(), build_delivery()
    first = sign_in(store, delivery)
    second = sign_in(store, delivery, at=BASE_TIME + minutes(5))

    revoked = auth_service(store, delivery).revoke_all_for_principal(
        principal_id=first.principal_id,
        now=BASE_TIME + minutes(6),
        reason="suspended",
    )

    assert revoked == 2
    assert _introspect(store, first.token, at=BASE_TIME + minutes(7)).active is False
    assert _introspect(store, second.token, at=BASE_TIME + minutes(7)).active is False


def test_two_sign_ins_produce_two_different_tokens():
    store, delivery = build_store(), build_delivery()

    first = sign_in(store, delivery)
    second = sign_in(store, delivery, at=BASE_TIME + minutes(5))

    assert first.token != second.token
    assert first.session_id != second.session_id


# ------------------------------------------------------------- bootstrap


def test_the_configured_bootstrap_phone_becomes_the_first_operator():
    """A fresh deployment has no operator, so no one could grant the first role."""
    store, delivery = build_store(), build_delivery()
    service = AuthenticationService(
        store,
        signing_key=SIGNING_KEY,
        otp_delivery=delivery,
        bootstrap=BootstrapPolicy(operations_phone="+964 770 182 0934"),
    )
    issued = service.request_code(RequestOtpCommand(phone=PHONE, occurred_at=BASE_TIME))
    session = service.verify_code(
        VerifyOtpCommand(
            challenge_id=issued.challenge_id,
            code=delivery.code_for(str(issued.challenge_id)),
            occurred_at=BASE_TIME + minutes(1),
        )
    )

    assert Role.OPERATIONS in session.roles


def test_nobody_else_receives_the_bootstrap_role():
    store, delivery = build_store(), build_delivery()
    service = AuthenticationService(
        store,
        signing_key=SIGNING_KEY,
        otp_delivery=delivery,
        bootstrap=BootstrapPolicy(operations_phone="+9647701820934"),
    )
    issued = service.request_code(
        RequestOtpCommand(phone=OTHER_PHONE, occurred_at=BASE_TIME)
    )
    session = service.verify_code(
        VerifyOtpCommand(
            challenge_id=issued.challenge_id,
            code=delivery.code_for(str(issued.challenge_id)),
            occurred_at=BASE_TIME + minutes(1),
        )
    )

    assert Role.OPERATIONS not in session.roles


def test_no_bootstrap_phone_means_no_automatic_operator():
    store, delivery = build_store(), build_delivery()

    session = sign_in(store, delivery)

    assert Role.OPERATIONS not in session.roles


def test_a_malformed_bootstrap_number_grants_nothing():
    store, delivery = build_store(), build_delivery()
    service = AuthenticationService(
        store,
        signing_key=SIGNING_KEY,
        otp_delivery=delivery,
        bootstrap=BootstrapPolicy(operations_phone="not-a-number"),
    )
    issued = service.request_code(RequestOtpCommand(phone=PHONE, occurred_at=BASE_TIME))
    session = service.verify_code(
        VerifyOtpCommand(
            challenge_id=issued.challenge_id,
            code=delivery.code_for(str(issued.challenge_id)),
            occurred_at=BASE_TIME + minutes(1),
        )
    )

    assert Role.OPERATIONS not in session.roles


# ------------------------------------------------- the transaction boundary


def test_introspection_opens_a_transaction() -> None:
    """Token introspection reads three things that have to agree with each other.

    This is a regression test for a real failure: `introspect` read the session, the
    principal and the grants without opening a transaction. The in-memory double quietly
    fell back to committed state, so every unit test passed — and the first time it ran
    against PostgreSQL it raised `no active transaction`. Because every other service in
    the platform authorizes through this endpoint, authorization was broken everywhere
    at once and nowhere in the tests.
    """
    store = build_store()
    service = introspection_service(store)

    result = service.introspect(
        token="not-a-real-token",
        service_credential=SERVICE_CREDENTIAL,
        now=BASE_TIME,
    )
    assert result.active is False
    # The transaction it opened was also closed; a leaked one would wedge the next call.
    assert store._tx is None


def test_introspection_leaves_no_transaction_open_when_it_refuses() -> None:
    store = build_store()
    service = introspection_service(store)
    with pytest.raises(ServiceCredentialRejected):
        service.introspect(
            token="anything", service_credential="wrong-secret", now=BASE_TIME
        )
    assert store._tx is None


def test_the_in_memory_unit_of_work_refuses_a_read_outside_a_transaction() -> None:
    """The double must not be more permissive than the store it stands in for."""
    store = build_store()
    with pytest.raises(RuntimeError, match="no active transaction"):
        store.sessions.find_by_token_hash("anything")


# --------------------------------------- the published role contract


def _published_roles() -> dict:
    contract = (
        Path(__file__).resolve().parents[3] / "contracts" / "identity" / "roles.yaml"
    )
    return {
        role["name"]: role
        for role in yaml.safe_load(contract.read_text(encoding="utf-8"))["roles"]
    }


def test_the_published_contract_lists_exactly_the_roles_identity_has() -> None:
    """Other services map these names. A stale contract is a silent 403 for them.

    `delivery`, `finance` and `workforce` each read
    `contracts/identity/roles.yaml` in their own boundary tests, because they may not
    import this package. That only protects them while this file is the truth.
    """
    assert set(_published_roles()) == {role.value for role in Role}


def test_the_published_scope_matches_what_grant_requires() -> None:
    """A role published as GLOBAL that actually needs a scope cannot be granted."""
    published = _published_roles()
    for role in Role:
        expected = REQUIRED_SCOPE.get(role, ScopeKind.GLOBAL)
        assert published[role.value]["scope"] == expected.value, role.value


def test_the_published_privileged_flag_matches_the_domain() -> None:
    published = _published_roles()
    for role in Role:
        declared = bool(published[role.value].get("privileged", False))
        assert declared is (role in PRIVILEGED_ROLES), role.value


def test_only_customer_is_published_as_self_service() -> None:
    published = _published_roles()
    self_service = {
        name for name, role in published.items() if role.get("self_service")
    }
    assert self_service == {SELF_SERVICE_ROLE.value}
