"""The goods taxonomy and the prohibited-goods list (MER-22, MER-23).

The taxonomy is data an operator maintains, seeded from the categories the Customer App
actually offers. Prohibited kinds are marked on the same rows rather than kept in a second
list, so a category can never be offered for selection and refused at registration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from ordering.domain.entities import GoodsCategory
from ordering.domain.errors import UnknownGoodsCategory
from ordering.ports.repository import OrderingUnitOfWork


@dataclass(frozen=True, slots=True)
class ProhibitedGoodsNotice:
    """What the app shows on `prohibited` before a sender may continue.

    The wording is the product's, quoted rather than paraphrased: it states a legal
    position and a consequence, and softening either would misinform the sender.
    """

    summary: str = (
        "Weapons, drugs, flammables, cash and undeclared valuables are not accepted — a "
        "criminal offence, and the parcel is handed to the authorities."
    )
    detail: str = (
        "You are responsible for what goes in the parcel. HUDHUD may open and inspect any "
        "shipment. Prohibited contents are held, the shipment is cancelled without "
        "refund, and the case is reported to the authorities — sending them is a criminal "
        "offence under Iraqi law."
    )


class GoodsCatalogueService:
    def __init__(self, unit_of_work: OrderingUnitOfWork) -> None:
        self._uow = unit_of_work

    def list_categories(self) -> tuple[GoodsCategory, ...]:
        self._uow.begin()
        try:
            found = self._uow.goods.list_active()
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return tuple(sorted(found, key=lambda item: (item.sort_order, item.display_name)))

    def get(self, code: str) -> GoodsCategory:
        self._uow.begin()
        try:
            category = self._uow.goods.get(code)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        if category is None or not category.is_active:
            raise UnknownGoodsCategory(code)
        return category

    def upsert(
        self,
        *,
        code: str,
        display_name: str,
        hint: str | None = None,
        restriction_note: str | None = None,
        prohibited: bool = False,
        sort_order: int = 0,
    ) -> GoodsCategory:
        self._uow.begin()
        try:
            category = GoodsCategory(
                code=code.strip().upper(),
                display_name=display_name.strip(),
                hint=(hint or "").strip() or None,
                restriction_note=(restriction_note or "").strip() or None,
                prohibited=prohibited,
                sort_order=sort_order,
            )
            self._uow.goods.save(category)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return category

    def archive(self, *, code: str) -> GoodsCategory:
        self._uow.begin()
        try:
            category = self._uow.goods.get(code)
            if category is None or not category.is_active:
                raise UnknownGoodsCategory(code)
            category.archived_at = datetime.now(tz=UTC)
            self._uow.goods.save(category)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return category

    @staticmethod
    def prohibited_notice() -> ProhibitedGoodsNotice:
        return ProhibitedGoodsNotice()
