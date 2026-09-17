"""Asking Workforce whether a driver may be given work (ADR-0013).

Delivery owns parcels at doors; it does not own shifts, leave or lateness. So before a
manifest puts parcels into a driver's custody it asks the Workforce service over HTTP —
never by import, and never by reading Workforce's database.

The refusal is fail-closed on purpose. If Workforce cannot be reached, Delivery does not
know whether this driver is on shift, and handing real parcels to a driver who might be
on leave is worse than a manifest that has to be retried.
"""

from __future__ import annotations

from uuid import UUID

import httpx

from delivery.ports.authorization import AuthorizerUnavailableError, DriverEligibility


class DenyingWorkforceEligibility:
    """The composition default: no Workforce configured, so no driver is eligible."""

    @property
    def is_production_ready(self) -> bool:
        return False

    async def eligibility_for(self, principal_id: UUID) -> DriverEligibility:
        _ = principal_id
        return DriverEligibility(
            eligible=False, reasons=("workforce eligibility source is not configured",)
        )


class FakeWorkforceEligibility:
    """Test-only eligibility source."""

    def __init__(
        self,
        *,
        eligible_principals: frozenset[UUID] | None = None,
        reasons: tuple[str, ...] = ("not eligible",),
        production_ready: bool = True,
        unavailable: bool = False,
    ) -> None:
        self._eligible = eligible_principals or frozenset()
        self._reasons = reasons
        self._production_ready = production_ready
        self._unavailable = unavailable

    @property
    def is_production_ready(self) -> bool:
        return self._production_ready

    async def eligibility_for(self, principal_id: UUID) -> DriverEligibility:
        if self._unavailable:
            raise AuthorizerUnavailableError("workforce unavailable")
        if principal_id in self._eligible:
            return DriverEligibility(eligible=True)
        return DriverEligibility(eligible=False, reasons=self._reasons)


class HttpWorkforceEligibility:
    """The real adapter for ``GET /workforce/principals/{id}/eligibility``.

    That route authenticates with a bearer token like every other Workforce route, so
    the configured credential is Delivery's own service token — held by a principal that
    Identity grants an operations role to. ``X-Service-Credential`` is Identity's
    introspection header and does not apply here.
    """

    def __init__(
        self,
        *,
        base_url: str,
        service_credential: str,
        timeout_seconds: float = 3.0,
        client_factory=None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_credential = service_credential
        self._timeout = timeout_seconds
        self._client_factory = client_factory

    @property
    def is_production_ready(self) -> bool:
        return True

    async def eligibility_for(self, principal_id: UUID) -> DriverEligibility:
        client_factory = self._client_factory
        if client_factory is None:

            def client_factory() -> httpx.AsyncClient:
                return httpx.AsyncClient(timeout=self._timeout)

        try:
            async with client_factory() as client:
                response = await client.get(
                    f"{self._base_url}/workforce/principals/{principal_id}/eligibility",
                    headers={"Authorization": f"Bearer {self._service_credential}"},
                )
        except Exception as exc:  # noqa: BLE001 - any transport fault is unavailability
            raise AuthorizerUnavailableError("workforce eligibility failed") from exc
        if response.status_code in {401, 403}:
            # Delivery's own token was rejected. That says nothing about the driver, so
            # it must not be reported as the driver being ineligible.
            raise AuthorizerUnavailableError(
                f"workforce rejected delivery's service token ({response.status_code})"
            )
        if response.status_code == 404:
            # Workforce has never heard of this principal, which is an answer: no shift
            # record means no eligibility.
            return DriverEligibility(
                eligible=False, reasons=("no workforce record for this driver",)
            )
        if response.status_code != 200:
            raise AuthorizerUnavailableError(
                f"workforce eligibility returned {response.status_code}"
            )
        body = response.json()
        if not isinstance(body, dict):
            raise AuthorizerUnavailableError("eligibility body was not an object")
        reasons = tuple(
            reason
            for reason in body.get("reasons", [])  # type: ignore[union-attr]
            if isinstance(reason, str)
        )
        return DriverEligibility(eligible=body.get("eligible") is True, reasons=reasons)
