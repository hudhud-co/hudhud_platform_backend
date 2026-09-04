"""Authorization adapter package."""

from shipment.infrastructure.authorizers.default_deny import DefaultDenyAcceptanceAuthorizer
from shipment.infrastructure.authorizers.fake import FakeAcceptanceAuthorizer

__all__ = ["DefaultDenyAcceptanceAuthorizer", "FakeAcceptanceAuthorizer"]
