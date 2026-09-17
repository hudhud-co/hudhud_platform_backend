"""FastAPI composition root for the Finance service.

The fail-closed shape here is stricter than elsewhere, because the failures are
financial rather than operational:

* with no Identity configured the authorizer denies everything — otherwise anyone could
  approve their own payout;
* with no cash limit configured **production refuses to start**, because an unset limit
  is not a cautious default, it is no limit at all (DRV-A06);
* with no return-trip tariff configured a refusal charge is refused rather than guessed.

Two operations stay refused whatever is configured, and both are v6.3 Appendix A Open
Items: paying out a payout (PAY-07) and waiving a return-trip fee (PAY-08). The waiver
has a flag because its answer is a yes or a no somebody can give; the payout procedure
does not, because its answer is a document.
"""

from __future__ import annotations

from fastapi import FastAPI

from finance import __version__
from finance.api.health import router as health_router
from finance.api.routes import router as finance_router
from finance.application.readiness import evaluate_readiness
from finance.config import FinanceSettings, RuntimeEnvironment, load_settings
from finance.domain.money import Currency, Money
from finance.infrastructure.authorizers.identity import (
    DefaultDenyFinanceAuthorizer,
    IdentityFinanceAuthorizer,
)
from finance.infrastructure.memory import InMemoryUnitOfWork
from finance.infrastructure.persistence.session import (
    build_engine,
    build_session_factory,
)
from finance.infrastructure.persistence.sqlalchemy_store import (
    SqlAlchemyFinanceUnitOfWork,
)
from finance.ports.authorization import FinanceAuthorizer
from finance.ports.repository import FinanceUnitOfWork

#: Used only outside production, where startup refuses without a configured limit. A
#: local run needs *a* number to function at all; production needs the real one.
_DEVELOPMENT_CASH_LIMIT = Money(minor_units=1_000_000, currency=Currency.IQD)


def create_app(
    settings: FinanceSettings | None = None,
    *,
    unit_of_work: FinanceUnitOfWork | None = None,
    authorizer: FinanceAuthorizer | None = None,
) -> FastAPI:
    resolved = settings or load_settings(environment=RuntimeEnvironment.LOCAL)
    resolved.assert_production_gates()

    engine = None
    persistence_wired = False
    if unit_of_work is not None:
        # A test injected a store. Its *rows* are shared, as a database's are; each
        # request still gets its own transaction over them.
        new_unit_of_work = getattr(unit_of_work, "new_unit_of_work", None)
        unit_of_work_factory = new_unit_of_work or (lambda: unit_of_work)
        persistence_wired = True
    elif resolved.database_url:
        engine = build_engine(resolved.database_url)
        session_factory = build_session_factory(engine)

        def unit_of_work_factory() -> FinanceUnitOfWork:
            return SqlAlchemyFinanceUnitOfWork(session_factory=session_factory)

        persistence_wired = True
    else:
        database = InMemoryUnitOfWork().database

        def unit_of_work_factory() -> FinanceUnitOfWork:
            return InMemoryUnitOfWork(database)

    resolved_authorizer = authorizer or _build_authorizer(resolved)

    cash_limit = (
        Money(
            minor_units=resolved.default_cash_limit_minor_units, currency=Currency.IQD
        )
        if resolved.default_cash_limit_minor_units is not None
        else _DEVELOPMENT_CASH_LIMIT
    )
    return_trip_fee = (
        Money(
            minor_units=resolved.return_trip_fee_minor_units, currency=Currency.IQD
        )
        if resolved.return_trip_fee_minor_units is not None
        else None
    )

    app = FastAPI(
        title="HUDHUD Finance",
        version=__version__,
        description=(
            "Double-entry ledger, COD receivable, driver cash custody and limits, "
            "exchange-office settlement, merchant balance, payout requests and refunds"
        ),
    )
    app.include_router(health_router)
    app.include_router(finance_router)

    app.state.settings = resolved
    app.state.engine = engine
    # A *factory*, never a unit of work. A unit of work holds one request's session and
    # one request's pending writes; sharing the instance across requests meant two
    # concurrent callers fought over one session, and 39 of 40 failed with "transaction
    # already active". `tests/architecture/test_request_scoped_state.py` now refuses to
    # let any service put a mutable unit of work back into application state.
    app.state.unit_of_work_factory = unit_of_work_factory
    app.state.authorizer = resolved_authorizer
    # The services are built per request too, from that request's unit of work, because
    # each one holds a reference to it. What lives here is only what it takes to build
    # them, all of it immutable.
    app.state.cash_limit = cash_limit
    app.state.return_trip_fee = return_trip_fee
    app.state.readiness_report = evaluate_readiness(
        persistence_wired=persistence_wired,
        authorization_configured=resolved_authorizer.is_production_ready,
        extra_checks={
            "cash_limit_configured": resolved.cash_limit_configured,
            # Reported rather than enforced: without it a refusal charge is refused,
            # which is correct behaviour for an unconfigured tariff.
            "return_trip_fee_configured": resolved.return_trip_fee_configured,
        },
    )
    return app


def _build_authorizer(settings: FinanceSettings) -> FinanceAuthorizer:
    """Authorize through Identity when configured; otherwise stay fail-closed."""
    if not settings.identity_authorization_enabled:
        return DefaultDenyFinanceAuthorizer()
    assert settings.identity_base_url is not None
    assert settings.identity_service_credential is not None
    return IdentityFinanceAuthorizer(
        base_url=settings.identity_base_url,
        service_credential=settings.identity_service_credential,
    )


app = create_app()
