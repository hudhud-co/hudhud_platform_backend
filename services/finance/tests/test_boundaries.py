"""Import and database boundary guards for the Finance service.

The one that matters most: **Delivery must not be able to write the ledger.** ADR-0012
puts the posting here and only here, and Delivery states what happened at the door. That
is a boundary rather than a convention because the alternative — two services both
allowed to credit a merchant — is how a payable ends up with two different values.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import yaml

from finance.infrastructure.authorizers.identity import _ROLE_MAP
from finance.infrastructure.persistence.models import Base

SERVICE_ROOT = Path(__file__).resolve().parents[1] / "src"
REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICES = SERVICE_ROOT.parents[1]

OTHER_SERVICES = {
    "audit",
    "claims",
    "customer",
    "delivery",
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
    "finance",
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
    assert users == ["finance/infrastructure/authorizers/identity.py"]


def test_the_domain_layer_has_no_infrastructure_dependency() -> None:
    infrastructure = {"sqlalchemy", "fastapi", "httpx", "nats", "starlette", "pydantic"}
    offenders = {
        str(path.relative_to(SERVICE_ROOT)): sorted(_imports(path) & infrastructure)
        for path in (SERVICE_ROOT / "finance" / "domain").rglob("*.py")
        if _imports(path) & infrastructure
    }
    assert offenders == {}


def test_the_application_layer_does_not_depend_on_the_web_framework() -> None:
    web = {"fastapi", "starlette"}
    offenders = {
        str(path.relative_to(SERVICE_ROOT)): sorted(_imports(path) & web)
        for path in (SERVICE_ROOT / "finance" / "application").rglob("*.py")
        if _imports(path) & web
    }
    assert offenders == {}


# ------------------------------------------------- Delivery never writes here


def test_no_other_service_imports_this_one() -> None:
    """ADR-0012 — Finance owns the posting, and nobody reaches into it."""
    offenders = []
    for service in SERVICES.iterdir():
        if not service.is_dir() or service.name == "finance":
            continue
        src = service / "src"
        if not src.is_dir():
            continue
        for path in src.rglob("*.py"):
            if "site-packages" in str(path):
                continue
            roots = _imports(path)
            if "finance" in roots:
                offenders.append(f"{service.name}/{path.name}")
    assert offenders == []


def test_no_other_service_owns_a_table_named_like_a_ledger() -> None:
    """A second place that posts money is a second answer to what a merchant is owed."""
    offenders = []
    for service in SERVICES.iterdir():
        if not service.is_dir() or service.name == "finance":
            continue
        models = service / "src" / service.name / "infrastructure" / "persistence"
        model_file = models / "models.py"
        if not model_file.is_file():
            continue
        text = model_file.read_text(encoding="utf-8")
        for word in ("journal_entr", "journal_posting", "_ledger", "wallet"):
            if word in text:
                offenders.append(f"{service.name}: {word}")
    assert offenders == []


def test_this_services_tables_are_all_its_own() -> None:
    mine = {table.name for table in Base.metadata.sorted_tables}
    assert all(name.startswith("finance_") for name in mine), sorted(mine)


def test_migrations_only_create_finance_owned_tables() -> None:
    owned = {
        "finance_journal_entries",
        "finance_journal_postings",
        "finance_driver_cash_accounts",
        "finance_merchant_accounts",
        "finance_cod_collections",
        "finance_deposits",
        "finance_payout_requests",
        "finance_route_reconciliations",
        "finance_integration_outbox",
        "finance_integration_inbox",
    }
    migrations = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    source = "\n".join(p.read_text(encoding="utf-8") for p in migrations.glob("*.py"))
    created = set(re.findall(r'op\.create_table\(\s*"([^"]+)"', source))
    dropped = set(re.findall(r'op\.drop_table\(\s*"([^"]+)"', source))

    assert created, "no tables found — the parser is looking in the wrong place"
    assert sorted(created | dropped) == sorted(owned)


def test_the_owned_table_set_matches_the_orm_metadata() -> None:
    migrations = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    source = "\n".join(p.read_text(encoding="utf-8") for p in migrations.glob("*.py"))
    created = set(re.findall(r'op\.create_table\(\s*"([^"]+)"', source))
    assert created == {table.name for table in Base.metadata.sorted_tables}


def test_this_service_owns_exactly_one_outbox_and_one_inbox() -> None:
    names = {table.name for table in Base.metadata.sorted_tables}
    assert {n for n in names if "outbox" in n} == {"finance_integration_outbox"}
    assert {n for n in names if "inbox" in n} == {"finance_integration_inbox"}


# ------------------------------------------------------------ handling money


def test_nothing_in_this_service_logs() -> None:
    """Amounts, balances and receipt references pass through every service here.

    Rather than review each log call for what it interpolates, Finance emits none.
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


def test_no_module_imports_decimal_or_uses_float() -> None:
    """Exact IQD is integer minor units. Neither alternative belongs in this service."""
    offenders = []
    for path in SERVICE_ROOT.rglob("*.py"):
        roots = _imports(path)
        if "decimal" in roots:
            offenders.append(f"{path.name}: decimal")
        if "float(" in path.read_text(encoding="utf-8"):
            offenders.append(f"{path.name}: float(")
    assert offenders == []


# ------------------------------------------- the Identity role vocabulary


def _identity_roles() -> dict:
    """Identity's published role contract.

    Read as data, never imported: `finance` may not import Identity's package, and the
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
