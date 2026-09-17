"""Outbound OTP delivery boundary.

Identity produces the code; it does not own the SMS channel. In production this is an
adapter onto the notification service. The port exists so that a missing channel fails
closed rather than silently issuing codes nobody receives.
"""

from __future__ import annotations

from typing import Protocol


class OtpDeliveryPort(Protocol):
    @property
    def is_production_ready(self) -> bool:
        """True when a real delivery channel is configured."""

    def send_code(self, *, phone_last4: str, code: str, reference: str) -> None:
        """Deliver one code. Implementations must never log or persist ``code``."""
