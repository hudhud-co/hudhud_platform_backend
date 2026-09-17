"""Import boundary guards for the Workforce service.

Workforce talks to Identity over HTTP and to the broker over NATS. It must never reach
another service's Python package or its database — that is what makes the boundary real
rather than a convention.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import yaml

from workforce.infrastructure.authorizers.identity import _ROLE_MAP
from workforce.infrastructure.persistence.models import Base

SERVICE_ROOT = Path(__file__).resolve().parents[1] / "src"
REPO_ROOT = Path(__file__).resolve().parents[3]

OTHER_SERVICES = {
    "audit",
    "claims",
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
    "gateway",
    "services",
}

ALLOWED_THIRD_PARTY = {
    "workforce",
    "event_envelope",
    "fastapi",
    # Production authorization calls Identity's introspection endpoint over HTTP.
    # This is an infrastructure-layer transport, exactly like `nats`.
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
    """One file may speak to another service, and it is an HTTP adapter."""
    users = sorted(
        str(path.relative_to(SERVICE_ROOT))
        for path in SERVICE_ROOT.rglob("*.py")
        if "httpx" in _imports(path)
    )
    assert users == ["workforce/infrastructure/authorizers/identity.py"]


def test_the_domain_layer_has_no_infrastructure_dependency() -> None:
    """Domain code that imports a driver or a framework stops being testable in isolation."""
    infrastructure = {"sqlalchemy", "fastapi", "httpx", "nats", "starlette", "pydantic"}
    offenders = {
        str(path.relative_to(SERVICE_ROOT)): sorted(_imports(path) & infrastructure)
        for path in (SERVICE_ROOT / "workforce" / "domain").rglob("*.py")
        if _imports(path) & infrastructure
    }
    assert offenders == {}


def test_the_application_layer_does_not_depend_on_the_web_framework() -> None:
    web = {"fastapi", "starlette"}
    offenders = {
        str(path.relative_to(SERVICE_ROOT)): sorted(_imports(path) & web)
        for path in (SERVICE_ROOT / "workforce" / "application").rglob("*.py")
        if _imports(path) & web
    }
    assert offenders == {}


def test_migrations_only_create_workforce_owned_tables() -> None:
    """A migration that touches another service's table is a database-boundary breach.

    Workforce's tables are not all prefixed, so the owned set is named explicitly: an
    unprefixed name is exactly the kind that could collide with another context's.
    """
    owned = {
        "driver_applications",
        "drivers",
        "driver_shift_patterns",
        "driver_attendance",
        "driver_lateness_blocks",
        "driver_leave_requests",
        "workforce_integration_inbox",
    }
    migrations = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    source = "\n".join(p.read_text(encoding="utf-8") for p in migrations.glob("*.py"))
    created = set(re.findall(r'op\.create_table\(\s*"([^"]+)"', source))
    dropped = set(re.findall(r'op\.drop_table\(\s*"([^"]+)"', source))

    assert created, "no tables found — the parser is looking in the wrong place"
    assert sorted(created | dropped) == sorted(owned)


def test_this_service_publishes_nothing() -> None:
    """Workforce answers questions over HTTP and owns no outbox.

    Asserted rather than assumed: an outbox appearing here would mean this service had
    quietly become a publisher, which changes who depends on whom.
    """
    assert not [
        table.name for table in Base.metadata.sorted_tables if "outbox" in table.name
    ]
    assert not list(SERVICE_ROOT.rglob("*/nats/subjects.py"))


def test_the_owned_table_set_matches_the_orm_metadata() -> None:
    """Keeps the explicit list above honest when a table is added to the models."""
    migrations = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    source = "\n".join(p.read_text(encoding="utf-8") for p in migrations.glob("*.py"))
    created = set(re.findall(r'op\.create_table\(\s*"([^"]+)"', source))

    assert created == {table.name for table in Base.metadata.sorted_tables}


# ------------------------------------------- the Identity role vocabulary


def _identity_roles() -> dict:
    """Identity's published role contract.

    Read as data, never imported: `workforce` may not import Identity's package, and the
    point of the check is precisely that the two are separately maintained.
    """
    contract = REPO_ROOT / "contracts" / "identity" / "roles.yaml"
    return {
        role["name"]: role
        for role in yaml.safe_load(contract.read_text(encoding="utf-8"))["roles"]
    }


def test_every_mapped_role_is_one_identity_actually_grants() -> None:
    """A name Identity does not grant is silently dropped, and the caller gets nothing.

    This is a regression test for a real defect: this map keyed
    `LAST_MILE_DRIVER` and `MERCHANT_OWNER`, which Identity has never had. Every unit
    test passed because the fake authorizer handed out this service's own names; the
    first live sign-in produced a driver with no driver role and a 403 on every route.
    """
    unknown = sorted(set(_ROLE_MAP) - set(_identity_roles()))
    assert unknown == [], (
        f"{unknown} are not in contracts/identity/roles.yaml — "
        "Identity cannot grant them, so they would never arrive"
    )


def test_the_mapped_names_are_identitys_and_not_this_services() -> None:
    """The keys are Identity's vocabulary; the values are this service's."""
    published = _identity_roles()
    for identity_name in _ROLE_MAP:
        assert identity_name in published, identity_name
