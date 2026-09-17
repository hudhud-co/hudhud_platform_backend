"""The Delivery unit of work against the database its own migration built.

Three things cannot be proved by any in-memory double, and all three are the kind that
only fail under load:

* a version-conditional UPDATE really does refuse a stale write;
* a rollback really does discard the outbox row written beside the state change;
* the check constraints really do refuse the rows the domain refuses.

So this test applies the migration for real and drives the SQLAlchemy store through it.
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
        not docker_available(), reason="Docker is required for the store proof"
    ),
]

SERVICE = "delivery"

_PRELUDE = """
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from delivery.domain.entities import DeliveryManifest, DeliveryStop, ParcelSettings
from delivery.domain.errors import StaleDeliveryRecord
from delivery.domain.messaging import OutboxRecord, OutboxStatus
from delivery.domain.money import Currency, Money
from delivery.domain.value_objects import PaymentMethod, StopStatus
from delivery.infrastructure.persistence.sqlalchemy_store import (
    SqlAlchemyDeliveryUnitOfWork,
)

import os

URL = os.environ["DELIVERY_DATABASE_URL"]
engine = create_engine(URL)
uow = SqlAlchemyDeliveryUnitOfWork(session_factory=sessionmaker(bind=engine))


def now():
    return datetime.now(tz=UTC)


def new_manifest():
    manifest = DeliveryManifest(
        manifest_id=uuid4(),
        driver_principal_id=uuid4(),
        hub_id=uuid4(),
        created_at=now(),
    )
    uow.begin()
    uow.manifests.save(manifest)
    uow.commit()
    return manifest


def new_stop(manifest, tracking_code, **kwargs):
    stop = DeliveryStop(
        stop_id=uuid4(),
        manifest_id=manifest.manifest_id,
        tracking_code=tracking_code,
        driver_principal_id=manifest.driver_principal_id,
        status=StopStatus.IN_CUSTODY,
        custody_taken_at=now(),
        **kwargs,
    )
    uow.begin()
    uow.stops.save(stop)
    uow.commit()
    return stop
"""


@pytest.fixture(scope="module")
def lab():
    started = start_postgres(f"{SERVICE}-store")
    try:
        upgrade = alembic(SERVICE, started, "upgrade", "head")
        assert upgrade.returncode == 0, upgrade.stderr[-4000:]
        yield started
    finally:
        stop_postgres(started)


def run(lab, body: str):
    result = run_in_service(SERVICE, _PRELUDE + body, lab)
    assert result.returncode == 0, result.stderr[-4000:]
    return result.stdout


def test_a_stop_round_trips_through_postgres(lab) -> None:
    output = run(
        lab,
        """
manifest = new_manifest()
stop = new_stop(
    manifest,
    "SHP-20260915-000001",
    settings=ParcelSettings(open_box_allowed=True, packaging_seal_code="SEAL-1"),
    cod_amount=Money(minor_units=25000, currency=Currency.IQD),
    payment_method_expected=PaymentMethod.CASH,
    delivery_code_digest="a" * 64,
    named_receiver="Zaid Al-Rawi",
)
uow.begin()
read = uow.stops.get(stop.stop_id)
uow.commit()
assert read.tracking_code == stop.tracking_code
assert read.settings.open_box_allowed is True
assert read.settings.packaging_seal_code == "SEAL-1"
assert read.cod_amount.minor_units == 25000
assert read.cod_amount.currency is Currency.IQD
assert read.payment_method_expected is PaymentMethod.CASH
assert read.named_receiver == "Zaid Al-Rawi"
print("ROUNDTRIP_OK")
""",
    )
    assert "ROUNDTRIP_OK" in output


def test_money_survives_as_an_exact_integer(lab) -> None:
    """A large COD must not lose a dinar to a float or an int32."""
    output = run(
        lab,
        """
manifest = new_manifest()
huge = 9_000_000_000
stop = new_stop(
    manifest,
    "SHP-20260915-000002",
    cod_amount=Money(minor_units=huge, currency=Currency.IQD),
    payment_method_expected=PaymentMethod.CASH,
)
uow.begin()
read = uow.stops.get(stop.stop_id)
uow.commit()
assert read.cod_amount.minor_units == huge, read.cod_amount.minor_units
assert isinstance(read.cod_amount.minor_units, int)
print("MONEY_OK")
""",
    )
    assert "MONEY_OK" in output


def test_a_stale_write_is_refused(lab) -> None:
    """Two devices racing on one stop: the second must lose, not silently win."""
    output = run(
        lab,
        """
manifest = new_manifest()
stop = new_stop(manifest, "SHP-20260915-000003")

uow.begin()
first = uow.stops.get(stop.stop_id)
uow.commit()
uow.begin()
second = uow.stops.get(stop.stop_id)
uow.commit()

first.status = StopStatus.EN_ROUTE
first.departed_at = now()
first.version += 1
uow.begin()
uow.stops.save(first)
uow.commit()

