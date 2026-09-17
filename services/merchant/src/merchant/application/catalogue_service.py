"""Saved products and product categories (MER-17)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from merchant.domain.entities import Product, ProductCategory
from merchant.domain.errors import (
    CategoryInUse,
    CategoryNotFound,
    MerchantNotActive,
    MerchantNotFound,
    ProductNameRequired,
    ProductNotFound,
)
from merchant.ports.repository import MerchantUnitOfWork


def _now() -> datetime:
    return datetime.now(tz=UTC)


class CatalogueService:
    def __init__(self, unit_of_work: MerchantUnitOfWork) -> None:
        self._uow = unit_of_work

    # ------------------------------------------------------------- categories

    def create_category(self, *, merchant_id: UUID, name: str) -> ProductCategory:
        self._uow.begin()
        try:
            self._require_active_merchant(merchant_id)
            category = ProductCategory(
                category_id=uuid4(),
                merchant_id=merchant_id,
                name=name.strip(),
                created_at=_now(),
            )
            self._uow.catalogue.save_category(category)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return category

    def rename_category(self, *, category_id: UUID, name: str) -> ProductCategory:
        self._uow.begin()
        try:
            category = self._load_category(category_id)
            category.name = name.strip()
            category.version += 1
            self._uow.catalogue.save_category(category)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return category

    def delete_category(self, *, category_id: UUID) -> ProductCategory:
        """Refuse while products still point at it, rather than orphaning them."""
        self._uow.begin()
        try:
            category = self._load_category(category_id)
            in_use = self._uow.catalogue.count_products_in_category(category_id)
            if in_use:
                raise CategoryInUse(in_use)
            category.archived_at = _now()
            category.version += 1
            self._uow.catalogue.save_category(category)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return category

    def list_categories(self, *, merchant_id: UUID) -> tuple[ProductCategory, ...]:
        self._uow.begin()
        try:
            found = self._uow.catalogue.list_categories(merchant_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    # ------------------------------------------------------------- products

    def create_product(
        self,
        *,
        merchant_id: UUID,
        name: str,
        category_id: UUID | None = None,
        description: str | None = None,
    ) -> Product:
        if not name.strip():
            raise ProductNameRequired()
        self._uow.begin()
        try:
            self._require_active_merchant(merchant_id)
            if category_id is not None:
                category = self._load_category(category_id)
                if category.merchant_id != merchant_id:
                    raise CategoryNotFound(str(category_id))
            product = Product(
                product_id=uuid4(),
                merchant_id=merchant_id,
                name=name.strip(),
                category_id=category_id,
                description=(description or "").strip() or None,
                created_at=_now(),
            )
            self._uow.catalogue.save_product(product)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return product

    def update_product(
        self,
        *,
        product_id: UUID,
        name: str | None = None,
        category_id: UUID | None = None,
        description: str | None = None,
    ) -> Product:
        self._uow.begin()
        try:
            product = self._load_product(product_id)
            if name is not None:
                if not name.strip():
                    raise ProductNameRequired()
                product.name = name.strip()
            if category_id is not None:
                category = self._load_category(category_id)
                if category.merchant_id != product.merchant_id:
                    raise CategoryNotFound(str(category_id))
                product.category_id = category_id
            if description is not None:
                product.description = description.strip() or None
            product.version += 1
            self._uow.catalogue.save_product(product)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return product

    def archive_product(self, *, product_id: UUID) -> Product:
        self._uow.begin()
        try:
            product = self._load_product(product_id)
            product.archived_at = _now()
            product.version += 1
            self._uow.catalogue.save_product(product)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return product

    def list_products(
        self, *, merchant_id: UUID, category_id: UUID | None = None
    ) -> tuple[Product, ...]:
        self._uow.begin()
        try:
            found = self._uow.catalogue.list_products(
                merchant_id, category_id=category_id
            )
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    # ------------------------------------------------------------- internals

    def _require_active_merchant(self, merchant_id: UUID) -> None:
        merchant = self._uow.merchants.get(merchant_id)
        if merchant is None:
            raise MerchantNotFound(str(merchant_id))
        if not merchant.is_active:
            raise MerchantNotActive(str(merchant_id))

    def _load_category(self, category_id: UUID) -> ProductCategory:
        category = self._uow.catalogue.get_category(category_id)
        if category is None or not category.is_active:
            raise CategoryNotFound(str(category_id))
        return category

    def _load_product(self, product_id: UUID) -> Product:
        product = self._uow.catalogue.get_product(product_id)
        if product is None:
            raise ProductNotFound(str(product_id))
        return product
