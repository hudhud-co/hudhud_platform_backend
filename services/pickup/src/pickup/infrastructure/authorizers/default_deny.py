"""Fail-closed authorizer until a production identity adapter is wired."""

from __future__ import annotations

from uuid import UUID

from pickup.domain.value_objects import RecoveryAction
from pickup.ports.recovery_authorizer import RecoveryAccessDecision


class DefaultDenyRecoveryAuthorizer:
    """Rejects all recovery commands — production readiness remains blocked."""

    @property
    def is_production_ready(self) -> bool:
        return False

    async def authorize_recovery(
        self,
        *,
        bearer_token: str,
        pickup_task_id: UUID,
        action: RecoveryAction,
    ) -> RecoveryAccessDecision:
        _ = (bearer_token, pickup_task_id, action)
        return RecoveryAccessDecision.unauthenticated()
