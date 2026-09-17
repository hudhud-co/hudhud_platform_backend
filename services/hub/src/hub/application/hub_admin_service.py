"""Hub registry: facilities and their per-hub cut-off times (SHP-07, OPS-10)."""

from __future__ import annotations

from datetime import time
from uuid import UUID, uuid4

from hub.domain.entities import Hub
from hub.domain.errors import HubNotFound, UnknownGovernorate
from hub.domain.value_objects import (
    CutOffTime,
    normalize_governorate,
    normalize_hub_code,
)
from hub.ports.repository import HubUnitOfWork


class HubAdminService:
    def __init__(self, unit_of_work: HubUnitOfWork) -> None:
        self._uow = unit_of_work

    def register_hub(
        self,
        *,
        code: str,
        name: str,
        governorate: str,
        cut_off_local_time: time,
        vehicle_cameras_fitted: bool = False,
    ) -> Hub:
        """Register a facility with its own cut-off — never a company-wide one (p.23)."""
        self._uow.begin()
        try:
            facility = Hub(
                hub_id=uuid4(),
                code=normalize_hub_code(code),
                name=name.strip(),
                governorate=self._normalize(governorate),
                cut_off=CutOffTime(local_time=cut_off_local_time),
                vehicle_cameras_fitted=vehicle_cameras_fitted,
            )
            self._uow.hubs.save(facility)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return facility

    def set_cut_off(self, *, hub_id: UUID, cut_off_local_time: time) -> Hub:
        self._uow.begin()
        try:
            facility = self._load(hub_id)
            facility.cut_off = CutOffTime(local_time=cut_off_local_time)
            facility.version += 1
            self._uow.hubs.save(facility)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return facility

    def set_active(self, *, hub_id: UUID, is_active: bool) -> Hub:
        self._uow.begin()
        try:
            facility = self._load(hub_id)
            facility.is_active = is_active
            facility.version += 1
            self._uow.hubs.save(facility)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return facility

    def record_vehicle_cameras(self, *, hub_id: UUID, fitted: bool) -> Hub:
        """OPS-10 — v6.3 p.24 permits cameras inside inter-city vehicles.

        Recorded as fact rather than assumed: "can add" is a capability, not a guarantee
        that a given hub's fleet is fitted.
        """
        self._uow.begin()
        try:
            facility = self._load(hub_id)
            facility.vehicle_cameras_fitted = fitted
            facility.version += 1
            self._uow.hubs.save(facility)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return facility

    def get(self, hub_id: UUID) -> Hub:
        self._uow.begin()
        try:
            facility = self._load(hub_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return facility

    def list_hubs(self) -> tuple[Hub, ...]:
        self._uow.begin()
        try:
            found = self._uow.hubs.list_all()
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    def _load(self, hub_id: UUID) -> Hub:
        facility = self._uow.hubs.get(hub_id)
        if facility is None:
            raise HubNotFound(str(hub_id))
        return facility

    @staticmethod
    def _normalize(governorate: str) -> str:
        try:
            return normalize_governorate(governorate)
        except ValueError as exc:
            raise UnknownGovernorate(governorate) from exc
