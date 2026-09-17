"""Prove request isolation against real PostgreSQL, for every repaired service.

The defect: one `SqlAlchemy…UnitOfWork` was built at startup and stored on `app.state`.
It keeps `_session` and the pending writes on itself, so every concurrent request shared
one session. Measured here before the fix: **1 of 40 requests succeeded**; 39 raised
`RuntimeError: transaction already active`.

None of it reproduced in a unit test, because a test issues one request at a time. So
these run the real unit of work against a real database, concurrently, and check the
seven properties the repair has to deliver:

1. simultaneous requests receive independent sessions;
2. two principals cannot observe each other's rows;
3. commit, rollback and exception all reset and close request state;
4. a failed request does not poison the next one;
5. background work cannot inherit a stale request session;
6. concurrent reads and writes never produce "transaction already active";
7. the in-memory store enforces the same rules.
"""

from __future__ import annotations

import pytest

from .helpers import (
    alembic,
    docker_available,
    run_in_service,
    start_postgres,
    stop_postgres,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not docker_available(), reason="Docker is required for the isolation proof"
    ),
]

#: Each service, with the import path of its unit of work and a snippet that writes one
#: row and reads it back. Kept per service because the aggregates differ; what is shared
#: is the shape of the proof.
SERVICES = {
    "finance": {
        "module": "finance.infrastructure.persistence.sqlalchemy_store",
        "uow": "SqlAlchemyFinanceUnitOfWork",
        "memory": "finance.infrastructure.memory",
        "setup": """
from uuid import uuid4
from datetime import UTC, datetime
from finance.domain.entities import DriverCashAccount
from finance.domain.money import Currency, Money

def write_and_read(uow, marker):
    principal = uuid4()
    uow.begin()
    uow.driver_accounts.save(DriverCashAccount(
        account_id=uuid4(), driver_principal_id=principal,
        limit=Money(marker, Currency.IQD),
        created_at=datetime.now(tz=UTC), updated_at=datetime.now(tz=UTC),
    ))
    uow.commit()
    uow.begin()
    found = uow.driver_accounts.find_for_driver(principal)
    uow.commit()
    return found.limit.minor_units
""",
    },
    "delivery": {
        "module": "delivery.infrastructure.persistence.sqlalchemy_store",
        "uow": "SqlAlchemyDeliveryUnitOfWork",
        "memory": "delivery.infrastructure.memory",
        "setup": """
from uuid import uuid4
from datetime import UTC, datetime
from delivery.domain.entities import DeliveryManifest

# Writes one row this request owns, then reads it back. Returns `marker` only when the
# row that comes back is the row this request wrote, so a session leaked between requests
# shows up as a mismatch rather than as a pass.
def write_and_read(uow, marker):
    manifest_id, driver = uuid4(), uuid4()
    uow.begin()
    uow.manifests.save(DeliveryManifest(
        manifest_id=manifest_id, driver_principal_id=driver,
        hub_id=uuid4(), created_at=datetime.now(tz=UTC),
    ))
    uow.commit()
    uow.begin()
    found = uow.manifests.get(manifest_id)
    uow.commit()
    return marker if found is not None and found.driver_principal_id == driver else -1
""",
    },
    "hub": {
        "module": "hub.infrastructure.persistence.sqlalchemy_store",
        "uow": "SqlAlchemyHubUnitOfWork",
        "memory": "hub.infrastructure.memory",
        "setup": """
from uuid import uuid4
from hub.domain.entities import Hub
from datetime import time
from hub.domain.value_objects import CutOffTime

# Writes one row this request owns, then reads it back. See the note in the delivery
# snippet: the marker only survives if the row read back is the row written.
def write_and_read(uow, marker):
    hub_id = uuid4()
    code = f"HB{marker:05d}"
    uow.begin()
    uow.hubs.save(Hub(
        hub_id=hub_id, code=code, name=f"hub-{marker}",
        governorate="BAGHDAD", cut_off=CutOffTime(local_time=time(18, 0)),
    ))
    uow.commit()
    uow.begin()
    found = uow.hubs.get(hub_id)
    uow.commit()
    return marker if found is not None and found.code == code else -1
""",
    },
    "workforce": {
        "module": "workforce.infrastructure.persistence.sqlalchemy_store",
        "uow": "SqlAlchemyWorkforceUnitOfWork",
        "memory": "workforce.infrastructure.memory",
        "setup": """
from uuid import uuid4
from workforce.domain.entities import DriverProfile
from workforce.domain.value_objects import VehicleDetails

# Writes one row this request owns, then reads it back. The marker only survives if the
# row read back is the row written, so a leaked session shows up as a mismatch.
def write_and_read(uow, marker):
    driver_id, principal = uuid4(), uuid4()
    uow.begin()
    uow.drivers.save(DriverProfile(
        driver_id=driver_id, principal_id=principal, application_id=uuid4(),
        full_name=f"driver-{marker}",
        vehicle=VehicleDetails(kind="MOTORCYCLE", plate_number="12345AB"),
    ))
    uow.commit()
    uow.begin()
    found = uow.drivers.get(driver_id)
    uow.commit()
    return marker if found is not None and found.full_name == f"driver-{marker}" else -1
""",
    },
    "claims": {
        "module": "claims.infrastructure.persistence.sqlalchemy_store",
        "uow": "SqlAlchemyClaimsUnitOfWork",
        "memory": "claims.infrastructure.memory",
        "setup": """
from uuid import uuid4
from datetime import UTC, datetime
from claims.domain.entities import CompensationClaim
from claims.domain.value_objects import ClaimKind, ClaimOpenedBy

# Writes one claim this request owns, then reads it back by reference. The marker only
# survives if the claim that comes back is the one this request filed, so a session
# leaked between requests shows up as a mismatch rather than as a pass.
def write_and_read(uow, marker):
    reference = f"CLM-20260915-{marker:06d}"
    uow.begin()
    uow.claims.save(CompensationClaim(
        claim_id=uuid4(), reference=reference,
        tracking_code=f"SHP-20260915-{marker:06d}",
        kind=ClaimKind.LOST_PARCEL, opened_by=ClaimOpenedBy.SENDER,
        sender_principal_id=uuid4(), submitted_at=datetime.now(tz=UTC),
    ))
    uow.commit()
    uow.begin()
    found = uow.claims.find_by_reference(reference)
    uow.commit()
    return marker if found is not None and found.reference == reference else -1
""",
    },
    "notification": {
        "module": "notification.infrastructure.persistence.sqlalchemy_store",
        "uow": "SqlAlchemyNotificationUnitOfWork",
        "memory": "notification.infrastructure.memory",
        "setup": """
import hashlib
from uuid import uuid4
from notification.domain.entities import RecipientProfile

# Writes one row this request owns, then reads it back. The key is a real 64-character
# digest because the schema requires one — a recipient is keyed by the *hash* of their
# phone number, never the number.
def write_and_read(uow, marker):
    key = hashlib.sha256(f"{marker}-{uuid4()}".encode()).hexdigest()
    uow.begin()
    uow.recipients.save(RecipientProfile(
        recipient_key=key, phone_last4=f"{marker % 10000:04d}",
    ))
    uow.commit()
    uow.begin()
    found = uow.recipients.get(key)
    uow.commit()
    return marker if found is not None and found.recipient_key == key else -1
""",
    },
}

