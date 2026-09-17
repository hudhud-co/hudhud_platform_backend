# ADR-0013: Driver Workforce as a Distinct Bounded Context

- **Status:** accepted
- **Date:** 2026-09-14
- **Deciders:** platform architecture review; product owner (v6.3 authorization)
- **Workstream:** W25 — driver workforce build-out
- **Amends:** ADR-0011 (platform topology) — adds a fourteenth service
- **Implementation allowed:** yes

Label key: **[evidence]** verified from a product source or this repository; **[decision]**
binding; **[assumption]** engineering default.

## Context

**[evidence]** The Driver App v8 has three modules: `pickup`, `lastmile` and `account`. ADR-0011
assigned the first to `pickup` and the second to `delivery`, and split the third: the money half
(earnings, cash custody, deposits, settlement) to `finance` under ADR-0012. The remaining half has
no owner.

**[evidence]** That remainder is six catalogued requirements, and none of them is about a parcel:

| ID | Requirement | Screen |
|----|-------------|--------|
| SEC-03 | Driver applicant submits identity, vehicle, shifts and terms; the account starts `Pending verification`; physical office verification is required before any task assignment | `reg1`–`reg4` |
| DRV-A01 | Starting a shift records attendance; assigned pickups unlock on start | `shift` |
| DRV-A02 | A late start is recorded and can temporarily block assignment; support can clear it | `shift`, `blocked` |
| DRV-A03 | Leave request (full-day or hourly) with reason and note; no lateness penalty while pending | `leave`, `leaveDone` |
| DRV-A04 | Working-hours / shift-pattern management | `regHours`, `hoursEdit` |
| OPS-09 | Support clears lateness blocks and decides leave requests | `blocked`, `leaveDone` |

**[evidence]** Two services need the same answer from this data — *may this driver be given work
right now?* `pickup` assigns pickup batches and `delivery` assigns last-mile manifests. Today
neither can ask.

## Decision

**[decision]** Driver workforce is a bounded context of its own, owned by a new `workforce`
service. It owns driver onboarding and office verification, working-hours patterns, shift
attendance, lateness records and blocks, leave requests, and the derived **assignment
eligibility** fact.

**[decision]** `workforce` is the canonical writer of all six requirements above.
`architecture/ownership-matrix.yaml` records it as `driver_workforce`.

### Why not `identity`

**[decision]** `identity` owns *who a principal is and whether they may authenticate*. Workforce
owns *whether an authenticated driver may be given work today*. These have different lifecycles,
different deciders and different failure modes: a suspended principal must lose every live
session immediately, while a lateness block must leave the session intact and only withhold new
assignments. ADR-0011 already forbids `identity` from storing domain membership; vehicle details,
shift patterns and leave balances are domain membership.

### Why not `pickup`

**[decision]** Pickup owns driver pickup execution. Shift patterns and leave are neither pickup
nor custody, and `delivery` needs them just as much. Putting them in `pickup` would make
`delivery` depend on `pickup` for a fact that has nothing to do with parcels.

## Consequences

**[decision]** `workforce` exposes an assignment-eligibility query. `pickup` and `delivery` call
it over HTTP and never import it or read its tables, exactly as they call `identity` for
authorization.

**[decision]** `workforce` is forbidden from storing authentication credentials, from owning any
parcel or custody lifecycle state, and from any cash custody or finance posting — those belong to
`identity`, `shipment`/`pickup`/`delivery` and `finance` respectively.

**[assumption]** Office verification is recorded as an operations action against a driver
application. The exact document checklist is operational configuration, not an invented policy.

**[decision]** The lateness threshold and the block duration are configuration with explicit
values, not constants buried in code: v6.3 does not fix them, and a guessed threshold silently
penalises drivers.
