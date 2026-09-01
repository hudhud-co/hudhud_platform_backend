"""Shipment access policy adapters."""

from tracking.infrastructure.policies.default_deny import DefaultDenyShipmentAccessPolicy

__all__ = ["DefaultDenyShipmentAccessPolicy"]