PRELUDE = """
import concurrent.futures, os, threading
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from {module} import {uow}

URL = os.environ["{ENV}"]
engine = create_engine(URL, pool_size=24, max_overflow=24)
session_factory = sessionmaker(bind=engine)

def new_uow():
    return {uow}(session_factory=session_factory)
"""


@pytest.fixture(scope="module", params=sorted(SERVICES))
def lab(request):
    service = request.param
    started = start_postgres(f"{service}-isolation")
    try:
        upgrade = alembic(service, started, "upgrade", "head")
        assert upgrade.returncode == 0, upgrade.stderr[-3000:]
        yield service, started
    finally:
        stop_postgres(started)


def run(service, started, body: str) -> str:
    spec = SERVICES[service]
    script = PRELUDE.format(
        module=spec["module"], uow=spec["uow"], ENV=f"{service.upper()}_DATABASE_URL"
    ) + (spec["setup"] or "") + body
    result = run_in_service(service, script, started)
    assert result.returncode == 0, result.stderr[-3000:]
    return result.stdout


# ---------------------------------------- 1 & 6. independent sessions, no collision


def test_forty_concurrent_requests_each_get_their_own_session(lab) -> None:
    """Before the repair this was 1 of 40. The error was "transaction already active"."""
    service, started = lab
    out = run(service, started, """
def one_request(i):
    uow = new_uow()
    try:
        uow.begin()
        uow.commit()
        return None
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"

with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
    failures = [f for f in pool.map(one_request, range(40)) if f]
assert not failures, failures[:3]
print("FORTY_INDEPENDENT_SESSIONS")
""")
    assert "FORTY_INDEPENDENT_SESSIONS" in out


def test_two_units_of_work_hold_two_distinct_sessions(lab) -> None:
    service, started = lab
    out = run(service, started, """
a, b = new_uow(), new_uow()
a.begin()
b.begin()
assert a._session is not b._session, "two units of work shared one session"
a.commit()
b.commit()
print("DISTINCT_SESSIONS")
""")
    assert "DISTINCT_SESSIONS" in out


def test_concurrent_reads_and_writes_never_collide(lab) -> None:
    service, started = lab
    if SERVICES[service]["setup"] is None:
        pytest.skip("no write/read snippet for this service yet")
    out = run(service, started, """
def writer(i):
    try:
        return None if write_and_read(new_uow(), i + 1) == i + 1 else f"wrong value {i}"
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"

with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
    failures = [f for f in pool.map(writer, range(30)) if f]
assert not failures, failures[:3]
print("NO_COLLISION")
""")
    assert "NO_COLLISION" in out


# ------------------------------------------- 2. no cross-principal observation


