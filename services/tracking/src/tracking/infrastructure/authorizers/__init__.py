"""Authorization adapter implementations."""

from tracking.infrastructure.authorizers.default_deny import DefaultDenyQueryAuthorizer

__all__ = ["DefaultDenyQueryAuthorizer"]
