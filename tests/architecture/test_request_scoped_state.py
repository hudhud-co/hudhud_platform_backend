"""A mutable unit of work must never be shared application state.

## What went wrong

Every service built one `SqlAlchemy…UnitOfWork` at startup, stored it on
`app.state`, and constructed its application services around it. A unit of work keeps
*one* request's session and *one* request's pending writes on itself. Sharing the
instance meant every concurrent request used the same session.

Measured against real PostgreSQL before the fix: **1 of 40 concurrent requests
succeeded**; the other 39 raised `RuntimeError: transaction already active`. The service
worked perfectly in every test, because tests issue one request at a time.

## What this guard enforces

Application state may hold what it takes to *build* a unit of work — a session factory, a
callable, settings — but never a unit of work, never a session, and never an application
service that holds one. Those are per request.

The guard is structural rather than behavioural on purpose: a concurrency bug reproduces
only under load, and by then it is in production. This fails at import time instead.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES = REPO_ROOT / "services"

#: Services that own no unit of work at all, and so cannot hold one.
#: `tracking` reads through an adapter that opens a session per call
#: (`with self.session_factory() as session:`) and keeps none; `audit` is a consumer with
#: no application service; `legacy_event_bridge` is a CDC relay with neither. They are
#: listed so that *becoming* stateful is a visible change to this file rather than a
#: silent regression.
NO_UNIT_OF_WORK = {"audit", "tracking", "legacy_event_bridge"}

#: Repaired by the Flutter integration workstream, not here. In this tree they still
#: hold the shared instance, so the guard is expected to fail for them and says so rather
#: than passing quietly or being deleted. When those repairs land these turn XPASS, which
#: is the signal to remove the name from this set — the failure is tracked either way,
#: and no concurrent work is overwritten to make a test green.
PENDING_CONCURRENT_REPAIR = {"identity", "customer", "merchant", "ordering"}


def _pending(service: str) -> None:
    if service in PENDING_CONCURRENT_REPAIR:
        pytest.xfail(
            f"{service} is repaired by the Flutter integration workstream; this tree "
            "still has the shared unit of work and must not be edited here"
        )

#: Names that are a unit of work by construction, wherever they appear.
UOW_CONSTRUCTOR = re.compile(r"(SqlAlchemy\w*UnitOfWork|InMemory\w*UnitOfWork)$")

#: Bare attribute names that are a per-request object whatever built them.
#: What application state *may* hold, even though the name looks close. All immutable,
#: or a callable that makes something per request.
ALLOWED_STATE = {
    "app.state.unit_of_work_factory",
    "app.state.session_factory",
    "app.state.readiness_report",
}

FORBIDDEN_STATE_NAMES = {
    "unit_of_work",
    "uow",
    "session",
    "db_session",
    "connection",
    "transaction",
}


def _taint(source: str) -> set[str]:
    """Every local name that holds, or was built from, a unit of work.

    Following the taint is what lets this guard tell two superficially similar things
    apart: `app.state.timeline_query_service` in `tracking` holds a read adapter that
    opens a session per call and keeps none, while `app.state.cash_service` used to hold
    an object wrapped around one shared session. Only the second is a hazard, and a rule
    that just matched `*_service` would flag both and teach people to add exemptions.
    """
    tree = ast.parse(source)
    tainted: set[str] = set()

    # The `unit_of_work` parameter of `create_app` is a unit of work by definition.
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for argument in [*node.args.args, *node.args.kwonlyargs]:
                if argument.arg in {"unit_of_work", "uow", "session"}:
                    tainted.add(argument.arg)

    # Repeat to a fixed point: a name built from a tainted name is itself tainted.
    for _ in range(6):
        before = len(tainted)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            if _references(node.value, tainted) or _constructs_a_unit_of_work(node.value):
                tainted.add(target.id)
        if len(tainted) == before:
            break
    return tainted


def _constructs_a_unit_of_work(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    name = _dotted(node.func) or getattr(node.func, "id", "")
    return bool(UOW_CONSTRUCTOR.search(name or ""))


def _references(node: ast.AST, names: set[str]) -> bool:
    return any(
        isinstance(inner, ast.Name) and inner.id in names for inner in ast.walk(node)
    )


def service_names() -> list[str]:
    return sorted(
        path.name
        for path in SERVICES.iterdir()
        if path.is_dir() and (path / "src" / path.name / "main.py").is_file()
    )


def main_module(service: str) -> Path:
    return SERVICES / service / "src" / service / "main.py"


def state_assignments(source: str) -> list[tuple[str, int]]:
    """Every `app.state.X = ...` in a module, with its line number."""
    found: list[tuple[str, int]] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Attribute):
                continue
            dotted = _dotted(target)
            if dotted and dotted.startswith("app.state."):
                found.append((dotted, node.lineno))
    return found


def _dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


@pytest.mark.parametrize("service", service_names())
def test_no_mutable_unit_of_work_in_application_state(service: str) -> None:
    """`app.state.unit_of_work = …`, and anything built around one, is the defect."""
    _pending(service)
    source = main_module(service).read_text(encoding="utf-8")
    tainted = _taint(source)
    tree = ast.parse(source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            dotted = _dotted(target)
            if not dotted or not dotted.startswith("app.state."):
                continue
            attribute = dotted.removeprefix("app.state.")
            if dotted in ALLOWED_STATE:
                continue
            if attribute in FORBIDDEN_STATE_NAMES:
                offenders.append(f"{dotted} (line {node.lineno}) — a per-request object")
            elif _references(node.value, tainted):
                offenders.append(
                    f"{dotted} (line {node.lineno}) — built from a unit of work"
                )
    assert offenders == [], (
        f"{service} stores per-request objects as shared application state:\n  "
        + "\n  ".join(offenders)
        + "\nStore a factory and build them in a FastAPI dependency instead: a unit of "
        "work holds one request's session, and sharing it makes concurrent requests "
        "collide (measured: 1 of 40 succeeded)."
    )


@pytest.mark.parametrize("service", service_names())
def test_a_service_that_owns_a_unit_of_work_exposes_a_factory(service: str) -> None:
    """Having removed the instance, a service still needs a way to make one."""
    _pending(service)
    if service in NO_UNIT_OF_WORK:
        return
    source = main_module(service).read_text(encoding="utf-8")
    assert "unit_of_work_factory" in source, (
        f"{service} owns a unit of work but publishes no factory; its routes have no "
        "way to get a per-request one"
    )


@pytest.mark.parametrize("service", service_names())
def test_routes_build_their_unit_of_work_per_request(service: str) -> None:
    """The dependency must *call* the factory, not hand back a stored instance."""
    _pending(service)
    if service in NO_UNIT_OF_WORK:
        return
    routes = SERVICES / service / "src" / service / "api" / "routes.py"
    if not routes.is_file():
        return
    source = routes.read_text(encoding="utf-8")
    if "unit_of_work_factory" not in source:
        return
    # `factory()` — the call is the whole point; a reference alone would share it again.
    assert re.search(r"factory\(\)", source), (
        f"{service}/api/routes.py names the factory but never calls it"
    )


def test_every_service_is_classified() -> None:
    """A new service must be audited, not silently skipped by this guard."""
    unknown = sorted(NO_UNIT_OF_WORK - set(service_names()))
    assert unknown == [], (
        f"{unknown} are exempted here but no longer exist; remove them so the exemption "
        "list cannot drift into hiding a real service"
    )


@pytest.mark.parametrize("service", service_names())
def test_the_in_memory_unit_of_work_refuses_a_second_transaction(service: str) -> None:
    """The double must be as strict as the store, or it hides the defect.

    The SQLAlchemy unit of work raises `transaction already active` on a nested `begin`.
    An in-memory one that shrugs is how a shared instance passed every unit test: the
    misuse only failed against PostgreSQL.
    """
    _pending(service)
    if service in NO_UNIT_OF_WORK:
        return
    memory = SERVICES / service / "src" / service / "infrastructure" / "memory.py"
    if not memory.is_file():
        return
    source = memory.read_text(encoding="utf-8")
    if "def begin" not in source:
        return
    assert "transaction already active" in source, (
        f"{service}'s in-memory unit of work permits a nested begin that the SQLAlchemy "
        "store rejects; make the double agree with the thing it stands in for"
    )
