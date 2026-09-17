"""Prove the Pickup driver endpoints work with real Identity authorization.

Before this wave Pickup shipped a default-deny authorizer, so every driver endpoint
answered 401 no matter who called it — the API existed but could not be used. This suite
boots the real Identity service over HTTP, signs a driver in with a real one-time code,
and drives Pickup's own endpoints with the resulting bearer token.

Nothing here is mocked: Identity runs in its own process and its own environment, and
Pickup runs in its own, talking to it over a socket.
"""

from __future__ import annotations

import json
import urllib.request

import pytest

from identity_pickup_integration.harness import (
    BOOTSTRAP_OPERATIONS_PHONE,
    SERVICE_CREDENTIAL,
    IdentityProcess,
    run_in_pickup,
)

pytestmark = pytest.mark.integration

DRIVER_PHONE = "+9647701820934"
OPS_PHONE = BOOTSTRAP_OPERATIONS_PHONE


def _post(base_url: str, path: str, payload: dict, headers: dict | None = None) -> dict:
    request = urllib.request.Request(  # noqa: S310
        f"{base_url}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            body = response.read().decode("utf-8")
            return {"status": response.status, "body": json.loads(body) if body else {}}
    except urllib.error.HTTPError as exc:  # type: ignore[attr-defined]
        body = exc.read().decode("utf-8")
        return {"status": exc.code, "body": json.loads(body) if body else {}}


def sign_in(identity: IdentityProcess, phone: str) -> dict:
    issued = _post(identity.base_url, "/identity/otp/request", {"phone": phone})
    assert issued["status"] == 200, issued
    challenge_id = issued["body"]["challenge_id"]
    code = identity.code_for(challenge_id)
    verified = _post(
        identity.base_url,
        "/identity/otp/verify",
        {"challenge_id": challenge_id, "code": code},
    )
    assert verified["status"] == 200, verified
    return verified["body"]


def grant_role(identity: IdentityProcess, *, ops_token: str, principal_id: str, role: str):
    return _post(
        identity.base_url,
        f"/identity/principals/{principal_id}/roles",
        {"role": role},
        headers={
            "Authorization": f"Bearer {ops_token}",
            "X-Service-Credential": SERVICE_CREDENTIAL,
        },
    )


# The snippet runs inside the Pickup service's own environment. It builds the Pickup app
# with the real Identity-backed authorizer and calls a driver endpoint over that adapter.
_PICKUP_SCRIPT = r"""
import json, os, sys
from uuid import uuid4
from fastapi.testclient import TestClient

from pickup.config import RuntimeEnvironment, load_settings
from pickup.infrastructure.fake_shipment_eligibility import InMemoryShipmentEligibilityAdapter
from pickup.infrastructure.memory import InMemoryPickupUnitOfWork
from pickup.main import create_app
from pickup.application.recovery_service import PickupRecoveryService, RegisterPickupTaskCommand
from pickup.domain.value_objects import AssignmentState, PickupTaskStatus
from datetime import UTC, datetime

token = os.environ["PROBE_TOKEN"]
store = InMemoryPickupUnitOfWork()
settings = load_settings(
    environment=RuntimeEnvironment.TEST,
    signing_key=os.environ["PROBE_PICKUP_SIGNING_KEY"],
    identity_base_url=os.environ["PROBE_IDENTITY_URL"],
    identity_service_credential=os.environ["PROBE_SERVICE_CREDENTIAL"],
)
app = create_app(
    settings,
    unit_of_work=store,
    shipment_eligibility=InMemoryShipmentEligibilityAdapter(production_ready=True),
)
authorizer = app.state.pickup_authorizer
result = {"authorizer": type(authorizer).__name__,
          "authorizer_production_ready": authorizer.is_production_ready}

# Seed one pickup task assigned to the authenticated driver principal.
driver_id = os.environ["PROBE_PRINCIPAL_ID"]
task_id, batch_id = uuid4(), uuid4()
PickupRecoveryService(store, InMemoryShipmentEligibilityAdapter()).register_pickup_task(
    RegisterPickupTaskCommand(
        pickup_task_id=task_id,
        shipment_id=uuid4(),
        assigned_driver_user_id=driver_id,
        assigned_batch_id=batch_id,
        status=PickupTaskStatus.PENDING,
        created_at=datetime.now(tz=UTC),
        assignment_state=AssignmentState.OFFERED,
    )
)

with TestClient(app) as client:
    auth = {"Authorization": "Bearer " + token}
    result["no_token"] = client.post("/pickup/work-sessions/start", json={}).status_code
    started = client.post("/pickup/work-sessions/start", json={}, headers=auth)
    result["start_session"] = started.status_code
    result["session_body"] = started.json() if started.status_code < 500 else None
    ack = client.post(f"/pickup/tasks/{task_id}/acknowledge", headers=auth)
    result["acknowledge"] = ack.status_code
    result["acknowledge_body"] = ack.json() if ack.status_code < 500 else None
    stop = client.get(f"/pickup/batches/{batch_id}/stop", headers=auth)
    result["stop"] = stop.status_code
    result["stop_body"] = stop.json() if stop.status_code < 500 else None
    bad = client.post("/pickup/work-sessions/start", json={},
                      headers={"Authorization": "Bearer not-a-real-token"})
    result["forged_token"] = bad.status_code

print("PROBE_RESULT " + json.dumps(result))
"""


def _run_probe(identity: IdentityProcess, *, token: str, principal_id: str) -> dict:
    completed = run_in_pickup(
        _PICKUP_SCRIPT,
        {
            "PROBE_TOKEN": token,
            "PROBE_PRINCIPAL_ID": principal_id,
            "PROBE_IDENTITY_URL": identity.base_url,
            "PROBE_SERVICE_CREDENTIAL": SERVICE_CREDENTIAL,
            "PROBE_PICKUP_SIGNING_KEY": "integration-pickup-signing-key-32c",
        },
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    for line in completed.stdout.splitlines():
        if line.startswith("PROBE_RESULT "):
            return json.loads(line.removeprefix("PROBE_RESULT "))
    msg = f"probe produced no result:\n{completed.stdout}\n{completed.stderr}"
    raise AssertionError(msg)


@pytest.fixture(scope="module")
def driver_session(identity_service: IdentityProcess) -> dict:
    """A driver principal that has actually been granted the PICKUP_DRIVER role."""
    ops = sign_in(identity_service, OPS_PHONE)
    assert "OPERATIONS" in ops["roles"], "the configured bootstrap phone must be operator"

    driver = sign_in(identity_service, DRIVER_PHONE)
    granted = grant_role(
        identity_service,
        ops_token=ops["access_token"],
        principal_id=driver["principal_id"],
        role="PICKUP_DRIVER",
    )
    assert granted["status"] == 200, granted
    return sign_in(identity_service, DRIVER_PHONE)


def test_a_driver_endpoint_rejects_a_call_with_no_token(
    identity_service: IdentityProcess,
) -> None:
    driver = sign_in(identity_service, DRIVER_PHONE)
    probe = _run_probe(
        identity_service, token=driver["access_token"], principal_id=driver["principal_id"]
    )

    assert probe["no_token"] == 401


def test_pickup_composes_the_real_identity_authorizer_when_configured(
    identity_service: IdentityProcess,
) -> None:
    driver = sign_in(identity_service, DRIVER_PHONE)
    probe = _run_probe(
        identity_service, token=driver["access_token"], principal_id=driver["principal_id"]
    )

    assert probe["authorizer"] == "IdentityIntrospectionAuthorizer"
    assert probe["authorizer_production_ready"] is True


def test_a_forged_token_is_rejected_by_real_introspection(
    identity_service: IdentityProcess,
) -> None:
    driver = sign_in(identity_service, DRIVER_PHONE)
    probe = _run_probe(
        identity_service, token=driver["access_token"], principal_id=driver["principal_id"]
    )

    assert probe["forged_token"] == 401


def test_a_customer_token_cannot_use_the_driver_endpoints(
    identity_service: IdentityProcess,
) -> None:
    """Authentication succeeds, authorization does not: the role check still bites."""
    customer = sign_in(identity_service, "+9647701820999")
    probe = _run_probe(
        identity_service,
        token=customer["access_token"],
        principal_id=customer["principal_id"],
    )

    assert probe["start_session"] == 403


def test_a_real_driver_token_makes_the_pickup_endpoints_usable(
    identity_service: IdentityProcess, driver_session: dict
) -> None:
    """The non-dormancy proof: the same endpoints that always answered 401 now work."""
    probe = _run_probe(
        identity_service,
        token=driver_session["access_token"],
        principal_id=driver_session["principal_id"],
    )

    assert probe["start_session"] == 200, probe
    assert probe["session_body"]["status"] == "ACTIVE"
    assert probe["acknowledge"] == 200, probe
    assert probe["acknowledge_body"]["assignment_state"] == "ACKNOWLEDGED"


def test_the_new_stop_endpoint_is_reachable_with_a_real_token(
    identity_service: IdentityProcess, driver_session: dict
) -> None:
    probe = _run_probe(
        identity_service,
        token=driver_session["access_token"],
        principal_id=driver_session["principal_id"],
    )

    assert probe["stop"] == 200, probe
    assert probe["stop_body"]["expected"] == 1
    assert probe["stop_body"]["can_complete"] is False
