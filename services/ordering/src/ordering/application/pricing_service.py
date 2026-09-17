"""Serviceability and the delivery-fee quote.

Two things are deliberately data rather than code.

Serviceability is an allowlist of governorates an operator maintains: where Hudhud
delivers changes with the network, not with a release.

The tariff is a table of exact amounts in minor units. v6.3 publishes no rates, so this
service refuses to quote a route it has no rate for rather than inventing a price. The
numbers in the app's prototype screens are fixtures, not a published tariff, and are not
copied here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from ordering.domain.entities import TariffRate
from ordering.domain.errors import (
    NotServiceable,
    TariffNotConfigured,
    UnknownGovernorate,
)
from ordering.domain.money import Currency, Money
from ordering.domain.value_objects import PriceQuote, normalize_governorate
from ordering.ports.repository import OrderingUnitOfWork


def _now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class ServiceabilityPolicy:
    """Where Hudhud currently delivers.

    An empty allowlist means "not configured", and every route is refused. That is the
    fail-closed reading: quietly serving everywhere would promise deliveries the network
    cannot make.
    """

    serviceable_governorates: frozenset[str] = frozenset()

    @property
    def is_configured(self) -> bool:
        return bool(self.serviceable_governorates)

    def covers(self, governorate: str) -> bool:
        return governorate in self.serviceable_governorates


class PricingService:
    def __init__(
        self,
        unit_of_work: OrderingUnitOfWork,
        *,
        serviceability: ServiceabilityPolicy | None = None,
    ) -> None:
        self._uow = unit_of_work
        self._serviceability = serviceability or ServiceabilityPolicy()

    # ------------------------------------------------------------- serviceability

    @property
    def serviceability(self) -> ServiceabilityPolicy:
        return self._serviceability

    def assert_serviceable(self, governorate: str) -> str:
        token = self._normalize(governorate)
        if not self._serviceability.covers(token):
            raise NotServiceable(token)
        return token

    def is_serviceable(self, governorate: str) -> bool:
        try:
            return self._serviceability.covers(self._normalize(governorate))
        except UnknownGovernorate:
            return False

    # ------------------------------------------------------------- tariff

    def quote(
        self,
        *,
        origin_governorate: str,
        destination_governorate: str,
        hudhud_packaging: bool = False,
        moment: datetime | None = None,
    ) -> PriceQuote:
        origin = self.assert_serviceable(origin_governorate)
        destination = self.assert_serviceable(destination_governorate)
        when = moment or _now()

        self._uow.begin()
        try:
            rate = self._uow.tariffs.find_rate(
                origin=origin, destination=destination, moment=when
            )
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        if rate is None:
            raise TariffNotConfigured(origin, destination)

        packaging = (
            rate.packaging_fee
            if hudhud_packaging
            else Money.zero(rate.delivery_fee.currency)
        )
        return PriceQuote(
            delivery_fee=rate.delivery_fee,
            packaging_fee=packaging,
            tariff_reference=rate.reference,
        )

    def publish_rate(
        self,
        *,
        reference: str,
        origin_governorate: str,
        destination_governorate: str,
        delivery_fee_minor_units: int,
        packaging_fee_minor_units: int,
        currency: Currency = Currency.IQD,
        effective_from: datetime | None = None,
        effective_to: datetime | None = None,
    ) -> TariffRate:
        """Load an approved rate. Amounts are integers — never a float or a string."""
        origin = self._normalize(origin_governorate)
        destination = self._normalize(destination_governorate)
        self._uow.begin()
        try:
            rate = TariffRate(
                tariff_id=uuid4(),
                reference=reference.strip(),
                origin_governorate=origin,
                destination_governorate=destination,
                delivery_fee=Money(delivery_fee_minor_units, currency),
                packaging_fee=Money(packaging_fee_minor_units, currency),
                effective_from=effective_from or _now(),
                effective_to=effective_to,
            )
            self._uow.tariffs.save(rate)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return rate

    def list_rates(self) -> tuple[TariffRate, ...]:
        self._uow.begin()
        try:
            rates = self._uow.tariffs.list_rates()
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return rates

    # ------------------------------------------------------------- internals

    @staticmethod
    def _normalize(governorate: str) -> str:
        try:
            return normalize_governorate(governorate)
        except ValueError as exc:
            raise UnknownGovernorate(governorate) from exc
