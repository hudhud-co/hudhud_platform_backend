# HUDHUD Delivery Service

Bounded context owned: `delivery` (ADR-0003, ADR-0011).

The last mile: the manifest scan that transfers custody, the ten minutes at the door,
receiver verification, the seal check and open-box inspection, payment at the door, and
the three ways a visit ends — delivered, refused, or a failed attempt.

This service owns its own database, migrations, contracts and tests. It resolves callers
by asking Identity over HTTP and asks Workforce whether a driver may be given work at
all; it never imports another service's package and never reads its tables.

## Requirements covered

| ID | Requirement |
|----|-------------|
| DRV-L01 | The receiver is told before the driver sets off, with an **ETA, never an exact time** |
| DRV-L02 | The driver records arrival |
| DRV-L03 | Ten-minute wait; a failed attempt is only recordable after it expires |
| DRV-L04 | **Anyone holding the code** may receive the parcel, regardless of identity |
| DRV-L05 | The driver enters the code and the service validates it — **length undecided, see below** |
| DRV-L06 | The ID fallback works only for the merchant-named receiver |
| DRV-L07 | The ID is checked — **retention undecided, see below** |
| DRV-L08 | Verification failure ⇒ failed attempt; the parcel stays in HUDHUD custody |
| DRV-L09 | Parcel seal check before handover; a broken seal stops it |
| DRV-L10 | Open-box only when the merchant enabled it |
| DRV-L11 | Photo before opening and after inspection when the add-on is on |
| DRV-L12 | Payment: prepaid confirm · cash · POS card |
| DRV-L13 | A POS decline is never a dead end — cash can still fill the slot |
| DRV-L14 | POS approval requires proof: a transaction number or a receipt photo |
| DRV-L15 | Card money goes straight to HUDHUD and never enters driver cash custody |
| DRV-L16 | **A COD parcel is not Delivered unless payment was actually collected** |
| DRV-L17 | Delivery completes only with verification + payment + (add-on) proof |
| DRV-L18 | Refusal reasons are optional; nothing is collected; custody stays with HUDHUD |
| DRV-L19 | Failed-attempt reasons: absent after ten minutes · verification not completed · refusal |
| DRV-L20 | A three-day hold replaced the three-attempt limit; still undelivered ⇒ back to the merchant |
| DRV-L21 | The driver may open a damage or loss incident from a stop |
| CUS-11 | The receiver may state a handover window and an exact location |
| CUS-12 | The receiver may report a problem with an incoming parcel |
| CUS-13 | Rate the courier 1–5 with tags after the handover |
| SEC-08 | The rating is private: the courier never sees the rater or the note |
| OPS-08 | Operations decides the next attempt after a failure |

## The two unresolved product decisions

Both are contradictions **between authoritative sources**, not gaps. Neither is resolved
here, and neither blocks anything it does not have to.

### DRV-L05 — how long is the delivery code?

Customer App v3 tells the receiver it is four digits (`deliveryCodeNote`: "Give this
4-digit code to the courier"). Driver App v8 collects six (`lmOtp`: six boxes, fixture
`482913`, "Enter all 6 digits"). A receiver told to expect four cannot satisfy an app
demanding six, and the parcel does not get handed over.

`DELIVERY_CODE_LENGTH` therefore **has no default**. Code verification answers `501
delivery_code_length_not_decided` until it is set, and `GET /delivery/code-policy`
reports both readings so an operator can see why. Everything else at the door works
meanwhile, including the named-receiver ID fallback. Setting the variable opens the flow
with no code change, and both readings are already tested.

The code itself is **never stored**: what the database holds is an HMAC-SHA256 digest
keyed by `DELIVERY_CODE_HMAC_KEY` and bound to the stop, so a code lifted from one parcel
cannot be replayed against another. A check constraint refuses anything in that column
that is not 64 hex characters.

### DRV-L07 — is the receiver's ID photographed?

v6.3 p.26 says the driver photographs the ID card as a record. Driver App v8
(`lmIdCapture`) says the ID is checked and explicitly **not** stored as a photo.

So **no ID imagery is persisted and there is no column one could be persisted in** —
proven against real PostgreSQL by
`tests/new_service_migration_proof/test_delivery_migration.py`. An unrecorded photo can
be taken later if the business decides it must be; a wrongly retained one cannot be
untaken. Offering one to the API is refused with `501 id_photo_retention_not_decided`.
The ID verification itself (DRV-L06) is fully implemented.

## What is settled, and therefore a constant

* **Ten minutes at the door.** v6.3 p.26 and Customer App v3 `waitRuleBody` agree, so
  `DOOR_WAIT_SECONDS = 600` is a constant, not configuration.
* **A three-day hold.** v6.3 p.29 records it as a *Confirmed decision*, so `HOLD_DAYS = 3`
  is likewise a constant.

A hold can only expire by the clock, so something has to come and read it:
`POST /delivery/failed-attempts/return-past-hold` returns the parcels whose three days
are up. The actor who runs it is recorded on every parcel it moves — the schema requires
every disposition to name who made it, and OPS-08 exists to stop unattributed ones.

## Money

Integer minor units with an explicit currency, end to end: `BigInteger` columns, integer
fields in every event payload, and no float or `NUMERIC` on any monetary column. The only
`NUMERIC` in the schema is a map coordinate. The amount collected comes from the parcel,
never from something the driver typed.

Cash enters the driver's cash custody; **a card payment never does** — Driver App v8 is
explicit that "this amount is not added to your cash on hand", and the
`delivery.fact.cod_collected` schema enforces the distinction rather than trusting the
producer.

## Privacy

* No published fact carries a receiver's name, a landmark, a phone number or a code, and
  every envelope declares `pii_present: false`. A test walks the real payloads.
* Nothing in this service logs. The doorstep handles codes, names and amounts, and a log
  line is the easiest place for one to escape; a boundary test asserts no module imports
  `logging`.
* A courier reads their ratings as a `CourierRatingSummary`, which has no field for the
  rater and none for the note.

## Depends on

* **Identity**, over HTTP token introspection, for every request. No Identity configured
  means every request is denied.
* **Workforce** (ADR-0013), over HTTP, for whether a driver may be given work today. No
  Workforce configured means no driver is eligible. Delivery owns parcels at doors; it
  does not own shifts, leave or lateness.

## Publishes

| Fact | When |
|------|------|
| `delivery.fact.departed_for_receiver` | The driver sets off, with an ETA window |
| `delivery.fact.delivered` | The handover completes |
| `delivery.fact.cod_collected` | Money changed hands at the door |
| `delivery.fact.attempt_failed` | A refusal or a failed attempt |

Each is written to the transactional outbox in the same transaction as the state change
that caused it, and validated against its registered schema before it gets there.

## Validation

```bash
cd services/delivery && uv run pytest
uv run ruff check .

# From the repository root: the migration and the real unit of work against PostgreSQL 16
uv run pytest tests/new_service_migration_proof/test_delivery_migration.py
uv run pytest tests/new_service_migration_proof/test_delivery_store.py
```

Migration head: `w26_delivery_core_001`.