second.status = StopStatus.FAILED
second.closed_at = now()
second.version += 1
uow.begin()
uow.stops.save(second)
try:
    uow.commit()
except StaleDeliveryRecord:
    print("STALE_REFUSED")
else:
    raise AssertionError("the stale write was accepted")
""",
    )
    assert "STALE_REFUSED" in output


def test_a_rollback_discards_the_outbox_row_too(lab) -> None:
    """The whole point of a transactional outbox: no fact without its state change."""
    output = run(
        lab,
        """
manifest = new_manifest()
stop = new_stop(manifest, "SHP-20260915-000004")
event_id = uuid4()

uow.begin()
current = uow.stops.get(stop.stop_id)
current.status = StopStatus.EN_ROUTE
current.departed_at = now()
current.version += 1
uow.stops.save(current)
uow.outbox.insert(
    OutboxRecord(
        id=uuid4(),
        event_id=event_id,
        subject="hudhud.delivery.delivery.fact.departed_for_receiver.v1",
        event_type="delivery.fact.departed_for_receiver",
        event_version=1,
        aggregate_id=stop.stop_id,
        aggregate_version=current.version,
        payload_json={"payload": {}},
        status=OutboxStatus.PENDING,
        attempt_count=0,
        max_attempts=5,
        next_attempt_at=now(),
        created_at=now(),
    )
)
uow.rollback()

uow.begin()
after = uow.stops.get(stop.stop_id)
published = uow.outbox.get_by_event_id(event_id)
uow.commit()
assert after.status is StopStatus.IN_CUSTODY, after.status
assert published is None
print("ROLLBACK_OK")
""",
    )
    assert "ROLLBACK_OK" in output


def test_the_outbox_refuses_two_facts_at_one_aggregate_version(lab) -> None:
    output = run(
        lab,
        """
from sqlalchemy.exc import IntegrityError

manifest = new_manifest()
stop = new_stop(manifest, "SHP-20260915-000005")


def record(event_id, version):
    return OutboxRecord(
        id=uuid4(),
        event_id=event_id,
        subject="hudhud.delivery.delivery.fact.delivered.v1",
        event_type="delivery.fact.delivered",
        event_version=1,
        aggregate_id=stop.stop_id,
        aggregate_version=version,
        payload_json={"payload": {}},
        status=OutboxStatus.PENDING,
        attempt_count=0,
        max_attempts=5,
        next_attempt_at=now(),
        created_at=now(),
    )


uow.begin()
uow.outbox.insert(record(uuid4(), 2))
uow.commit()

uow.begin()
uow.outbox.insert(record(uuid4(), 2))
try:
    uow.commit()
except IntegrityError:
    print("VERSION_COLLISION_REFUSED")
else:
    raise AssertionError("two facts shared one aggregate version")
""",
    )
    assert "VERSION_COLLISION_REFUSED" in output


def test_postgres_refuses_a_delivered_stop_that_was_never_verified(lab) -> None:
    """The rule from v6.3 p.26, enforced where a direct UPDATE cannot dodge it."""
    output = run(
        lab,
        """
from sqlalchemy.exc import IntegrityError

manifest = new_manifest()
stop = new_stop(manifest, "SHP-20260915-000006")
with engine.begin() as connection:
    try:
        connection.execute(
            text(
                "UPDATE delivery_stops SET status = 'DELIVERED', closed_at = now() "
                "WHERE stop_id = :stop_id"
            ),
            {"stop_id": str(stop.stop_id)},
        )
    except IntegrityError:
        print("UNVERIFIED_DELIVERY_REFUSED")
    else:
        raise AssertionError("an unverified delivery was accepted")
""",
    )
    assert "UNVERIFIED_DELIVERY_REFUSED" in output


def test_postgres_refuses_a_second_live_stop_for_one_parcel(lab) -> None:
    output = run(
        lab,
        """
from sqlalchemy.exc import IntegrityError

manifest = new_manifest()
new_stop(manifest, "SHP-20260915-000007")
try:
    new_stop(manifest, "SHP-20260915-000007")
except IntegrityError:
    uow.rollback()
    print("SECOND_LIVE_STOP_REFUSED")
else:
    raise AssertionError("a parcel was live on two stops at once")
""",
    )
    assert "SECOND_LIVE_STOP_REFUSED" in output


def test_postgres_refuses_a_digest_that_is_not_a_digest(lab) -> None:
    """DRV-L05 — a short value here would mean a code had been written in the clear."""
    output = run(
        lab,
        """
from sqlalchemy.exc import IntegrityError

manifest = new_manifest()
try:
    new_stop(manifest, "SHP-20260915-000008", delivery_code_digest="482913")
except IntegrityError:
    uow.rollback()
    print("SHORT_DIGEST_REFUSED")
else:
    raise AssertionError("a six-character value was stored as a digest")
""",
    )
    assert "SHORT_DIGEST_REFUSED" in output
