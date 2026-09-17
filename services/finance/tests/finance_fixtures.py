"""Shared builders for the Finance test suite."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from finance.application.cash_service import CashService
from finance.application.cod_service import CodService
from finance.application.settlement_service import SettlementService
from finance.domain.money import Currency, Money
from finance.domain.value_objects import CodPaymentChannel, EvidenceMediaRef
from finance.infrastructure.memory import InMemoryUnitOfWork
from finance.ports.authorization import FinanceActor, FinanceRole

#: Limits and tariffs chosen by the test, never by the service. v6.3 states that a
#: driver cash limit and a return-trip fee exist; it never states what they are.
TEST_CASH_LIMIT = 1_000_000
TEST_RETURN_TRIP_FEE = 5_000


def iqd(minor_units: int) -> Money:
    return Money(minor_units=minor_units, currency=Currency.IQD)


def receipt(key: str = "receipt.jpg") -> EvidenceMediaRef:
    return EvidenceMediaRef(bucket="finance-evidence", key=key)


def actor(*roles: FinanceRole, principal_id: UUID | None = None, merchant_id=None):
    return FinanceActor(
        principal_id=principal_id or uuid4(),
        roles=frozenset(roles),
        merchant_id=merchant_id,
    )


def driver_actor(principal_id: UUID):
    return actor(FinanceRole.LAST_MILE_DRIVER, principal_id=principal_id)


def cashier():
    return actor(FinanceRole.HUB_CASHIER)


def accountant():
    return actor(FinanceRole.ACCOUNTANT)


def operations():
    return actor(FinanceRole.OPERATIONS)


@dataclass
class Lab:
    uow: InMemoryUnitOfWork
    cash: CashService
    cod: CodService
    settlement: SettlementService
    driver_id: UUID
    merchant_id: UUID


def build_lab(
    *,
    cash_limit: int = TEST_CASH_LIMIT,
    return_trip_fee: int | None = TEST_RETURN_TRIP_FEE,
    waiver_permitted: bool = False,
    driver_id: UUID | None = None,
    merchant_id: UUID | None = None,
) -> Lab:
    uow = InMemoryUnitOfWork()
    cash = CashService(uow, default_cash_limit=iqd(cash_limit))
    cod = CodService(uow)
    settlement = SettlementService(
        uow,
        return_trip_fee=iqd(return_trip_fee) if return_trip_fee is not None else None,
        return_fee_waiver_permitted=waiver_permitted,
    )
    driver = driver_id or uuid4()
    merchant = merchant_id or uuid4()
    cash.open_account(driver_principal_id=driver)
    settlement.open_merchant_account(merchant_id=merchant)
    return Lab(
        uow=uow,
        cash=cash,
        cod=cod,
        settlement=settlement,
        driver_id=driver,
        merchant_id=merchant,
    )


_SEQUENCE = {"n": 0}


def next_tracking_code() -> str:
    _SEQUENCE["n"] += 1
    return f"SHP-20260915-{_SEQUENCE['n']:06d}"


def collect_cash(lab: Lab, *, goods: int = 100_000, fee: int = 5_000) -> str:
    """Take one parcel's COD in cash, into the driver's custody."""
    code = next_tracking_code()
    lab.cod.record_collection(
        tracking_code=code,
        merchant_id=lab.merchant_id,
        channel=CodPaymentChannel.CASH,
        goods_amount=iqd(goods),
        delivery_fee=iqd(fee),
        driver_principal_id=lab.driver_id,
    )
    return code
