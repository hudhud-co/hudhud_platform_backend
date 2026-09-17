"""The HTTP adapter: separation of duties, and the two blocked operations.

ADR-0012's rule runs through every test here — a driver records their own deposit, a
cashier or accountant confirms it, only Operations approves a payout — because those are
three different people on purpose, and an adapter that collapsed them would undo the
control without changing a single domain rule.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from finance_fixtures import TEST_CASH_LIMIT, TEST_RETURN_TRIP_FEE

from finance.application.settlement_service import SettlementService
from finance.config import (
    ProductionStartupBlockedError,
    RuntimeEnvironment,
    load_settings,
)
from finance.infrastructure.authorizers.identity import (
    DefaultDenyFinanceAuthorizer,
    FakeFinanceAuthorizer,
    build_actor,
)
from finance.infrastructure.memory import InMemoryUnitOfWork
from finance.main import create_app
from finance.ports.authorization import FinanceActor, FinanceCommand, FinanceRole

DRIVER = "driver-token"
OTHER_DRIVER = "other-driver-token"
CASHIER = "cashier-token"
ACCOUNTANT = "accountant-token"
OPERATIONS = "operations-token"
MERCHANT = "merchant-token"
OTHER_MERCHANT = "other-merchant-token"


@pytest.fixture
def ids():
    return {
        "driver": uuid4(),
        "other_driver": uuid4(),
        "merchant": uuid4(),
        "other_merchant": uuid4(),
    }


def _actors(ids) -> dict[str, FinanceActor]:
    return {
        DRIVER: FinanceActor(
            principal_id=ids["driver"],
            roles=frozenset({FinanceRole.LAST_MILE_DRIVER}),
        ),
        OTHER_DRIVER: FinanceActor(
            principal_id=ids["other_driver"],
            roles=frozenset({FinanceRole.LAST_MILE_DRIVER}),
        ),
        CASHIER: FinanceActor(
            principal_id=uuid4(), roles=frozenset({FinanceRole.HUB_CASHIER})
        ),
        ACCOUNTANT: FinanceActor(
            principal_id=uuid4(), roles=frozenset({FinanceRole.ACCOUNTANT})
        ),
        OPERATIONS: FinanceActor(
            principal_id=uuid4(), roles=frozenset({FinanceRole.OPERATIONS})
        ),
        MERCHANT: FinanceActor(
            principal_id=uuid4(),
            roles=frozenset({FinanceRole.MERCHANT_OWNER}),
            merchant_id=ids["merchant"],
        ),
        OTHER_MERCHANT: FinanceActor(
            principal_id=uuid4(),
            roles=frozenset({FinanceRole.MERCHANT_OWNER}),
            merchant_id=ids["other_merchant"],
        ),
    }


def build_client(ids, **settings_overrides) -> TestClient:
    options = {
        "environment": RuntimeEnvironment.TEST,
        "default_cash_limit_minor_units": TEST_CASH_LIMIT,
        "return_trip_fee_minor_units": TEST_RETURN_TRIP_FEE,
    }
    options.update(settings_overrides)
    settings = load_settings(**options)
    app = create_app(
        settings,
        unit_of_work=InMemoryUnitOfWork(),
        authorizer=FakeFinanceAuthorizer(token_actors=_actors(ids)),
    )
    client = TestClient(app)
    # Two accounts, opened the way operations would.
    for driver in (ids["driver"], ids["other_driver"]):
        client.post(
            "/finance/cash-accounts",
            json={"driver_principal_id": str(driver)},
            headers=bearer(OPERATIONS),
        )
    # Services are per request now, so a test that wants one builds it from the same
    # factory a request would — never from application state, which holds none.
    settlement = SettlementService(app.state.unit_of_work_factory())
    settlement.open_merchant_account(merchant_id=ids["merchant"])
    SettlementService(app.state.unit_of_work_factory()).open_merchant_account(
        merchant_id=ids["other_merchant"]
    )
    return client


@pytest.fixture
def client(ids) -> TestClient:
    return build_client(ids)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def iqd_json(minor_units: int) -> dict:
    return {"minor_units": minor_units, "currency": "IQD"}


_CODES = {"n": 0}


def collect(client: TestClient, ids, *, goods=100_000, fee=5_000, channel="CASH"):
    _CODES["n"] += 1
    body = {
        "tracking_code": f"SHP-20260915-{_CODES['n']:06d}",
        "merchant_id": str(ids["merchant"]),
        "channel": channel,
        "goods_amount": iqd_json(goods),
        "delivery_fee": iqd_json(fee),
    }
    if channel == "CASH":
        body["driver_principal_id"] = str(ids["driver"])
    response = client.post("/finance/collections", json=body, headers=bearer(DRIVER))
    assert response.status_code == 201, response.text
    return response.json()


# ------------------------------------------------------------ authorization


def test_every_route_needs_a_bearer_token(client: TestClient, ids) -> None:
    assert client.get(f"/finance/cash-accounts/{ids['driver']}").status_code == 401
    assert client.get("/finance/cash-exposure").status_code == 401


def test_an_unknown_token_is_unauthenticated(client: TestClient, ids) -> None:
    assert (
        client.get(
            f"/finance/cash-accounts/{ids['driver']}", headers=bearer("nonsense")
        ).status_code
        == 401
    )


def test_a_service_with_no_identity_denies_everything(ids) -> None:
    """Otherwise anyone could approve their own payout."""
    app = create_app(
        load_settings(
            environment=RuntimeEnvironment.TEST,
            default_cash_limit_minor_units=TEST_CASH_LIMIT,
        ),
        unit_of_work=InMemoryUnitOfWork(),
        authorizer=DefaultDenyFinanceAuthorizer(),
    )
    assert (
        TestClient(app)
        .get(f"/finance/cash-accounts/{ids['driver']}", headers=bearer(DRIVER))
        .status_code
        == 401
    )


def test_identity_being_unreachable_is_not_a_denial(ids) -> None:
    app = create_app(
        load_settings(
            environment=RuntimeEnvironment.TEST,
            default_cash_limit_minor_units=TEST_CASH_LIMIT,
        ),
        unit_of_work=InMemoryUnitOfWork(),
        authorizer=FakeFinanceAuthorizer(unavailable=True),
    )
    response = TestClient(app).get(
        f"/finance/cash-accounts/{ids['driver']}", headers=bearer(DRIVER)
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "authorization_unavailable"


def test_a_denied_command_is_forbidden(ids) -> None:
    app = create_app(
        load_settings(
            environment=RuntimeEnvironment.TEST,
            default_cash_limit_minor_units=TEST_CASH_LIMIT,
        ),
        unit_of_work=InMemoryUnitOfWork(),
        authorizer=FakeFinanceAuthorizer(
            token_actors=_actors(ids),
            denied_commands=frozenset({FinanceCommand.CASH_ACCOUNT_READ}),
        ),
    )
    assert (
        TestClient(app)
        .get(f"/finance/cash-accounts/{ids['driver']}", headers=bearer(DRIVER))
        .status_code
        == 403
    )


def test_a_driver_cannot_read_another_drivers_cash(client: TestClient, ids) -> None:
    response = client.get(
        f"/finance/cash-accounts/{ids['other_driver']}", headers=bearer(DRIVER)
    )
    assert response.status_code == 403


def test_a_driver_reads_their_own_cash(client: TestClient, ids) -> None:
    assert (
        client.get(
            f"/finance/cash-accounts/{ids['driver']}", headers=bearer(DRIVER)
        ).status_code
        == 200
    )


def test_a_driver_does_not_set_their_own_limit(client: TestClient, ids) -> None:
    response = client.put(
        f"/finance/cash-accounts/{ids['driver']}/limit",
        json={"limit": iqd_json(99_000_000)},
        headers=bearer(DRIVER),
    )
    assert response.status_code == 403


def test_a_driver_cannot_see_platform_cash_exposure(client: TestClient) -> None:
    """OPS-04 is an operations view, not a leaderboard."""
    assert client.get("/finance/cash-exposure", headers=bearer(DRIVER)).status_code == 403
    assert (
        client.get("/finance/cash-exposure", headers=bearer(OPERATIONS)).status_code
        == 200
    )


def test_a_merchant_cannot_read_another_merchants_balance(
    client: TestClient, ids
) -> None:
    response = client.get(
        f"/finance/merchants/{ids['merchant']}/balance", headers=bearer(OTHER_MERCHANT)
    )
    assert response.status_code == 403


# ------------------------------------------------------------ COD over HTTP


def test_a_collection_posts_and_reads_back(client: TestClient, ids) -> None:
    collected = collect(client, ids, goods=100_000, fee=5_000)
    assert collected["total"] == iqd_json(105_000)
    assert collected["is_paid"] is False

    read = client.get(
        f"/finance/collections/{collected['tracking_code']}", headers=bearer(OPERATIONS)
    )
    assert read.status_code == 200
    assert read.json()["collection_id"] == collected["collection_id"]


def test_a_card_collection_is_paid_at_once(client: TestClient, ids) -> None:
    collected = collect(client, ids, goods=50_000, fee=2_500, channel="POS_CARD")
    assert collected["is_paid"] is True


def test_money_must_be_an_integer_over_the_wire(client: TestClient, ids) -> None:
    """A JSON `105000.5` has been through a float somewhere; this is where it stops."""
    _CODES["n"] += 1
    response = client.post(
        "/finance/collections",
        json={
            "tracking_code": f"SHP-20260915-{_CODES['n']:06d}",
            "merchant_id": str(ids["merchant"]),
            "channel": "CASH",
            "goods_amount": {"minor_units": 100000.5, "currency": "IQD"},
            "delivery_fee": iqd_json(0),
            "driver_principal_id": str(ids["driver"]),
        },
        headers=bearer(DRIVER),
    )
    assert response.status_code == 422


def test_a_retried_collection_posts_once(client: TestClient, ids) -> None:
    _CODES["n"] += 1
    body = {
        "tracking_code": f"SHP-20260915-{_CODES['n']:06d}",
        "merchant_id": str(ids["merchant"]),
        "channel": "CASH",
        "goods_amount": iqd_json(40_000),
        "delivery_fee": iqd_json(0),
        "driver_principal_id": str(ids["driver"]),
        "idempotency_key": "http-retry-1",
    }
    first = client.post("/finance/collections", json=body, headers=bearer(DRIVER))
    second = client.post("/finance/collections", json=body, headers=bearer(DRIVER))
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["collection_id"] == second.json()["collection_id"]
    held = client.get(
        f"/finance/cash-accounts/{ids['driver']}", headers=bearer(DRIVER)
    ).json()["held"]
    assert held == iqd_json(40_000)


# ------------------------------------------------------ deposits, and who confirms


def test_a_driver_records_their_own_deposit(client: TestClient, ids) -> None:
    collect(client, ids, goods=50_000, fee=0)
    response = client.post(
        "/finance/deposits",
        json={
            "method": "BANK_TRANSFER",
            "amount": iqd_json(50_000),
            "reference": "TRX-HTTP-1",
            "receipt": {"bucket": "finance-evidence", "key": "slip.jpg"},
        },
        headers=bearer(DRIVER),
    )
    assert response.status_code == 201, response.text
    assert response.json()["held_after"] == iqd_json(0)


def test_the_deposit_is_recorded_against_the_authenticated_driver(
    client: TestClient, ids
) -> None:
    """A body-supplied id would let one driver clear another's cash off their record."""
    collect(client, ids, goods=50_000, fee=0)
    response = client.post(
        "/finance/deposits",
        json={
            "method": "BANK_TRANSFER",
            "amount": iqd_json(10_000),
            "reference": "TRX-HTTP-2",
            "receipt": {"bucket": "finance-evidence", "key": "slip.jpg"},
            "driver_principal_id": str(ids["other_driver"]),
        },
        headers=bearer(DRIVER),
    )
    assert response.status_code == 422


