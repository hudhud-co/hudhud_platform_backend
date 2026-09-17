"""Pre-printed label stock and self-printing authorization (MER-03, MER-04)."""

from __future__ import annotations

import inspect

import pytest
from merchant_fixtures import approved_merchant, build_store, label_service, new_id

from merchant.application.label_stock_service import LabelStockService
from merchant.domain.errors import (
    LabelCountInvalid,
    LabelStockExhausted,
    MerchantNotFound,
    PrinterAuthorizationNotFound,
    SelfPrintingNotAuthorized,
)
from merchant.domain.value_objects import LabelStockSource, StockKind

# ------------------------------------------------------------------ MER-03


def test_labels_are_issued_as_stock_before_any_parcel_exists() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)

    allocation = label_service(uow).issue_stock(
        merchant_id=merchant.merchant_id, batch_reference="ROLL-001", label_count=50
    )

    assert allocation.source is LabelStockSource.HUDHUD_PREPRINTED
    assert allocation.label_count == 50
    assert allocation.remaining == 50


def test_there_is_no_way_to_print_a_label_for_a_shipment() -> None:
    """v6.3 p.10: labels are not printed on demand for each parcel.

    The absence of such an operation *is* the requirement, so assert it directly rather
    than trusting that nobody adds one later.
    """
    operations = {
        name: inspect.signature(member)
        for name, member in inspect.getmembers(LabelStockService, inspect.isfunction)
        if not name.startswith("_")
    }

    assert not [name for name in operations if "print_label" in name or "mint" in name]
    # No label operation may be addressed to a parcel: that is what "not on demand" means.
    parcel_scoped = {
        name: sorted(signature.parameters)
        for name, signature in operations.items()
        if {"shipment_id", "parcel_id", "order_id"} & set(signature.parameters)
    }
    assert parcel_scoped == {}


def test_stock_counts_down_as_labels_are_used() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    service = label_service(uow)
    allocation = service.issue_stock(
        merchant_id=merchant.merchant_id, batch_reference="ROLL-001", label_count=50
    )

    service.consume_labels(allocation_id=allocation.allocation_id, count=3)
    summary = service.summarize(merchant_id=merchant.merchant_id)

    assert summary.total_labels == 50
    assert summary.consumed_labels == 3
    assert summary.remaining == 47


def test_stock_cannot_be_overdrawn() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    service = label_service(uow)
    allocation = service.issue_stock(
        merchant_id=merchant.merchant_id, batch_reference="ROLL-001", label_count=2
    )

    with pytest.raises(LabelStockExhausted):
        service.consume_labels(allocation_id=allocation.allocation_id, count=3)

    assert (
        service.summarize(merchant_id=merchant.merchant_id).consumed_labels == 0
    )


def test_an_empty_allocation_is_refused() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)

    with pytest.raises(LabelCountInvalid):
        label_service(uow).issue_stock(
            merchant_id=merchant.merchant_id, batch_reference="ROLL-001", label_count=0
        )


def test_stock_cannot_be_issued_to_an_unknown_merchant() -> None:
    uow = build_store()

    with pytest.raises(MerchantNotFound):
        label_service(uow).issue_stock(
            merchant_id=new_id(), batch_reference="ROLL-001", label_count=10
        )


def test_the_dashboard_summary_spans_every_allocation() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    service = label_service(uow)
    service.issue_stock(
        merchant_id=merchant.merchant_id, batch_reference="ROLL-001", label_count=50
    )
    service.issue_stock(
        merchant_id=merchant.merchant_id, batch_reference="ROLL-002", label_count=25
    )

    assert service.summarize(merchant_id=merchant.merchant_id).total_labels == 75


# ------------------------------------------------------------------ MER-04


def test_self_printing_without_an_authorization_is_refused() -> None:
    """v6.3 p.12: no other printer or label stock may be used."""
    uow = build_store()
    merchant = approved_merchant(uow)

    with pytest.raises(SelfPrintingNotAuthorized):
        label_service(uow).issue_stock(
            merchant_id=merchant.merchant_id,
            batch_reference="SELF-001",
            label_count=10,
            source=LabelStockSource.MERCHANT_SELF_PRINTED,
        )


def test_self_printing_works_with_a_hudhud_issued_printer() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    service = label_service(uow)
    authorization = service.authorize_printer(
        merchant_id=merchant.merchant_id,
        printer_serial="ZD421-00917",
        stock_reference="HUDHUD-BLANK-58MM",
    )

    allocation = service.issue_stock(
        merchant_id=merchant.merchant_id,
        batch_reference="SELF-001",
        label_count=10,
        source=LabelStockSource.MERCHANT_SELF_PRINTED,
        printer_authorization_id=authorization.authorization_id,
    )

    assert allocation.printer_authorization_id == authorization.authorization_id


