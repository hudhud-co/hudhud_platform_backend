# HUDHUD Claims Service

Bounded context owned: `support_claims` (ADR-0011).

Compensation claims, the support conversation that goes with one, driver incident
reports, and the operations view over both.

This service owns its own database, migrations and tests. It resolves callers by asking
Identity over HTTP and never imports another service's package or reads its tables.

## Requirements covered

| ID | Requirement |
|----|-------------|
| CLM-01 | HUDHUD is responsible while the parcel is in its custody and **compensates the sender** |
| CLM-02 | A claim may be opened by the sender, the receiver **or the driver** |
| CLM-03 | The scan and custody records are reviewed **before** compensating |
| CLM-04 | Approved ⇒ sender compensated; rejected ⇒ documented reason |
| CLM-05 | Responsibility during an at-the-door open-box check stays with HUDHUD; it ends if the receiver takes it inside to test it |
| CLM-06 | **No return window** after acceptance at the door — that decision is final |
| CLM-07 | Customer-side claim filing and the support conversation |
| CLM-08 | **Blocked** — the high-value declared-value threshold is a v6.3 Open Item |
| SEC-07 | A driver is **never** shown a compensation or claim value |
| DRV-P25 | Driver incident report, with the parcel held while it is open |
| OPS-06 | The returns-and-claims view |
| OPS-07 | Operations resolves driver incidents |

## Two questions that look like one

**Who may open a claim** and **who gets compensated** are different, and the model keeps
them apart. v6.3 p.42 lets the sender, the receiver or the driver open one; p.40 — a
Confirmed decision reversed from v5 — compensates the **sender**. A receiver may raise a
claim about a parcel they were paying cash for, and the money still goes back to the
person who paid HUDHUD to carry it. Collapsing the two is how a receiver ends up
compensated for a parcel they never paid for.

## Where responsibility ends

v6.3 p.37, and Customer App v3 says the same thing to the receiver in `openBoxBody`:

> You may open and inspect the parcel in front of the courier. If you take it inside to
> test it, HUDHUD's liability for damage ends there.

`CustodyBoundary` carries exactly that, and `HUDHUD_STILL_LIABLE` is written as the
**positive** set so a boundary added later defaults to "not ours" and has to be added
deliberately. Filing and approval consult the same set, so a claim is never accepted and
then refused for a reason that was knowable on day one.

`CLM-06` is a refusal rather than a missing route: `request_return` always raises, so the
absence reads as the decision it is. No return window is not the same as no recourse — a
claim is still possible.

## Review is a step, not a formality

v6.3 p.42: "Hudhud reviews scan and custody records before compensating." The state
machine makes `UNDER_REVIEW` the only route to a decision, and `custody_records_reviewed`
is the reviewer's explicit assertion that they looked. Approving or rejecting without it
is refused. "We reviewed the records" is the entire basis on which HUDHUD either pays or
declines to, so it is recorded rather than implied by a status.

## Who may do what

Deliberately **not** widened. Support can review and talk to the claimant; committing
HUDHUD to a payment is narrower.

| Action | Who |
|---|---|
| File a claim | The sender, receiver or driver |
| Read a claim / its thread | The claimant (who opened it, or who it compensates) — and support or operations |
| Withdraw | The claimant, while it is open |
| Review, read the custody record | Support or Operations |
| **Approve or reject** | **Operations or an accountant** — not support |
| Resolve a driver incident | **Operations only** (OPS-07) |
| Returns-and-claims view | Operations or support (OPS-06) |

## SEC-07 — the driver never sees a value

Driver App v8 `incidentDone`: *"No compensation or claim value is shown to the driver."*
The report screen adds *"No amounts are shown or decided here."*

Enforced by construction rather than by filtering:

* `DriverIncident` has **no compensation field at all** — a test asserts the absence;
* `ClaimSummaryForDriver` has six fields and none of them could hold a value;
* `may_see_a_compensation_value` excludes a driver **first**, so a driver who is also
  support is still a driver — the promise is about what a driver is shown;
* the OPS-06 view drops the amounts rather than refusing the view, so an operator who is
  also a driver still sees the rows.

## CLM-08 — the one Open Item

The high-value declared-value threshold (v6.3 Appendix A p.44). Above it a parcel gets
different handling and HUDHUD a different exposure. The number decides which parcels get
extra scrutiny, so `HighValuePolicy.threshold` has **no default** and asking
`is_high_value` raises until `CLAIMS_HIGH_VALUE_THRESHOLD` is set.

Everything else works without it: filing, evidence, review, approval, rejection, the
support thread and the operations views never ask.

## Request-scoped from the start

`InMemoryDatabase` holds the rows and is shared; `InMemoryUnitOfWork` holds one request's
transaction and is not. `begin` refuses a second transaction with the same message the
SQLAlchemy store uses. See `docs/audits/hudhud-app-redesign-v6.3/13-SHARED-UNIT-OF-WORK-P0.md`
for why this service was built that way rather than repaired into it.

## Validation

```bash
cd services/claims && uv run pytest
uv run ruff check .
```
