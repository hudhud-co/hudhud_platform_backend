"""Notification HTTP adapter: own-data scoping and what responses never carry."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from notification_fixtures import (
    DELIVERY_CODE,
    DRIVER,
    RECEIVER,
    RECEIVER_PHONE,
    RECIPIENT_KEY,
    SENDER,
    TEMPLATES,
    TRACKING,
    acceptance_context,
    fan_out_service,
    receiver_ref,
    recording_transports,
    sender_ref,
)

from notification.config import (
    NotificationSettings,
    ProductionStartupBlockedError,
    RuntimeEnvironment,
)
from notification.domain.value_objects import Category
from notification.infrastructure.authorizers.identity import (
    FakeNotificationAuthorizer,
)
from notification.infrastructure.memory import InMemoryNotificationUnitOfWork
from notification.main import create_app
from notification.ports.authorization import NotificationActor, NotificationRole

SENDER_TOKEN = "sender-token"
RECEIVER_TOKEN = "receiver-token"
OPS_TOKEN = "ops-token"
SUPPORT_TOKEN = "support-token"


def build_client(**kwargs) -> tuple[TestClient, InMemoryNotificationUnitOfWork]:
    uow = InMemoryNotificationUnitOfWork()
    authorizer = FakeNotificationAuthorizer(
        token_actors={
            SENDER_TOKEN: NotificationActor(
                principal_id=SENDER, roles=frozenset({NotificationRole.CUSTOMER})
            ),
            RECEIVER_TOKEN: NotificationActor(
                principal_id=RECEIVER, roles=frozenset({NotificationRole.CUSTOMER})
            ),
            OPS_TOKEN: NotificationActor(
                principal_id=DRIVER, roles=frozenset({NotificationRole.OPERATIONS})
            ),
            SUPPORT_TOKEN: NotificationActor(
                principal_id=DRIVER, roles=frozenset({NotificationRole.SUPPORT})
            ),
        }
    )
    app = create_app(
        NotificationSettings(
            environment=RuntimeEnvironment.TEST, recipient_hash_key=RECIPIENT_KEY
        ),
        unit_of_work=uow,
        authorizer=authorizer,
        transports=recording_transports(),
        templates=TEMPLATES,
        **kwargs,
    )
    return TestClient(app), uow


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _accept(uow) -> None:
    fan_out_service(uow).fan_out(
        category=Category.ACCEPTANCE,
        trigger_reference="pickup-accepted-1",
        recipients=(sender_ref(), receiver_ref()),
        tracking_code=TRACKING,
        context=acceptance_context(),
    )


# ------------------------------------------------------------------ auth


def test_health_needs_no_token() -> None:
    client, _ = build_client()
    assert client.get("/health").status_code == 200


def test_a_missing_token_is_401() -> None:
    client, _ = build_client()
    assert client.get("/notification/centre").status_code == 401


def test_the_default_composition_denies_everything() -> None:
    app = create_app(
        NotificationSettings(environment=RuntimeEnvironment.TEST),
        unit_of_work=InMemoryNotificationUnitOfWork(),
    )
    assert (
        TestClient(app).get("/notification/centre", headers=auth(SENDER_TOKEN)).status_code
        == 401
    )


def test_production_requires_a_recipient_hash_key() -> None:
    """Without it a recipient key is either reversible or a plain phone number."""
    with pytest.raises(ProductionStartupBlockedError) as caught:
        create_app(
            NotificationSettings(
                environment=RuntimeEnvironment.PRODUCTION,
                database_url="postgresql+psycopg://x/y",
                identity_base_url="http://identity",
                identity_service_credential="secret",
            )
        )
    assert "NOTIFICATION_RECIPIENT_HASH_KEY" in str(caught.value)


def test_readiness_reports_a_missing_sms_transport() -> None:
    """NTF-03 — the receiver always gets an SMS, so no SMS transport is not ready."""
    client, _ = build_client()
    body = client.get("/ready").json()

    assert body["checks"]["sms_transport_configured"] is False
    assert "sms_transport_configured" in body["blockers"]


# ------------------------------------------------------------------ preferences


def test_a_preference_can_be_switched_off_over_http() -> None:
    client, _ = build_client()

    response = client.put(
        "/notification/preferences",
        json={"category": "PRE_DELIVERY", "channel": "APP", "enabled": False},
        headers=auth(RECEIVER_TOKEN),
    )

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert response.json()["mandatory"] is False


def test_the_acceptance_sms_cannot_be_switched_off_over_http() -> None:
    client, _ = build_client()

    response = client.put(
        "/notification/preferences",
        json={"category": "ACCEPTANCE", "channel": "SMS", "enabled": False},
        headers=auth(RECEIVER_TOKEN),
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "notification_cannot_be_disabled"
    assert "delivery code" in detail["reason"]


def test_the_acceptance_sms_can_be_confirmed_on() -> None:
    """Switching it *on* is a no-op, not an error."""
    client, _ = build_client()

    response = client.put(
        "/notification/preferences",
        json={"category": "ACCEPTANCE", "channel": "SMS", "enabled": True},
        headers=auth(RECEIVER_TOKEN),
    )

    assert response.status_code == 200
    assert response.json()["mandatory"] is True


def test_preferences_are_listed_for_the_caller_only() -> None:
    client, _ = build_client()
    client.put(
        "/notification/preferences",
        json={"category": "PRE_DELIVERY", "channel": "APP", "enabled": False},
        headers=auth(RECEIVER_TOKEN),
    )

    mine = client.get("/notification/preferences", headers=auth(RECEIVER_TOKEN)).json()
    theirs = client.get("/notification/preferences", headers=auth(SENDER_TOKEN)).json()

    assert len(mine) == 1
    assert theirs == []


def test_an_invented_category_is_rejected_by_the_schema() -> None:
    client, _ = build_client()

    response = client.put(
        "/notification/preferences",
        json={"category": "MARKETING", "channel": "SMS", "enabled": False},
        headers=auth(RECEIVER_TOKEN),
    )

    assert response.status_code == 422


# ------------------------------------------------------------------ centre


def test_the_centre_shows_only_the_callers_own_entries() -> None:
    client, uow = build_client()
    _accept(uow)

    mine = client.get("/notification/centre", headers=auth(SENDER_TOKEN)).json()
    theirs = client.get("/notification/centre", headers=auth(RECEIVER_TOKEN)).json()

    assert mine["unread"] == 1
    assert theirs["unread"] == 0
    assert theirs["entries"] == []


def test_marking_an_entry_read_reduces_the_unread_count_over_http() -> None:
    client, uow = build_client()
    _accept(uow)
    entry_id = client.get("/notification/centre", headers=auth(SENDER_TOKEN)).json()[
        "entries"
    ][0]["entry_id"]

    client.post(f"/notification/centre/{entry_id}/read", headers=auth(SENDER_TOKEN))

    assert client.get("/notification/centre", headers=auth(SENDER_TOKEN)).json()["unread"] == 0


def test_another_principals_entry_answers_404() -> None:
    client, uow = build_client()
    _accept(uow)
    entry_id = client.get("/notification/centre", headers=auth(SENDER_TOKEN)).json()[
        "entries"
    ][0]["entry_id"]

    response = client.post(
        f"/notification/centre/{entry_id}/read", headers=auth(RECEIVER_TOKEN)
    )

    assert response.status_code == 404


# ------------------------------------------------------------------ bulletin


def test_only_operations_may_send_a_driver_bulletin() -> None:
    client, _ = build_client()

    response = client.post(
        "/notification/bulletins",
        json={
            "reference": "bulletin-1",
            "summary": "Hub cut-off moves to 19:00 tonight.",
            "driver_phones": ["+9647704444444"],
        },
        headers=auth(SENDER_TOKEN),
    )

    assert response.status_code == 403


def test_operations_reaches_drivers() -> None:
    client, _ = build_client()

    response = client.post(
        "/notification/bulletins",
        json={
            "reference": "bulletin-1",
            "summary": "Hub cut-off moves to 19:00 tonight.",
            "driver_phones": ["+9647704444444", "+9647705555555"],
            "driver_principal_ids": [str(DRIVER)],
        },
        headers=auth(OPS_TOKEN),
    )

    assert response.status_code == 201
    assert response.json()["planned"] == 2


# ------------------------------------------------------------------ disclosure


def test_the_support_view_needs_support_or_operations() -> None:
    client, uow = build_client()
    _accept(uow)

    denied = client.get(
        f"/notification/parcels/{TRACKING}/notifications", headers=auth(SENDER_TOKEN)
    )
    allowed = client.get(
        f"/notification/parcels/{TRACKING}/notifications", headers=auth(SUPPORT_TOKEN)
    )

    assert denied.status_code == 403
    assert allowed.status_code == 200


def test_the_support_view_carries_no_recipient_body_or_code() -> None:
    """Support answers "was I told?" without being shown what was said."""
    client, uow = build_client()
    _accept(uow)

    body = client.get(
        f"/notification/parcels/{TRACKING}/notifications", headers=auth(SUPPORT_TOKEN)
    ).text

    assert RECEIVER_PHONE not in body
    assert "2222222" not in body
    assert DELIVERY_CODE not in body
    assert "recipient_key" not in body


def test_no_response_anywhere_carries_a_rendered_message_body() -> None:
    client, uow = build_client()
    _accept(uow)

    support = client.get(
        f"/notification/parcels/{TRACKING}/notifications", headers=auth(SUPPORT_TOKEN)
    ).json()

    assert all("body" not in item for item in support["notifications"])


def test_the_openapi_surface_exposes_no_delivery_code_field() -> None:
    client, _ = build_client()
    schema = client.get("/openapi.json").text

    assert "delivery_code" not in schema
    assert "otp" not in schema.lower()
