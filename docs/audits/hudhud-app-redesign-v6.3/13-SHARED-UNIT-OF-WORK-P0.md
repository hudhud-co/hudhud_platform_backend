# 13 — The shared unit-of-work defect (P0)

Written for: engineers on any HUDHUD backend workstream.

## What was wrong

Every service built one `SqlAlchemy…UnitOfWork` at startup, stored it on `app.state`, and
constructed its application services around it:

```python
uow = SqlAlchemyFinanceUnitOfWork(session_factory=build_session_factory(engine))
app.state.unit_of_work = uow
app.state.cash_service = CashService(uow, ...)     # one instance, every request
```

A unit of work keeps `_session` and the pending writes **on itself**. One instance shared
across requests means one session shared across requests.

### Measured, not assumed

Forty concurrent requests against real PostgreSQL, before the repair:

```
succeeded: 1/40
   39 x RuntimeError:  transaction already active
```

After:

```
succeeded: 40/40
```

Nothing in 2,000+ unit tests caught it, because a test issues one request at a time.

## The repair

Application state holds what it takes to *build* a unit of work, never one:

```python
def unit_of_work_factory():
    return SqlAlchemyFinanceUnitOfWork(session_factory=_session_factory)

app.state.unit_of_work_factory = unit_of_work_factory
```

and the FastAPI dependency builds one per request. FastAPI caches a dependency's value for
the lifetime of a single request, so every service in that request shares that request's
transaction and no other's:

```python
def get_unit_of_work(request: Request) -> FinanceUnitOfWork:
    return request.app.state.unit_of_work_factory()

def get_cash(request: Request, unit_of_work: UnitOfWork) -> CashService:
    return CashService(unit_of_work, ...)
```

The application services are built per request too, because each holds a reference to the
unit of work. They are cheap: a couple of object allocations against a request that is
already doing I/O.

### In-memory stores got the same shape

`InMemoryDatabase` holds the rows and is shared, exactly as a database is.
`InMemoryUnitOfWork` holds one request's transaction and is not. `new_unit_of_work()`
makes another over the same rows.

## Scope

| Service | State | Note |
|---|---|---|
| `hub` · `notification` · `workforce` · `delivery` · `finance` · `pickup` · `shipment` | **repaired here** | factory + per-request services |
| `identity` · `customer` · `merchant` · `ordering` | **owned by the Flutter integration workstream** | untouched here; see below |
| `tracking` | not affected | its read adapter opens a session per call and keeps none |
| `audit` · `legacy_event_bridge` | not affected | no application service, no unit of work |

### A discrepancy worth reconciling

The brief said identity, customer, merchant and ordering were already repaired. **In this
tree they are not.** Their `main.py` files still hold `app.state.unit_of_work = uow` and
have not been modified since 14 September. They were left exactly as found, so nothing
from the other workstream can be overwritten, and
`tests/architecture/test_request_scoped_state.py` marks them `xfail` with that reason
rather than passing quietly or being deleted. When the repairs land those turn XPASS,
which is the signal to remove them from `PENDING_CONCURRENT_REPAIR`.

## Two further defects the repair uncovered

**The durable inbox could never have committed against PostgreSQL.** `InboxService.handle`
opens a transaction and calls the handler inside it — correct, because the deduplication
record and the effect it guards must commit together. But the handlers called transactional
service methods, which opened a *second* transaction on the same unit of work. The
in-memory double permitted the nested `begin`; SQLAlchemy does not. Fixed by documenting the
contract and giving the services a `*_within_transaction` variant for handlers to call.

**Identity's token introspection never worked against PostgreSQL** (found earlier, recorded
in `12-RUNNING-THE-PLATFORM.md`): it read the session, principal and grants without opening
a transaction at all. Same root cause — a double more permissive than the store it stands
in for.

The lesson is the same in all three cases and is now enforced: **a fake that is more
generous than the real thing hides the bug it exists to catch.** Every in-memory unit of
work now refuses a nested `begin` with the same message the SQLAlchemy store uses, and the
ones that fall back to committed state outside a transaction were made strict.

## What now enforces it

**`tests/architecture/test_request_scoped_state.py`** — a structural guard, because a
concurrency bug reproduces only under load and by then it is in production. It parses each
`main.py`, follows the taint from the unit of work through local variables, and fails if
anything built from one reaches `app.state`. Following the taint is what lets it tell
`tracking`'s stateless read adapter apart from a service wrapped around a shared session,
instead of matching `*_service` and teaching people to add exemptions.

**`tests/new_service_migration_proof/test_request_isolation.py`** — 50 tests against real
PostgreSQL, per service, proving:

1. forty concurrent requests each get their own session;
2. two principals cannot observe each other's rows;
3. commit, rollback and exception all reset and close request state;
4. a failed request does not poison the next;
5. background work cannot inherit a request session;
6. concurrent reads and writes never produce "transaction already active";
7. the in-memory store enforces the same rules.

```bash
uv run pytest tests/architecture/test_request_scoped_state.py
uv run pytest tests/new_service_migration_proof/test_request_isolation.py
```
