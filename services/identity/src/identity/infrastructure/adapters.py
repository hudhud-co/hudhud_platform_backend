"""Outbound adapters for OTP delivery and service credentials.

Both default to a **fail-closed** implementation: with no channel configured the service
refuses to issue codes rather than issuing codes nobody receives, and with no service
credential configured it refuses every introspection rather than trusting every caller.
"""

from __future__ import annotations

import hmac
import sys

from identity.ports.service_credentials import ServiceCaller


class UnavailableOtpDelivery:
    """No delivery channel configured — issuing a code would be a silent failure."""

    @property
    def is_production_ready(self) -> bool:
        return False

    def send_code(self, *, phone_last4: str, code: str, reference: str) -> None:
        msg = "no OTP delivery channel is configured"
        raise RuntimeError(msg)


class RecordingOtpDelivery:
    """Test double. Captures the reference only — never the code."""

    def __init__(self, *, production_ready: bool = True) -> None:
        self._production_ready = production_ready
        self.sent: list[tuple[str, str]] = []
        self._codes: dict[str, str] = {}

    @property
    def is_production_ready(self) -> bool:
        return self._production_ready

    def send_code(self, *, phone_last4: str, code: str, reference: str) -> None:
        self.sent.append((phone_last4, reference))
        self._codes[reference] = code

    def code_for(self, reference: str) -> str:
        """Test-only accessor standing in for the SMS the user would receive."""
        return self._codes[reference]


class ConsoleOtpDelivery:
    """Local-development channel: write the code to stderr instead of sending an SMS.

    This is a *stand-in for an SMS gateway during development*, and it is unsafe anywhere
    a log is retained, so composition refuses to select it outside local and test
    environments. It is never production ready, which keeps readiness honest.
    """

    def __init__(self, *, stream=None) -> None:
        self._stream = stream or sys.stderr

    @property
    def is_production_ready(self) -> bool:
        return False

    def send_code(self, *, phone_last4: str, code: str, reference: str) -> None:
        print(
            f"[identity][dev-otp] reference={reference} phone=***{phone_last4} code={code}",
            file=self._stream,
            flush=True,
        )


class DenyAllServiceCredentials:
    """Default posture: introspection is closed until a credential is configured."""

    @property
    def is_production_ready(self) -> bool:
        return False

    def verify(self, credential: str) -> ServiceCaller | None:
        return None


class SharedSecretServiceCredentials:
    """Verify a caller by a per-service shared secret, compared in constant time."""

    def __init__(self, secrets_by_service: dict[str, str]) -> None:
        self._secrets = dict(secrets_by_service)

    @property
    def is_production_ready(self) -> bool:
        return bool(self._secrets)

    def verify(self, credential: str) -> ServiceCaller | None:
        if not credential:
            return None
        for service_name, secret in self._secrets.items():
            if hmac.compare_digest(credential, secret):
                return ServiceCaller(service_name=service_name)
        return None