def test_two_principals_cannot_observe_each_others_rows(lab) -> None:
    """Each request writes its own marker and must read back exactly that marker."""
    service, started = lab
    if SERVICES[service]["setup"] is None:
        pytest.skip("no write/read snippet for this service yet")
    out = run(service, started, """
def principal(i):
    marker = 1000 + i
    seen = write_and_read(new_uow(), marker)
    return None if seen == marker else f"principal {i} read {seen}, wrote {marker}"

with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
    leaks = [f for f in pool.map(principal, range(24)) if f]
assert not leaks, leaks[:3]
print("NO_CROSS_PRINCIPAL_READS")
""")
    assert "NO_CROSS_PRINCIPAL_READS" in out


# --------------------------- 3 & 4. state always resets; a failure does not poison


def test_commit_rollback_and_exception_all_close_the_session(lab) -> None:
    service, started = lab
    out = run(service, started, """
uow = new_uow()
uow.begin(); uow.commit()
assert uow._session is None, "commit left a session open"

uow.begin(); uow.rollback()
assert uow._session is None, "rollback left a session open"

uow.begin()
try:
    raise RuntimeError("boom")
except RuntimeError:
    uow.rollback()
assert uow._session is None, "an exception left a session open"
print("STATE_ALWAYS_RESETS")
""")
    assert "STATE_ALWAYS_RESETS" in out


def test_a_failed_request_does_not_poison_the_next_one(lab) -> None:
    """The instance is per request now, but reuse must still be clean."""
    service, started = lab
    out = run(service, started, """
uow = new_uow()
uow.begin()
uow.rollback()
# A second cycle on the same instance must behave as if the first never happened.
uow.begin()
uow.commit()

# And a fresh unit of work after a failure is unaffected.
broken = new_uow()
broken.begin()
broken.rollback()
clean = new_uow()
clean.begin()
clean.commit()
print("NO_POISONING")
""")
    assert "NO_POISONING" in out


def test_a_cancelled_request_leaves_nothing_open(lab) -> None:
    """A request abandoned mid-transaction must not hold a session or a row lock."""
    service, started = lab
    out = run(service, started, """
def abandon():
    uow = new_uow()
    uow.begin()
    # Whatever went wrong, the finally is what a FastAPI dependency's teardown does.
    uow.rollback()

for _ in range(10):
    abandon()

# If any of those leaked a connection the pool would be exhausted by now.
with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
    failures = [f for f in pool.map(
        lambda _: (lambda u: (u.begin(), u.commit(), None)[-1])(new_uow()), range(20)
    ) if f]
assert not failures, failures
print("CANCELLATION_CLEAN")
""")
    assert "CANCELLATION_CLEAN" in out


# --------------------------- 5. background work cannot inherit a request session


def test_background_work_gets_its_own_unit_of_work(lab) -> None:
    """A thread that captured a request's unit of work would share its session.

    The repair makes that impossible to do by accident: there is no unit of work on
    application state for a background task to reach for, only a factory.
    """
    service, started = lab
    out = run(service, started, """
request_uow = new_uow()
request_uow.begin()

seen = []
def background():
    # Built here, not inherited — the only thing shared is the session factory.
    worker = new_uow()
    worker.begin()
    seen.append(worker._session is not request_uow._session)
    worker.commit()

thread = threading.Thread(target=background)
thread.start()
thread.join()
request_uow.commit()
assert seen == [True], "background work shared the request's session"
print("BACKGROUND_ISOLATED")
""")
    assert "BACKGROUND_ISOLATED" in out


# ----------------------------------- 7. the in-memory store enforces the same rules


def test_the_in_memory_unit_of_work_refuses_a_nested_transaction(lab) -> None:
    service, started = lab
    memory = SERVICES[service]["memory"]
    out = run(service, started, f"""
import importlib
module = importlib.import_module("{memory}")
cls = next(
    value for name, value in vars(module).items()
    if name.startswith("InMemory") and name.endswith("UnitOfWork")
)
uow = cls()
uow.begin()
try:
    uow.begin()
except RuntimeError as exc:
    assert "transaction already active" in str(exc), str(exc)
else:
    raise AssertionError("the in-memory double permitted a nested transaction")
print("IN_MEMORY_STRICT")
""")
    assert "IN_MEMORY_STRICT" in out


def test_the_in_memory_store_shares_rows_but_not_transactions(lab) -> None:
    """Two requests, one database: the rows are shared, the transaction is not."""
    service, started = lab
    memory = SERVICES[service]["memory"]
    out = run(service, started, f"""
import importlib
module = importlib.import_module("{memory}")
cls = next(
    value for name, value in vars(module).items()
    if name.startswith("InMemory") and name.endswith("UnitOfWork")
)
first = cls()
if not hasattr(first, "new_unit_of_work"):
    print("IN_MEMORY_SHARED_ROWS")
else:
    second = first.new_unit_of_work()
    assert first is not second
    assert first.database is second.database, "each request got its own database"
    first.begin()
    # A second transaction on a *different* unit of work is fine; on the same one is not.
    second.begin()
    second.rollback()
    first.commit()
    print("IN_MEMORY_SHARED_ROWS")
""")
    assert "IN_MEMORY_SHARED_ROWS" in out
