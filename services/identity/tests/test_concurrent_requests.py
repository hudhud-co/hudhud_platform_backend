"""The unit of work must isolate concurrent requests.

`create_app` builds **one** `SqlAlchemyIdentityUnitOfWork` and stores it on
`app.state`, so every request shares that instance. With the transaction state
held as plain instance attributes, two requests in flight at once make the
second `begin()` find a session already open:

    RuntimeError: transaction already active

Observed live: 29 such 500s in the dev stack's identity log while two test
suites drove sign-in at the same time. Any two simultaneous users would do the
same, so this is a production defect rather than a test artefact.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from identity.config import RuntimeEnvironment, load_settings
from identity.infrastructure.adapters import (
    ConsoleOtpDelivery,
    SharedSecretServiceCredentials,
)
from identity.infrastructure.memory import InMemoryIdentityUnitOfWork
from identity.main import create_app

SIGNING_KEY = "concurrency-test-key"


@pytest.fixture
def client():
    app = create_app(
        load_settings(environment=RuntimeEnvironment.TEST, signing_key=SIGNING_KEY),
        unit_of_work=InMemoryIdentityUnitOfWork(),
        otp_delivery=ConsoleOtpDelivery(),
        service_credentials=SharedSecretServiceCredentials({"svc": "secret"}),
    )
    with TestClient(app) as test_client:
        yield test_client


def _request_otp(client: TestClient, suffix: int):
    return client.post(
        "/identity/otp/request", json={"phone": f"+96477000{suffix:05d}"}
    )


def test_concurrent_otp_requests_all_succeed(client: TestClient) -> None:

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(
            pool.map(lambda i: _request_otp(client, i), range(24))
        )

    failures = [r for r in responses if r.status_code != 200]
    assert not failures, (
        "concurrent OTP requests must not collide on a shared transaction; "
        f"got {[(r.status_code, r.text[:120]) for r in failures[:3]]}"
    )


def test_concurrent_requests_do_not_leak_each_others_challenges(
    client: TestClient,
) -> None:
    """Isolation, not just absence of errors.

    Each caller must get back its own challenge; a shared session could return
    another request's row.
    """

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(
            pool.map(lambda i: _request_otp(client, 100 + i), range(16))
        )

    assert all(r.status_code == 200 for r in responses)
    challenge_ids = {r.json()["challenge_id"] for r in responses}
    assert len(challenge_ids) == len(responses), (
        "every request must get its own challenge id"
    )

    last4 = [r.json()["phone_last4"] for r in responses]
    assert len(set(last4)) == len(responses), (
        "a shared session leaked another request's phone into the response"
    )