def test_a_hub_deposit_needs_its_hub(client: TestClient, ids) -> None:
    collect(client, ids, goods=50_000, fee=0)
    response = client.post(
        "/finance/deposits",
        json={
            "method": "HUB_CASHIER",
            "amount": iqd_json(50_000),
            "reference": "TRX-HTTP-3",
            "receipt": {"bucket": "finance-evidence", "key": "slip.jpg"},
        },
        headers=bearer(DRIVER),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "hub_required_for_a_cashier_deposit"


def test_only_an_accountant_verifies_an_exchange_receipt(
    client: TestClient, ids
) -> None:
    collect(client, ids, goods=50_000, fee=0)
    deposit = client.post(
        "/finance/deposits",
        json={
            "method": "EXCHANGE_OFFICE",
            "amount": iqd_json(50_000),
            "reference": "EX-HTTP-1",
            "receipt": {"bucket": "finance-evidence", "key": "slip.jpg"},
        },
        headers=bearer(DRIVER),
    ).json()["deposit"]
    path = f"/finance/deposits/{deposit['deposit_id']}/confirm"
    assert client.post(path, headers=bearer(DRIVER)).status_code == 403
    assert client.post(path, headers=bearer(CASHIER)).status_code == 403
    assert client.post(path, headers=bearer(ACCOUNTANT)).status_code == 200


def test_a_driver_cannot_confirm_their_own_hub_deposit(
    client: TestClient, ids
) -> None:
    collect(client, ids, goods=50_000, fee=0)
    deposit = client.post(
        "/finance/deposits",
        json={
            "method": "HUB_CASHIER",
            "amount": iqd_json(50_000),
            "reference": "HUB-HTTP-1",
            "receipt": {"bucket": "finance-evidence", "key": "slip.jpg"},
            "hub_id": str(uuid4()),
        },
        headers=bearer(DRIVER),
    ).json()["deposit"]
    path = f"/finance/deposits/{deposit['deposit_id']}/confirm"
    assert client.post(path, headers=bearer(DRIVER)).status_code == 403
    assert client.post(path, headers=bearer(CASHIER)).status_code == 200


def test_a_hub_deposit_makes_the_parcel_paid_and_an_exchange_one_does_not(
    client: TestClient, ids
) -> None:
    """PAY-01 against PAY-04, over HTTP."""
    hub_parcel = collect(client, ids, goods=30_000, fee=0)
    deposit = client.post(
        "/finance/deposits",
        json={
            "method": "HUB_CASHIER",
            "amount": iqd_json(30_000),
            "reference": "HUB-HTTP-2",
            "receipt": {"bucket": "finance-evidence", "key": "slip.jpg"},
            "hub_id": str(uuid4()),
        },
        headers=bearer(DRIVER),
    ).json()["deposit"]
    client.post(
        f"/finance/deposits/{deposit['deposit_id']}/confirm", headers=bearer(CASHIER)
    )
    paid = client.get(
        f"/finance/collections/{hub_parcel['tracking_code']}", headers=bearer(OPERATIONS)
    ).json()
    assert paid["is_paid"] is True
    assert paid["settling_deposit_id"] == deposit["deposit_id"]


def test_settlement_history_is_the_drivers_own(client: TestClient, ids) -> None:
    assert (
        client.get(
            f"/finance/cash-accounts/{ids['driver']}/settlements", headers=bearer(DRIVER)
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/finance/cash-accounts/{ids['other_driver']}/settlements",
            headers=bearer(DRIVER),
        ).status_code
        == 403
    )


# ------------------------------------------------------------ payouts


def test_a_merchant_requests_their_own_payout(client: TestClient, ids) -> None:
    collect(client, ids, goods=100_000, fee=0)
    response = client.post(
        f"/finance/merchants/{ids['merchant']}/payouts",
        json={
            "method": "BANK_TRANSFER",
            "amount": iqd_json(10_000),
            "destination_reference": "IQ98NBIQ1234567890",
        },
        headers=bearer(MERCHANT),
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "REQUESTED"


def test_the_payout_response_never_echoes_the_destination(
    client: TestClient, ids
) -> None:
    collect(client, ids, goods=100_000, fee=0)
    response = client.post(
        f"/finance/merchants/{ids['merchant']}/payouts",
        json={
            "method": "BANK_TRANSFER",
            "amount": iqd_json(10_000),
            "destination_reference": "IQ98NBIQ1234567890",
        },
        headers=bearer(MERCHANT),
    )
    assert response.json()["has_destination"] is True
    assert "IQ98NBIQ1234567890" not in response.text


def test_a_merchant_cannot_request_a_payout_for_another(
    client: TestClient, ids
) -> None:
    response = client.post(
        f"/finance/merchants/{ids['merchant']}/payouts",
        json={"method": "IN_PERSON_AT_HUB", "amount": iqd_json(1_000)},
        headers=bearer(OTHER_MERCHANT),
    )
    assert response.status_code == 403


def test_only_operations_approves_a_payout(client: TestClient, ids) -> None:
    collect(client, ids, goods=100_000, fee=0)
    payout = client.post(
        f"/finance/merchants/{ids['merchant']}/payouts",
        json={"method": "IN_PERSON_AT_HUB", "amount": iqd_json(10_000)},
        headers=bearer(MERCHANT),
    ).json()
    path = f"/finance/payouts/{payout['payout_id']}/approve"
    assert client.post(path, headers=bearer(MERCHANT)).status_code == 403
    assert client.post(path, headers=bearer(ACCOUNTANT)).status_code == 403
    assert client.post(path, headers=bearer(OPERATIONS)).status_code == 200


# --------------------------------------------- PAY-07 over HTTP


def test_paying_a_payout_answers_501(client: TestClient, ids) -> None:
    """The request was fine and the service is healthy; the procedure is missing."""
    collect(client, ids, goods=100_000, fee=0)
    payout = client.post(
        f"/finance/merchants/{ids['merchant']}/payouts",
        json={"method": "IN_PERSON_AT_HUB", "amount": iqd_json(10_000)},
        headers=bearer(MERCHANT),
    ).json()
    client.post(
        f"/finance/payouts/{payout['payout_id']}/approve", headers=bearer(OPERATIONS)
    )
    response = client.post(
        f"/finance/payouts/{payout['payout_id']}/pay", headers=bearer(OPERATIONS)
    )
    assert response.status_code == 501
    assert response.json()["detail"]["code"] == "payout_procedure_not_defined"


def test_the_blocked_payment_leaves_the_balance_untouched(
    client: TestClient, ids
) -> None:
    collect(client, ids, goods=100_000, fee=0)
    before = client.get(
        f"/finance/merchants/{ids['merchant']}/balance", headers=bearer(MERCHANT)
    ).json()["balance"]
    payout = client.post(
        f"/finance/merchants/{ids['merchant']}/payouts",
        json={"method": "IN_PERSON_AT_HUB", "amount": iqd_json(10_000)},
        headers=bearer(MERCHANT),
    ).json()
    client.post(
        f"/finance/payouts/{payout['payout_id']}/approve", headers=bearer(OPERATIONS)
    )
    client.post(
        f"/finance/payouts/{payout['payout_id']}/pay", headers=bearer(OPERATIONS)
    )
    after = client.get(
        f"/finance/merchants/{ids['merchant']}/balance", headers=bearer(MERCHANT)
    ).json()["balance"]
    assert after == before


def test_an_unapproved_payout_answers_409_and_not_501(
    client: TestClient, ids
) -> None:
    """The state machine still applies; 501 is reserved for the missing decision."""
    collect(client, ids, goods=100_000, fee=0)
    payout = client.post(
        f"/finance/merchants/{ids['merchant']}/payouts",
        json={"method": "IN_PERSON_AT_HUB", "amount": iqd_json(10_000)},
        headers=bearer(MERCHANT),
    ).json()
    response = client.post(
        f"/finance/payouts/{payout['payout_id']}/pay", headers=bearer(OPERATIONS)
    )
    assert response.status_code == 409


# --------------------------------------------- PAY-08 over HTTP


def test_the_refusal_charge_works(client: TestClient, ids) -> None:
    """v6.3 p.38, p.39 settle the charge, so it is not blocked."""
    response = client.post(
        "/finance/refusal-charges",
        json={
            "tracking_code": "SHP-20260915-990001",
            "merchant_id": str(ids["merchant"]),
            "delivery_fee": iqd_json(5_000),
        },
        headers=bearer(OPERATIONS),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["return_trip_fee"] == iqd_json(TEST_RETURN_TRIP_FEE)
    assert body["total"] == iqd_json(5_000 + TEST_RETURN_TRIP_FEE)


def test_waiving_the_return_fee_answers_501(client: TestClient, ids) -> None:
    response = client.post(
        "/finance/refusal-charges/waive",
        json={
            "tracking_code": "SHP-20260915-990002",
            "merchant_id": str(ids["merchant"]),
            "amount": iqd_json(TEST_RETURN_TRIP_FEE),
            "note": "goodwill",
        },
        headers=bearer(OPERATIONS),
    )
    assert response.status_code == 501
    assert response.json()["detail"]["code"] == "return_fee_waiver_not_decided"


def test_the_waiver_opens_when_the_business_permits_it(ids) -> None:
    """One setting, no code change — which is what makes this a decision and not a gap."""
    client = build_client(ids, return_fee_waiver_permitted=True)
    client.post(
        "/finance/refusal-charges",
        json={
            "tracking_code": "SHP-20260915-990003",
            "merchant_id": str(ids["merchant"]),
            "delivery_fee": iqd_json(5_000),
        },
        headers=bearer(OPERATIONS),
    )
    response = client.post(
        "/finance/refusal-charges/waive",
        json={
            "tracking_code": "SHP-20260915-990003",
            "merchant_id": str(ids["merchant"]),
            "amount": iqd_json(TEST_RETURN_TRIP_FEE),
            "note": "merchant was not at fault",
        },
        headers=bearer(OPERATIONS),
    )
    assert response.status_code == 200, response.text


def test_no_tariff_answers_503_not_501(ids) -> None:
    """A number nobody set is a deployment problem; a decision nobody made is not."""
    client = build_client(ids, return_trip_fee_minor_units=None)
    response = client.post(
        "/finance/refusal-charges",
        json={
            "tracking_code": "SHP-20260915-990004",
            "merchant_id": str(ids["merchant"]),
            "delivery_fee": iqd_json(5_000),
        },
        headers=bearer(OPERATIONS),
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "tariff_not_configured"


def test_the_open_items_route_explains_both(client: TestClient) -> None:
    body = client.get("/finance/open-items", headers=bearer(OPERATIONS)).json()
    assert body["payout_payment_available"] is False
    assert "PAY-07" in body["payout_payment_blocked_by"]
    assert body["return_fee_waiver_permitted"] is False
    assert "PAY-08" in body["return_fee_waiver_blocked_by"]
    assert body["return_trip_fee_configured"] is True


# ------------------------------------------------------------ the ledger read


def test_a_platform_balance_is_readable_by_an_accountant(
    client: TestClient, ids
) -> None:
    collect(client, ids, goods=10_000, fee=1_000)
    response = client.get(
        "/finance/balances/HUDHUD_REVENUE", headers=bearer(ACCOUNTANT)
    )
    assert response.status_code == 200
    assert response.json()["balance"] == iqd_json(1_000)


def test_a_driver_cannot_read_the_ledger(client: TestClient) -> None:
    assert (
        client.get("/finance/balances/BANK", headers=bearer(DRIVER)).status_code == 403
    )


def test_a_party_scoped_account_needs_its_party(client: TestClient) -> None:
    response = client.get(
        "/finance/balances/DRIVER_CASH_CUSTODY", headers=bearer(ACCOUNTANT)
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "account_party_mismatch"


# ------------------------------------------------------------ readiness


def test_readiness_names_every_missing_gate() -> None:
    app = create_app(
        load_settings(environment=RuntimeEnvironment.TEST),
        unit_of_work=InMemoryUnitOfWork(),
        authorizer=DefaultDenyFinanceAuthorizer(),
    )
    body = TestClient(app).get("/ready").json()
    assert body["status"] == "not_ready"
    assert "cash_limit_configured" in body["blockers"]
    assert "return_trip_fee_configured" in body["blockers"]


def test_production_refuses_to_start_without_a_cash_limit() -> None:
    """DRV-A06 — an unset limit is not a cautious default, it is no limit at all."""
    with pytest.raises(ProductionStartupBlockedError) as caught:
        create_app(
            load_settings(
                environment=RuntimeEnvironment.PRODUCTION,
                database_url="postgresql+psycopg://localhost/finance",
                identity_base_url="http://identity",
                identity_service_credential="x",
            )
        )
    assert "FINANCE_DEFAULT_CASH_LIMIT" in str(caught.value)


def test_production_starts_without_a_return_trip_tariff() -> None:
    """Without it a refusal charge is refused, which is correct for an unset tariff."""
    app = create_app(
        load_settings(
            environment=RuntimeEnvironment.PRODUCTION,
            database_url="postgresql+psycopg://localhost/finance",
            identity_base_url="http://identity",
            identity_service_credential="x",
            default_cash_limit_minor_units=TEST_CASH_LIMIT,
            return_trip_fee_minor_units=None,
        ),
        unit_of_work=InMemoryUnitOfWork(),
    )
    assert app.state.settings.return_trip_fee_configured is False


# --------------------------------------------- the real Identity contract


def test_the_merchant_scope_comes_from_identitys_grants() -> None:
    """Identity publishes no top-level `merchant_id` — it publishes scoped grants.

    An actor built from an introspection body has to read the merchant out of a
    `MERCHANT`-scoped grant, or every merchant-scoped route would refuse every real
    caller while passing against a hand-built fake.
    """
    merchant = uuid4()
    principal = uuid4()
    actor = build_actor(
        {
            "active": True,
            "principal_id": str(principal),
            "status": "ACTIVE",
            "roles": ["MERCHANT_MEMBER"],
            "grants": [
                {
                    "role": "MERCHANT_MEMBER",
                    "scope_kind": "MERCHANT",
                    "scope_id": str(merchant),
                }
            ],
        }
    )
    assert actor is not None
    assert actor.principal_id == principal
    assert actor.merchant_id == merchant
    assert actor.has_role(FinanceRole.MERCHANT_OWNER)


def test_a_global_grant_carries_no_merchant() -> None:
    actor = build_actor(
        {
            "active": True,
            "principal_id": str(uuid4()),
            "roles": ["OPERATIONS"],
            "grants": [
                {"role": "OPERATIONS", "scope_kind": "GLOBAL", "scope_id": None}
            ],
        }
    )
    assert actor is not None
    assert actor.merchant_id is None


def test_two_merchant_scopes_are_ambiguous_and_resolve_to_none() -> None:
    """Guessing which one the caller meant would hand one merchant another's balance."""
    actor = build_actor(
        {
            "active": True,
            "principal_id": str(uuid4()),
            "roles": ["MERCHANT_MEMBER"],
            "grants": [
                {
                    "role": "MERCHANT_MEMBER",
                    "scope_kind": "MERCHANT",
                    "scope_id": str(uuid4()),
                },
                {
                    "role": "MERCHANT_MEMBER",
                    "scope_kind": "MERCHANT",
                    "scope_id": str(uuid4()),
                },
            ],
        }
    )
    assert actor is not None
    assert actor.merchant_id is None


def test_a_suspended_principal_is_no_actor_at_all() -> None:
    assert (
        build_actor(
            {
                "active": True,
                "principal_id": str(uuid4()),
                "status": "SUSPENDED",
                "roles": ["OPERATIONS"],
            }
        )
        is None
    )


def test_an_inactive_token_is_no_actor() -> None:
    assert build_actor({"active": False}) is None


def test_an_unknown_role_is_dropped_rather_than_guessed() -> None:
    actor = build_actor(
        {
            "active": True,
            "principal_id": str(uuid4()),
            "roles": ["OPERATIONS", "SOMETHING_NEW"],
        }
    )
    assert actor is not None
    assert actor.roles == frozenset({FinanceRole.OPERATIONS})