def test_a_revoked_printer_can_no_longer_be_used() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    service = label_service(uow)
    authorization = service.authorize_printer(
        merchant_id=merchant.merchant_id,
        printer_serial="ZD421-00917",
        stock_reference="HUDHUD-BLANK-58MM",
    )
    service.revoke_printer(authorization_id=authorization.authorization_id)

    with pytest.raises(SelfPrintingNotAuthorized):
        service.issue_stock(
            merchant_id=merchant.merchant_id,
            batch_reference="SELF-002",
            label_count=10,
            source=LabelStockSource.MERCHANT_SELF_PRINTED,
            printer_authorization_id=authorization.authorization_id,
        )


def test_another_merchants_printer_cannot_be_borrowed() -> None:
    uow = build_store()
    first = approved_merchant(uow)
    service = label_service(uow)
    authorization = service.authorize_printer(
        merchant_id=first.merchant_id,
        printer_serial="ZD421-00917",
        stock_reference="HUDHUD-BLANK-58MM",
    )
    second = approved_merchant(uow, applicant=new_id())

    with pytest.raises(SelfPrintingNotAuthorized):
        service.issue_stock(
            merchant_id=second.merchant_id,
            batch_reference="SELF-003",
            label_count=10,
            source=LabelStockSource.MERCHANT_SELF_PRINTED,
            printer_authorization_id=authorization.authorization_id,
        )


def test_revoking_an_unknown_authorization_is_refused() -> None:
    uow = build_store()

    with pytest.raises(PrinterAuthorizationNotFound):
        label_service(uow).revoke_printer(authorization_id=new_id())


def test_preprinted_stock_never_records_a_printer() -> None:
    """Attaching a printer to Hudhud-supplied stock would misstate its provenance."""
    uow = build_store()
    merchant = approved_merchant(uow)
    service = label_service(uow)
    authorization = service.authorize_printer(
        merchant_id=merchant.merchant_id,
        printer_serial="ZD421-00917",
        stock_reference="HUDHUD-BLANK-58MM",
    )

    allocation = service.issue_stock(
        merchant_id=merchant.merchant_id,
        batch_reference="ROLL-003",
        label_count=50,
        source=LabelStockSource.HUDHUD_PREPRINTED,
        printer_authorization_id=authorization.authorization_id,
    )

    assert allocation.printer_authorization_id is None


# ------------------------------------------------------------------ MER-12


def test_packaging_seals_are_issued_as_their_own_stock() -> None:
    """v6.3 p.14: a per-parcel seal carrying a unique scannable code."""
    uow = build_store()
    merchant = approved_merchant(uow)

    allocation = label_service(uow).issue_stock(
        merchant_id=merchant.merchant_id,
        batch_reference="SEAL-001",
        label_count=200,
        stock_kind=StockKind.PACKAGING_SEAL,
    )

    assert allocation.stock_kind is StockKind.PACKAGING_SEAL
    assert allocation.remaining == 200


def test_seal_stock_is_counted_separately_from_label_stock() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    service = label_service(uow)
    service.issue_stock(
        merchant_id=merchant.merchant_id, batch_reference="ROLL-001", label_count=50
    )
    seals = service.issue_stock(
        merchant_id=merchant.merchant_id,
        batch_reference="SEAL-001",
        label_count=200,
        stock_kind=StockKind.PACKAGING_SEAL,
    )
    service.consume_labels(allocation_id=seals.allocation_id, count=5)

    summary = service.summarize(merchant_id=merchant.merchant_id)

    assert summary.total_labels == 50
    assert summary.consumed_labels == 0
    assert summary.total_seals == 200
    assert summary.consumed_seals == 5
    assert summary.remaining_seals == 195


def test_a_merchant_can_never_self_print_a_packaging_seal() -> None:
    """Seals are Hudhud-supplied (p.14); self-printing one would defeat the seal."""
    uow = build_store()
    merchant = approved_merchant(uow)
    service = label_service(uow)
    authorization = service.authorize_printer(
        merchant_id=merchant.merchant_id,
        printer_serial="ZD421-00917",
        stock_reference="HUDHUD-BLANK-58MM",
    )

    with pytest.raises(SelfPrintingNotAuthorized):
        service.issue_stock(
            merchant_id=merchant.merchant_id,
            batch_reference="SEAL-002",
            label_count=100,
            source=LabelStockSource.MERCHANT_SELF_PRINTED,
            printer_authorization_id=authorization.authorization_id,
            stock_kind=StockKind.PACKAGING_SEAL,
        )


def test_seal_stock_cannot_be_overdrawn_either() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    service = label_service(uow)
    seals = service.issue_stock(
        merchant_id=merchant.merchant_id,
        batch_reference="SEAL-001",
        label_count=2,
        stock_kind=StockKind.PACKAGING_SEAL,
    )

    with pytest.raises(LabelStockExhausted):
        service.consume_labels(allocation_id=seals.allocation_id, count=3)
