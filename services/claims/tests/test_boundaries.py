"""Import and database boundary guards for the Claims service.

Claims talks to Identity over HTTP. It must never reach another service's Python package
or its tables — that is what makes the boundary real rather than a convention.

Two extra guards live here because Claims is where compensation figures are held:
SEC-07's "no compensation or claim value is shown to the driver" is checked against the
whole driver-facing surface, and nothing in this service writes a log line.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import yaml

from claims.infrastructure.authorizers.identity import _ROLE_MAP
from claims.infrastructure.persistence.models import Base

SERVICE_ROOT = Path(__file__).resolve().parents[1] / "src"
REPO_ROOT = Path(__file__).resolve().parents[3]

OTHER_SERVICES = {
    "audit",
    "customer",
    "delivery",
    "finance",
    "identity",
    "legacy_event_bridge",
    "hub",
    "merchant",
    "notification",
    "ordering",
    "pickup",
    "shipment",
    "tracking",
    "workforce",
    "gateway",
    "services",
}

ALLOWED_THIRD_PARTY = {
    "claims",
    "event_envelope",
    "fastapi",
    # Identity introspection is an HTTP transport, exactly like `nats`.
    "httpx",
    "jsonschema",
    "messaging_conformance",
    "nats",
    "pydantic",
    "referencing",
    "sqlalchemy",
    "starlette",
    "yaml",
}


def _imports(path: Path) -> set[str]:
    roots: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_no_cross_service_imports() -> None:
    offenders = {
        str(path.relative_to(SERVICE_ROOT)): sorted(_imports(path) & OTHER_SERVICES)
        for path in SERVICE_ROOT.rglob("*.py")
        if _imports(path) & OTHER_SERVICES
    }
    assert offenders == {}


def test_only_declared_dependencies_are_imported() -> None:
    allowed = ALLOWED_THIRD_PARTY | set(sys.stdlib_module_names)
    offenders = {
        str(path.relative_to(SERVICE_ROOT)): sorted(_imports(path) - allowed)
        for path in SERVICE_ROOT.rglob("*.py")
        if _imports(path) - allowed
    }
    assert offenders == {}


def test_identity_is_reached_only_over_http_from_its_adapter() -> None:
    users = sorted(
        str(path.relative_to(SERVICE_ROOT))
        for path in SERVICE_ROOT.rglob("*.py")
        if "httpx" in _imports(path)
    )
    assert users == ["claims/infrastructure/authorizers/identity.py"]


def test_the_domain_layer_has_no_infrastructure_dependency() -> None:
    infrastructure = {"sqlalchemy", "fastapi", "httpx", "nats", "starlette", "pydantic"}
    offenders = {
        str(path.relative_to(SERVICE_ROOT)): sorted(_imports(path) & infrastructure)
        for path in (SERVICE_ROOT / "claims" / "domain").rglob("*.py")
        if _imports(path) & infrastructure
    }
    assert offenders == {}


def test_the_application_layer_does_not_depend_on_the_web_framework() -> None:
    web = {"fastapi", "starlette"}
    offenders = {
        str(path.relative_to(SERVICE_ROOT)): sorted(_imports(path) & web)
        for path in (SERVICE_ROOT / "claims" / "application").rglob("*.py")
        if _imports(path) & web
    }
    assert offenders == {}


def test_migrations_only_create_claims_owned_tables() -> None:
    owned = {
        "claims_compensation_claims",
        "claims_messages",
        "claims_driver_incidents",
        "claims_reference_sequences",
        "claims_integration_outbox",
        "claims_integration_inbox",
    }
    migrations = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    source = "\n".join(p.read_text(encoding="utf-8") for p in migrations.glob("*.py"))
    created = set(re.findall(r'op\.create_table\(\s*"([^"]+)"', source))
    dropped = set(re.findall(r'op\.drop_table\(\s*"([^"]+)"', source))

    assert created, "no tables found — the parser is looking in the wrong place"
    assert sorted(created | dropped) == sorted(owned)


def test_the_owned_table_set_matches_the_orm_metadata() -> None:
    """Keeps the explicit list above honest when a table is added to the models."""
    migrations = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    source = "\n".join(p.read_text(encoding="utf-8") for p in migrations.glob("*.py"))
    created = set(re.findall(r'op\.create_table\(\s*"([^"]+)"', source))

    assert created == {table.name for table in Base.metadata.sorted_tables}


def test_every_owned_table_is_prefixed_with_this_context() -> None:
    """Database-per-service (ADR-0011): a bare `claims` table name is a shared one."""
    offenders = [
        table.name
        for table in Base.metadata.sorted_tables
        if not table.name.startswith("claims_")
    ]
    assert offenders == []


def test_this_service_owns_exactly_one_outbox_and_one_inbox() -> None:
    """Claims both publishes and consumes, so it owns one of each (ADR-0008)."""
    names = {table.name for table in Base.metadata.sorted_tables}
    assert {n for n in names if "outbox" in n} == {"claims_integration_outbox"}
    assert {n for n in names if "inbox" in n} == {"claims_integration_inbox"}


def test_nothing_in_this_service_logs() -> None:
    """A claim carries someone's account of what went wrong and what it cost.

    Rather than review every log call for what it interpolates, Claims emits none: a log
    line is the easiest place for a claimant's words or a compensation figure to escape.
    """
    offenders = sorted(
        str(path.relative_to(SERVICE_ROOT))
        for path in SERVICE_ROOT.rglob("*.py")
        if "logging" in _imports(path)
    )
    assert offenders == []


def test_no_module_prints() -> None:
    offenders = [
        str(path.relative_to(SERVICE_ROOT))
        for path in SERVICE_ROOT.rglob("*.py")
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        )
    ]
    assert offenders == []


# ------------------------------------------- the Identity role vocabulary


def _identity_roles() -> dict:
    """Identity's published role contract, read as data and never imported."""
    contract = REPO_ROOT / "contracts" / "identity" / "roles.yaml"
    return {
        role["name"]: role
        for role in yaml.safe_load(contract.read_text(encoding="utf-8"))["roles"]
    }


def test_every_mapped_role_is_one_identity_actually_grants() -> None:
    """A name Identity does not grant is silently dropped, and the caller gets nothing.

    This is a regression test for a real defect: three services keyed
    `LAST_MILE_DRIVER` and `MERCHANT_OWNER`, which Identity has never had. Every unit
    test passed because each fake authorizer handed out that service's own names; the
    first live sign-in produced a driver with no driver role and a 403 on every route.
    """
    unknown = sorted(set(_ROLE_MAP) - set(_identity_roles()))
    assert unknown == [], (
        f"{unknown} are not in contracts/identity/roles.yaml — "
        "Identity cannot grant them, so they would never arrive"
    )


def test_the_privileged_roles_this_service_acts_on_are_all_mapped() -> None:
    """Support, operations and an accountant all have work here; none may be missing."""
    for name in ("SUPPORT", "OPERATIONS", "ACCOUNTANT", "DELIVERY_DRIVER"):
        assert name in _ROLE_MAP, name


# ------------------------------------------------------------ SEC-07


def test_no_driver_facing_name_in_this_service_mentions_an_amount() -> None:
    """Belt and braces over the schema guard: the *types* carry no amount either."""
    source = (SERVICE_ROOT / "claims" / "domain" / "entities.py").read_text(
        encoding="utf-8"
    )
    driver_model = source.split("class ClaimSummaryForDriver", 1)[1].split(
        "@dataclass", 1
    )[0]
    assert "compensation" not in driver_model.split('"""')[-1]
    assert "amount" not in driver_model.split('"""')[-1]
