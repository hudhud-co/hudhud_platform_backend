"""Saved products and product categories (MER-17)."""

from __future__ import annotations

import pytest
from merchant_fixtures import (
    approved_merchant,
    build_store,
    catalogue_service,
    new_id,
)

from merchant.domain.errors import (
    CategoryInUse,
    CategoryNotFound,
    ProductNameRequired,
    ProductNotFound,
)


def test_a_product_can_be_saved_and_reused() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)

    product = catalogue_service(uow).create_product(
        merchant_id=merchant.merchant_id,
        name="Cotton bedsheet set",
        description="Queen, 4 pieces",
    )

    assert product.name == "Cotton bedsheet set"
    assert product.is_active is True


def test_a_product_needs_a_name() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)

    with pytest.raises(ProductNameRequired):
        catalogue_service(uow).create_product(merchant_id=merchant.merchant_id, name="  ")


def test_products_can_be_grouped_into_categories() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    service = catalogue_service(uow)
    category = service.create_category(merchant_id=merchant.merchant_id, name="Bedding")

    service.create_product(
        merchant_id=merchant.merchant_id,
        name="Cotton bedsheet set",
        category_id=category.category_id,
    )
    service.create_product(merchant_id=merchant.merchant_id, name="Linen curtain")

    in_category = service.list_products(
        merchant_id=merchant.merchant_id, category_id=category.category_id
    )
    assert [product.name for product in in_category] == ["Cotton bedsheet set"]
    assert len(service.list_products(merchant_id=merchant.merchant_id)) == 2


def test_a_category_in_use_cannot_be_deleted() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    service = catalogue_service(uow)
    category = service.create_category(merchant_id=merchant.merchant_id, name="Bedding")
    service.create_product(
        merchant_id=merchant.merchant_id,
        name="Cotton bedsheet set",
        category_id=category.category_id,
    )

    with pytest.raises(CategoryInUse) as caught:
        service.delete_category(category_id=category.category_id)

    assert caught.value.product_count == 1


def test_an_empty_category_can_be_deleted() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    service = catalogue_service(uow)
    category = service.create_category(merchant_id=merchant.merchant_id, name="Bedding")

    deleted = service.delete_category(category_id=category.category_id)

    assert deleted.is_active is False
    assert service.list_categories(merchant_id=merchant.merchant_id) == ()


def test_archiving_a_product_frees_its_category() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    service = catalogue_service(uow)
    category = service.create_category(merchant_id=merchant.merchant_id, name="Bedding")
    product = service.create_product(
        merchant_id=merchant.merchant_id,
        name="Cotton bedsheet set",
        category_id=category.category_id,
    )

    service.archive_product(product_id=product.product_id)
    deleted = service.delete_category(category_id=category.category_id)

    assert deleted.is_active is False


def test_a_category_from_another_merchant_cannot_be_used() -> None:
    uow = build_store()
    first = approved_merchant(uow)
    second = approved_merchant(uow, applicant=new_id())
    service = catalogue_service(uow)
    category = service.create_category(merchant_id=first.merchant_id, name="Bedding")

    with pytest.raises(CategoryNotFound):
        service.create_product(
            merchant_id=second.merchant_id,
            name="Cotton bedsheet set",
            category_id=category.category_id,
        )


def test_an_archived_product_disappears_from_the_list() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    service = catalogue_service(uow)
    product = service.create_product(
        merchant_id=merchant.merchant_id, name="Linen curtain"
    )

    service.archive_product(product_id=product.product_id)

    assert service.list_products(merchant_id=merchant.merchant_id) == ()


def test_renaming_a_product_keeps_its_identity() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    service = catalogue_service(uow)
    product = service.create_product(
        merchant_id=merchant.merchant_id, name="Linen curtain"
    )

    renamed = service.update_product(
        product_id=product.product_id, name="Linen curtain · wide"
    )

    assert renamed.product_id == product.product_id
    assert renamed.name == "Linen curtain · wide"


def test_an_unknown_product_is_reported_as_missing() -> None:
    uow = build_store()

    with pytest.raises(ProductNotFound):
        catalogue_service(uow).archive_product(product_id=new_id())
