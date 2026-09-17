"""Import boundary guards for the Notification service.

Notification talks to Identity over HTTP and to the broker over NATS. It must never reach
another service's Python package or its database — that is what makes the boundary real
rather than a convention.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

from notification.infrastructure.persistence.models import Base

SERVICE_ROOT = Path(__file__).resolve().parents[1] / "src"

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
    "ordering",
    "pickup",
    "shipment",
    "tracking",
    "workforce",
    "gateway",
    "services",
}

ALLOWED_THIRD_PARTY = {
    "notification",
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
    assert users == ["notification/infrastructure/authorizers/identity.py"]


def test_the_domain_layer_has_no_infrastructure_dependency() -> None:
    """Domain code that imports a driver or a framework stops being testable in isolation."""
    infrastructure = {"sqlalchemy", "fastapi", "httpx", "nats", "starlette", "pydantic"}
    offenders = {
        str(path.relative_to(SERVICE_ROOT)): sorted(_imports(path) & infrastructure)
        for path in (SERVICE_ROOT / "notification" / "domain").rglob("*.py")
        if _imports(path) & infrastructure
    }
    assert offenders == {}


def test_the_application_layer_does_not_depend_on_the_web_framework() -> None:
    web = {"fastapi", "starlette"}
    offenders = {
        str(path.relative_to(SERVICE_ROOT)): sorted(_imports(path) & web)
        for path in (SERVICE_ROOT / "notification" / "application").rglob("*.py")
        if _imports(path) & web
    }
    assert offenders == {}


def test_migrations_only_create_notification_owned_tables() -> None:
    """A migration that touches another service's table is a database-boundary breach.

    Notification's tables are not all prefixed, so the owned set is named explicitly: an
    unprefixed name is exactly the kind that could collide with another context's.
    """
    owned = {
        "notification_recipients",
        "notification_preferences",
        "notifications",
        "notification_centre_entries",
        "notification_integration_inbox",
    }
    migrations = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    source = "\n".join(p.read_text(encoding="utf-8") for p in migrations.glob("*.py"))
    created = set(re.findall(r'op\.create_table\(\s*"([^"]+)"', source))
    dropped = set(re.findall(r'op\.drop_table\(\s*"([^"]+)"', source))

    assert created, "no tables found — the parser is looking in the wrong place"
    assert sorted(created | dropped) == sorted(owned)


def test_this_service_publishes_nothing() -> None:
    """Notification consumes journey facts and owns no outbox.

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
