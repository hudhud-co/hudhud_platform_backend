# 00 — Source of Truth

Audit of the HUDHUD microservice backend against the v6.3 product sources.

## Product sources located and read

| # | Source | Path | Size | Extraction | Read |
|---|--------|------|------|-----------|------|
| 1 | Shipment Journey Business Process Design **v6.3** (Aug 2026) | `docs/input/Hudhud_Shipment_Journey_Business_Process_Design_v6.3.pdf` | 5,427,818 B · 46 pages | `pypdf` text extraction, all 46 pages | Complete |
| 2 | **HUDHUD Customer App v3** (standalone) | `docs/input/HUDHUD Customer App v3 (standalone) .html` | 2,388,477 B | bundler manifest decoded (gzip+base64); `text/x-dc` source = 328,733 chars / 3,901 lines | Complete |
| 3 | **HUDHUD Driver App v8** (standalone) | `docs/input/HUDHUD Driver App v8 (standalone).html` | 1,616,795 B | bundler manifest decoded; `text/x-dc` source = 135,112 chars / 1,168 lines + 4 JS assets (demo data + 2 i18n dictionaries) | Complete |

No product source was missing. Nothing was inferred from filenames — every requirement below cites
executable source (screen key, handler, guard, or literal) or a numbered PDF page.

### How the HTML apps were read

Both files are self-extracting bundles: a `<script type="__bundler/manifest">` JSON map of
`uuid → {mime, compressed, data(base64)}`, plus a `<script type="__bundler/template">` JSON string
holding the page. The executable application is the `<script type="text/x-dc">` block inside that
template — a `class Component extends DCLogic` with the real routing, guards, validation and state
transitions. Screens, actions, form fields, disabled-button reasons and error copy were read from
that source, **not** from rendered text. The Driver App additionally ships `window.HH7` (demo
fixtures: pickups, parcels, last-mile stops, inventory, history, notifications) and two Arabic
dictionaries (`HH_AR`, `HH7_AR`) keyed by exact English UI strings — used to confirm the complete
user-visible string set.

## Roles discovered

| Role | Source | Evidence |
|------|--------|----------|
| Regular customer | Customer App v3 | nav group `GET IN`/`CORE`; `homeAcct` toggle; PDF ch.2 "Anyone can send a parcel" |
| Seller / merchant | Customer App v3 (same app) | nav groups `ADD A STORE`, `STORE`, `SELLER SEND`, `STORE TEAM`; `sendMode: 'store'` |
| Store team member (branch staff) | Customer App v3 | nav group `WORKING AT A STORE` — `invite`, `jobAccepted`, `jobHome`, `jobParcel` (read-only) |
| Receiver | Customer App v3 | `incoming`, `incomingReport`, `refused`, `trackResult` |
| Sender at handover (ceremony half) | Customer App v3 | `courierScan`, `courierSearch`, `courierWait`, `courierVerified`, `courierMismatch`, `rvVerify`, `rvError`, `rvCamera`, `rvReview` |
| Pickup driver | Driver App v8 | `module: 'pickup'`; 45 pickup screens |
| Last-mile driver | Driver App v8 | `module: 'lastmile'`; 25 `lm*` screens |
| Driver applicant | Driver App v8 | `reg1`–`reg4` sign-up |
| Hub staff (origin) | Driver App v8 + PDF ch.3/4 | `hubProgress` hub-scanner; PDF "hub staff stick a label… and scan it" |
| Hub cashier | PDF ch.8 | "Hub cashier" role-boundary block |
| Linehaul driver | PDF ch.5 | role-boundary block |
| Operations / support | Both apps | `blocked`, `leave`, incident triage, `excReview` |

The Customer App is multi-role as stated; no separate Seller App exists or was requested.
The Driver App carries three operational modes: `pickup`, `lastmile`, `account`.

## Source precedence applied

1. Explicit business invariants in v6.3 (Confirmed-decision and Role-boundary blocks)
2. Explicit interactive behaviour in Customer App v3 / Driver App v8
3. Approved ADRs and existing production compatibility constraints
4. Existing implementation

Conflicts and open items are recorded in `08-DECISIONS-CONFLICTS-ASSUMPTIONS.md`. Nothing that
v6.3 marks **Open item**, and nothing whose bounded-context owner is undecided, was invented here.
