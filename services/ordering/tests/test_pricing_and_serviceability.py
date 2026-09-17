"""Serviceability and the delivery-fee tariff."""

from __future__ import annotations

import pytest
from ordering_fixtures import at, build_store, pricing_service

from ordering.application.pricing_service import ServiceabilityPolicy
from ordering.domain.errors import (
    NotServiceable,
    TariffNotConfigured,
    UnknownGovernorate,
)
from ordering.domain.money import Money


def _rate(service, *, origin="KARBALA", destination="BAGHDAD", fee=4_250, packaging=1_500):
    return service.publish_rate(
        reference="TARIFF-2026-09",
        origin_governorate=origin,
        destination_governorate=destination,
        delivery_fee_minor_units=fee,
        packaging_fee_minor_units=packaging,
        effective_from=at(0, day=1),
    )


# ------------------------------------------------------------------ serviceability


def test_an_unconfigured_allowlist_serves_nowhere() -> None:
    """Quietly serving everywhere would promise deliveries the network cannot make."""
    uow = build_store()
    service = pricing_service(uow, serviceable=frozenset())

    assert service.serviceability.is_configured is False
    with pytest.raises(NotServiceable):
        service.assert_serviceable("BAGHDAD")


def test_a_configured_governorate_is_serviceable() -> None:
    uow = build_store()
    assert pricing_service(uow).is_serviceable("baghdad") is True


def test_a_governorate_outside_the_allowlist_is_refused() -> None:
    uow = build_store()
    service = pricing_service(uow, serviceable=frozenset({"BAGHDAD"}))

    with pytest.raises(NotServiceable):
        service.assert_serviceable("KARBALA")


def test_an_unknown_governorate_is_not_serviceable() -> None:
    uow = build_store()
    assert pricing_service(uow).is_serviceable("ATLANTIS") is False


def test_asserting_an_unknown_governorate_names_it() -> None:
    uow = build_store()
    with pytest.raises(UnknownGovernorate):
        pricing_service(uow).assert_serviceable("ATLANTIS")


# ------------------------------------------------------------------ tariff


def test_a_route_with_no_published_rate_is_refused_rather_than_guessed() -> None:
    """v6.3 publishes no tariff, so this service will not invent a price."""
    uow = build_store()

    with pytest.raises(TariffNotConfigured) as caught:
        pricing_service(uow).quote(
            origin_governorate="KARBALA", destination_governorate="BAGHDAD"
        )

    assert caught.value.origin == "KARBALA"
    assert caught.value.destination == "BAGHDAD"


def test_a_published_rate_produces_an_exact_quote() -> None:
    uow = build_store()
    service = pricing_service(uow)
    _rate(service)

    quote = service.quote(
        origin_governorate="KARBALA", destination_governorate="BAGHDAD"
    )

    assert quote.delivery_fee == Money(4_250)
    assert quote.packaging_fee == Money.zero()
    assert quote.total == Money(4_250)
    assert quote.tariff_reference == "TARIFF-2026-09"


def test_hudhud_packaging_adds_its_own_fee() -> None:
    uow = build_store()
    service = pricing_service(uow)
    _rate(service)

    quote = service.quote(
        origin_governorate="KARBALA",
        destination_governorate="BAGHDAD",
        hudhud_packaging=True,
    )

    assert quote.packaging_fee == Money(1_500)
    assert quote.total == Money(5_750)


def test_a_rate_outside_its_effective_window_does_not_apply() -> None:
    uow = build_store()
    service = pricing_service(uow)
    service.publish_rate(
        reference="OLD",
        origin_governorate="KARBALA",
        destination_governorate="BAGHDAD",
        delivery_fee_minor_units=3_000,
        packaging_fee_minor_units=1_000,
        effective_from=at(0, day=1),
        effective_to=at(0, day=10),
    )

    with pytest.raises(TariffNotConfigured):
        service.quote(
            origin_governorate="KARBALA",
            destination_governorate="BAGHDAD",
            moment=at(0, day=20),
        )


def test_the_most_recent_effective_rate_wins() -> None:
    uow = build_store()
    service = pricing_service(uow)
    service.publish_rate(
        reference="OLD",
        origin_governorate="KARBALA",
        destination_governorate="BAGHDAD",
        delivery_fee_minor_units=3_000,
        packaging_fee_minor_units=1_000,
        effective_from=at(0, day=1),
    )
    service.publish_rate(
        reference="NEW",
        origin_governorate="KARBALA",
        destination_governorate="BAGHDAD",
        delivery_fee_minor_units=4_250,
        packaging_fee_minor_units=1_500,
        effective_from=at(0, day=10),
    )

    quote = service.quote(
        origin_governorate="KARBALA",
        destination_governorate="BAGHDAD",
        moment=at(0, day=20),
    )

    assert quote.tariff_reference == "NEW"
    assert quote.delivery_fee == Money(4_250)


def test_a_quote_to_an_unserviceable_destination_is_refused_before_pricing() -> None:
    uow = build_store()
    service = pricing_service(uow, serviceable=frozenset({"KARBALA"}))

    with pytest.raises(NotServiceable):
        service.quote(origin_governorate="KARBALA", destination_governorate="BAGHDAD")


def test_published_rates_are_listable() -> None:
    uow = build_store()
    service = pricing_service(uow)
    _rate(service)

    assert len(service.list_rates()) == 1


def test_an_empty_policy_reports_itself_as_unconfigured() -> None:
    assert ServiceabilityPolicy().is_configured is False
    assert ServiceabilityPolicy(frozenset({"BAGHDAD"})).is_configured is True
