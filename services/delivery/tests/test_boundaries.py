"""Import and database boundary guards for the Delivery service.

Delivery talks to Identity and to Workforce over HTTP, and to the broker over NATS. It
must never reach another service's Python package or its tables — that is what makes the
boundary real rather than a convention.

Two extra guards live here because Delivery is where the doorstep evidence is handled:
nothing outside the domain may reconstruct a delivery code, and no PII may be written to
a log.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import yaml

from delivery.infrastructure.authorizers.identity import _ROLE_MAP
from delivery.infrastructure.persistence.models import Base

SERVICE_ROOT = Path(__file__).resolve().parents[1] / "src"
REPO_ROOT = Path(__file__).resolve().parents[3]

OTHER_SERVICES = {
    "audit",
    "claims",
    "customer",
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
    "delivery",
    "event_envelope",
    "fastapi",
    # Identity introspection and the Workforce eligibility call are HTTP transports,
    # exactly like `nats`.
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
    """Workforce is in the forbidden set: ADR-0013 is an HTTP dependency, not an import."""
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


def test_other_services_are_reached_only_over_http_from_their_adapters() -> None:
    users = sorted(
        str(path.relative_to(SERVICE_ROOT))
        for path in SERVICE_ROOT.rglob("*.py")
        if "httpx" in _imports(path)
    )
    assert users == [
        "delivery/infrastructure/authorizers/identity.py",
        "delivery/infrastructure/authorizers/workforce.py",
    ]


def test_the_domain_layer_has_no_infrastructure_dependency() -> None:
    infrastructure = {"sqlalchemy", "fastapi", "httpx", "nats", "starlette", "pydantic"}
    offenders = {
        str(path.relative_to(SERVICE_ROOT)): sorted(_imports(path) & infrastructure)
        for path in (SERVICE_ROOT / "delivery" / "domain").rglob("*.py")
        if _imports(path) & infrastructure
    }
    assert offenders == {}


def test_the_application_layer_does_not_depend_on_the_web_framework() -> None:
    web = {"fastapi", "starlette"}
    offenders = {
        str(path.relative_to(SERVICE_ROOT)): sorted(_imports(path) & web)
        for path in (SERVICE_ROOT / "delivery" / "application").rglob("*.py")
        if _imports(path) & web
    }
    assert offenders == {}


def test_migrations_only_create_delivery_owned_tables() -> None:
    owned = {
        "delivery_manifests",
        "delivery_stops",
        "delivery_verification_attempts",
        "delivery_payments",
        "delivery_photo_evidence",
        "delivery_failed_attempts",
        "delivery_receiver_preferences",
        "delivery_issue_reports",
        "delivery_courier_ratings",
        "delivery_integration_outbox",
        "delivery_integration_inbox",
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


def test_this_service_owns_exactly_one_outbox_and_one_inbox() -> None:
    """Delivery both publishes and consumes, so it owns one of each (ADR-0008)."""
    names = {table.name for table in Base.metadata.sorted_tables}
    assert {n for n in names if "outbox" in n} == {"delivery_integration_outbox"}
    assert {n for n in names if "inbox" in n} == {"delivery_integration_inbox"}


def test_only_the_domain_can_derive_a_delivery_code_digest() -> None:
    """A second hashing site is a second chance to hash without the key or the binding."""
    users = sorted(
        str(path.relative_to(SERVICE_ROOT))
        for path in SERVICE_ROOT.rglob("*.py")
        if "hashlib" in _imports(path) or "hmac" in _imports(path)
    )
    assert users == ["delivery/domain/delivery_code.py"]


def test_nothing_in_this_service_logs() -> None:
    """The doorstep handles codes, names and amounts.

    Rather than review every log call for what it interpolates, Delivery emits none: a
    log line is the easiest place for a receiver's name or a delivery code to escape.
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
    """Identity's published role contract.

    Read as data, never imported: `delivery` may not import Identity's package, and the
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
