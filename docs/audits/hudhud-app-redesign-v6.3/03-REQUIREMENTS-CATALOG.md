# 03 — Requirements Catalog

Every requirement cites its source. `PDF p.N` = page of the extracted v6.3 text.
`DRV:<screen>` = Driver App v8 screen key. `CUS:<screen>` = Customer App v3 screen key.

## SEC — Security, identity, authorization

| ID | Requirement | Evidence |
|----|-------------|----------|
| SEC-01 | Phone + OTP sign-in for customers, then profile completion (name), terms acceptance, notification opt-in | CUS:`authPhone`,`authOtp`,`authName`,`authTerms`,`authNotify` |
| SEC-02 | Driver signs in by phone OTP; session bound to the registered device | DRV:`login`,`authOtp`; i18n "OTP sign-in, then a secure session bound to your registered device" |
| SEC-03 | Driver applicant submits identity, vehicle, shifts, terms; account starts `Pending verification`; physical office verification required before task assignment | DRV:`reg1`–`reg4` |
| SEC-04 | Legal/terms version must be accepted and enforced | CUS:`authTerms`; DRV:`reg3` |
| SEC-05 | Role-aware access in one app: customer vs merchant vs store team member (read-only branch staff) | CUS nav `WORKING AT A STORE`, `jobParcel` read-only |
| SEC-06 | Pickup driver must never see receiver contact, delivery codes or payments | DRV:`collected` foot: "Receiver contact, delivery codes and payments are never shown to the pickup driver." |
| SEC-07 | Driver never sees compensation or claim value | DRV:`incidentDone`: "No compensation or claim value is shown to the driver." |
| SEC-08 | Courier rating is private; courier never sees rater name or note | CUS:`rateCourier` |
| SEC-09 | Every custody-changing action is attributable to a proven actor | PDF p.25 role boundaries; DRV acceptance/handoff |

## CUS — Regular customer

| ID | Requirement | Evidence |
|----|-------------|----------|
| CUS-01 | Any individual can create a shipment request without merchant status | PDF p.8 |
| CUS-02 | **A regular customer's parcel has no COD option** | PDF p.8 (Confirmed decision) |
| CUS-03 | **No pickup for a regular customer** — parcel is handed in at a hub | PDF p.8, p.15; CUS:`send2` "No courier pickup" |
| CUS-04 | Customer may pre-enter details or arrive with nothing; hub staff take details on the spot | PDF p.18 |
| CUS-05 | **Hub staff — not the customer — stick the label and scan it** | PDF p.8, p.18 |
| CUS-06 | Hub drop-off order is cancelled if not dropped within 3 days | CUS:`created` "Unclaimed orders are cancelled after 3 days" |
| CUS-07 | Hub weighs the parcel and prints the label at drop-off | CUS:`created` |
| CUS-08 | Track a parcel by tracking code without signing in | CUS:`track`,`trackResult`; PDF p.20 (SMS tracking link) |
| CUS-09 | Tracking timeline of checkpoint scans | CUS:`shipmentTimeline`; PDF ch.10 |
| CUS-10 | Address book: contacts, addresses, map pin | CUS:`addressBook`,`addContact`,`contactAddr`,`mapPicker` |
| CUS-11 | Receiver may specify handover time window and exact address/location via app | PDF p.20 |
| CUS-12 | Receiver sees an incoming parcel and may report a problem (damaged / wrong amount / wrong parcel / missing items / courier issue / other) | CUS:`incoming`,`incomingReport` |
| CUS-13 | Rate the courier 1–5 with tags after pickup | CUS:`rateCourier` |
| CUS-14 | Notification centre and per-channel preferences | CUS:`notifications`,`notifPrefs` |
| CUS-15 | Profile view/edit | CUS:`profile`,`editProfile` |

## MER — Seller / merchant

| ID | Requirement | Evidence |
|----|-------------|----------|
| MER-01 | Regular customer applies for merchant status in-app; Hudhud reviews; approved → merchant, rejected → stays regular and may re-apply | PDF p.9; CUS:`storeIntro`→`storeLive`/`storeRejected` |
| MER-02 | **Exact merchant-application data set is an Open Item** — "has not yet been defined and needs a deliberate answer before the application flow can be built" | PDF p.10, Appendix A p.44 |
| MER-03 | Merchant keeps a stock of **pre-printed** barcode labels; no per-parcel on-demand printing | PDF p.9, p.15 |
| MER-04 | A merchant may self-print only with the Hudhud-provided printer and blank stock | PDF p.12 |
| MER-05 | Registration = enter details → stick label → scan label (links it) → write receiver info by hand | PDF p.9 |
| MER-06 | Sender mandatory fields: receiver phone **and** governorate | PDF p.12 |
| MER-07 | Optional: receiver name, address, precise map pin | PDF p.12 |
| MER-08 | **Weight and size are optional**; a parcel description is **required** | PDF p.12 (Confirmed decision); CUS:`send4` "No weighing or measuring needed" |
| MER-09 | Payment terms: prepaid / postpaid / COD | PDF p.12 |
| MER-10 | Open-box allowed per shipment or as standing policy; **off by default** | PDF p.13 |
| MER-11 | Photo-documentation add-on, free; when on, a photo is taken at pickup and at delivery (before and after inspection when open-box is also on); without it, **no photo at any stage** | PDF p.13, p.14 |
| MER-12 | Hudhud-supplied packaging add-on with a per-parcel scannable seal sticker; scanned at delivery; intact-seal confirmation sent to the merchant | PDF p.14 |
| MER-13 | One order can produce more than one shipment | PDF p.14 |
| MER-14 | Sender sets: open-box, who pays the delivery fee, commercial terms, photo add-on, packaging add-on | PDF p.13 |
| MER-15 | Sender may **not** set: acceptance/packaging standard, return-fee ownership, refund ownership, 3-day hold, 10-minute wait, liability position | PDF p.13 (Role boundary — Sender) |
| MER-16 | Stores and store pickup addresses; multiple branches | CUS:`addPickup`,`addressBookMerchant`,`editBranchList` |
| MER-17 | Saved products and product categories, reusable in shipment contents | CUS:`pList`…`pCatDelete` |
| MER-18 | Bulk / batch shipment creation | CUS:`sendBulk`,`bulkRow`,`bulkDetail`,`bulkTransit` |
| MER-19 | **Pickup booking is gated on every parcel in the order carrying a label** | CUS:`sendBulk` "pickup time opens once every parcel carries a label"; `pendLabel` |
| MER-20 | Store team: invite members, roles, branch assignment, remove | CUS:`team*`,`invite`,`jobAccepted` |
| MER-21 | Store dashboard and store shipment list/detail | CUS:`seller`,`storeShipments`,`storeShipDetail` |
| MER-22 | Prohibited-goods list shown during send | CUS:`prohibited` |
| MER-23 | Goods category / kind of goods selection | CUS:`goodsCat`,`pCatPick` |

## SHP — Shared shipment lifecycle

| ID | Requirement | Evidence |
|----|-------------|----------|
| SHP-01 | Eight stages; first seven in fixed order; the eighth (Refusal & Return) activates only when the receiver does not keep the parcel | PDF p.7 |
| SHP-02 | **The 24-hour delivery window is a goal, not a promise**, measured from the acceptance scan — not from order creation | PDF p.7 (Confirmed decision) |
| SHP-03 | Custody begins at the driver's acceptance scan (merchant path) or the hub staff scan (customer drop-off path) | PDF p.16, p.20 |
| SHP-04 | Every stage is scanned (checkpoint scans: pickup, hub arrival, hub departure, hand-off to last-mile) | PDF p.7, p.5 |
| SHP-05 | Origin hub: scan in, sort by destination/urgency/route, group, optional security seal at hub discretion | PDF p.22 |
| SHP-06 | Same-city shipments skip the hub-to-hub stage | PDF p.22 |
| SHP-07 | Inter-city moves overnight after a **per-hub** cut-off time | PDF p.23 |
| SHP-08 | Destination hub: scan in; if sealed, check the seal; mismatch opens a tamper investigation, never a silent pass-through | PDF p.25 |
| SHP-09 | Last-mile driver builds their manifest by scanning assigned parcels; **that scan transfers custody** | PDF p.25, p.26 |
| SHP-10 | Shipment cancellation before pickup; cancelled shipments cannot enter custody | DRV:`scanEx:cancelled`; CUS:`shipCancelConfirm` |
| SHP-11 | Edit rules by stage: free before a courier is assigned; assigning/changing pickup time or address **releases the courier**; contents, dimensions and pickup address **lock** once with the courier; later edits go through a support ticket | CUS:`editPick`/`editForm`/`editWarn`/`editLimited`/`editTicket` |
| SHP-12 | COD amount change must reach the courier before the door; after that the old amount stands and the difference is settled through a claim | CUS:`editForm` |
| SHP-13 | Checkpoint opening a parcel in inter-city transit ⇒ parcel is returned to the merchant | PDF p.24 (Confirmed decision) |

## DRV — Driver (pickup + last-mile + account)

### DRV-P — Pickup

| ID | Requirement | Evidence |
|----|-------------|----------|
| DRV-P01 | Pickup is assigned **for merchants only** | PDF p.15 |
| DRV-P02 | Assigned-work list mixes pickups and deliveries with filters and list/map views | DRV:`pickups` |
| DRV-P03 | Stop lifecycle: upcoming/ready → enroute → arrived → in progress → completed/partial; arrival is a neutral event, nothing has changed hands | DRV:`stop`,`arrived` |
| DRV-P04 | Navigation runs in the driver's own maps app — Hudhud does not route | DRV:`navigating` |
| DRV-P05 | **The driver scans a label already on the parcel**; drivers never carry, print, replace or type a code | PDF p.15; DRV:`matched`,`scanEx:unreadable` |
| DRV-P06 | Scan resolves to exactly one of 8 outcomes: valid · unknown barcode · wrong merchant · not part of this pickup · already accepted · cancelled shipment · unreadable · duplicate | DRV:`scanner`,`scanEx:*` |
| DRV-P07 | Adding an unscheduled parcel in the field is **not defined yet** (button disabled) | DRV:`scanEx:pickup` |
| DRV-P08 | Condition check judges **packaging only, not contents**, with 4 outcomes | DRV:`condition`; PDF p.16 |
| DRV-P09 | `good` → accept normally | DRV:`condition`; PDF p.17 |
| DRV-P10 | `borderline` → driver's judgment: accept **with warning** or refuse | DRV:`condition`; PDF p.16, p.17 |
| DRV-P11 | **`weak` (too weak) → refuse only**; it cannot be accepted | DRV:`condition` hint "Too weak packaging can only be refused" |
| DRV-P12 | `damage` (visible pre-existing damage) → accept **with a condition note** (evidence, not a compensation decision) or refuse | DRV:`condition`; PDF p.16 |
| DRV-P13 | When the photo add-on is on, **acceptance is blocked until a photo is attached** | DRV:`photo`,`review` |
| DRV-P14 | Refusal reasons: packaging too weak · damaged before pickup · does not match the shipment. The parcel stays with the merchant; **no custody event is recorded** | DRV:`refuse`,`notAccepted` |
| DRV-P15 | A parcel the merchant did not hand over is marked **not presented** — stays expected and unpicked, no penalty | DRV:`progress` sheet |
| DRV-P16 | Acceptance is **the only custody event** and is recorded exactly once | DRV:`review`,`accepted` |
| DRV-P17 | A stop can only be completed when **every expected parcel has an outcome** | DRV:`progress` disabled reason |
| DRV-P18 | A cancelled pickup disables scanning and acceptance entirely | DRV:`stop:cancelled` |
| DRV-P19 | On connection loss during acceptance the driver must be able to **query acceptance status** rather than re-accept; result is either "recorded once — no duplicate" or "no record — safe to retry" | DRV:`connLost`,`checking`,`recovered`,`safeRetry` |
| DRV-P20 | Merchant unavailable / no-show: **policy not defined yet** | DRV:`unavailable` (explicit TBD) |
| DRV-P21 | Driver carries an inventory of parcels in custody, searchable by code | DRV:`collected` |
| DRV-P22 | Hub handoff is reconciled parcel by parcel and **can never be overridden**; unscanned parcels stay in driver custody | DRV:`hubProgress`,`hubIncomplete` |
| DRV-P23 | A hub handoff issue keeps the parcel in driver custody until operations resolve it | DRV:`hubIssue` |
| DRV-P24 | Pickup history per stop with outcome counts | DRV:`history`,`histSummary` |
| DRV-P25 | Driver incident report: damage after acceptance · parcel missing · label unreadable · vehicle or safety issue · handoff mismatch. The parcel stays in custody while the report is open | DRV:`incident`,`incidentDone` |
| DRV-P26 | Offline sync queue: actions saved on device, sent when connection returns, nothing lost, nothing submitted twice | DRV:`sync` |

### DRV-L — Last-mile

| ID | Requirement | Evidence |
|----|-------------|----------|
| DRV-L01 | Before setting off the receiver gets app notification + SMS + phone call, giving an **ETA, not an exact time** | PDF p.26 |
| DRV-L02 | Driver records arrival at the receiver | DRV:`lmArrived` |
| DRV-L03 | **10-minute wait**; failed attempt only recordable after expiry | PDF p.26; DRV:`lmWait` (600 s) |
| DRV-L04 | **Anyone holding the OTP may receive the parcel**, regardless of identity | PDF p.26 |
| DRV-L05 | OTP is entered by the driver into the driver app, which validates it | PDF p.26; DRV:`lmOtp` |
| DRV-L06 | ID fallback works **only** for the merchant-named receiver | PDF p.26; DRV:`lmIdEligible` |
| DRV-L07 | Driver checks the ID against on-file details **and photographs the ID card as a record** | PDF p.26 |
| DRV-L08 | Verification failure ⇒ failed attempt; parcel stays in Hudhud custody | DRV:`lmCannot`,`lmFailed` |
| DRV-L09 | Parcel seal check before handover; broken seal ⇒ stop and report | DRV:`lmSeal`; MER-12 |
| DRV-L10 | Open-box only when the merchant enabled it; otherwise accept/refuse sealed | PDF p.35, p.36; DRV:`lmSealed`,`lmOpenBox` |
| DRV-L11 | Photo before opening and after inspection when the photo add-on is on | MER-11; DRV:`lmPhotoBefore`,`lmPhotoAfter` |
| DRV-L12 | Payment methods: prepaid confirm · cash · POS card | PDF p.31; DRV:`lmPay*` |
| DRV-L13 | **POS decline is never a dead end — fall back to cash** | PDF p.31; DRV:`lmPayFailed` |
| DRV-L14 | POS approval requires proof: transaction number **or** receipt photo, before handover | DRV:`lmPayApproved` |
| DRV-L15 | Card payments go straight to Hudhud and do **not** enter driver cash custody | DRV:`lmPayApproved` |
| DRV-L16 | **A COD parcel is not Delivered unless payment was actually collected** | PDF p.31 |
| DRV-L17 | Delivery completes only with verification + payment + (add-on) proof | PDF p.30; DRV:`lmDelivered` |
| DRV-L18 | Receiver refusal reasons (optional): changed mind · wrong item · looks damaged; nothing collected; parcel stays in Hudhud custody | PDF p.34; DRV:`lmRefuse`,`lmRefused` |
| DRV-L19 | Failed-attempt reasons: absent after 10 min · verification could not be completed · refusal | PDF p.28 |
| DRV-L20 | **3-day hold** replaces the earlier three-attempt limit; still undelivered ⇒ returned to merchant as a failed delivery | PDF p.29 (Confirmed decision) |
| DRV-L21 | Driver may open a damage/loss incident from a delivery stop | DRV:`lmIncident` |

### DRV-A — Driver account, shift, money

| ID | Requirement | Evidence |
|----|-------------|----------|
| DRV-A01 | Start shift records attendance; assigned pickups unlock on start | DRV:`shift` |
| DRV-A02 | Late start is recorded and can temporarily **block assignment**; support can clear it | DRV:`shift`,`blocked` |
| DRV-A03 | Leave request (full-day or hourly) with reason and note; no lateness penalty while pending | DRV:`leave`,`leaveDone` |
| DRV-A04 | Working-hours / shift-pattern management | DRV:`regHours`,`hoursEdit` |
| DRV-A05 | Driver cash-on-hand record of collected COD | PDF p.31; DRV:`earnings` |
| DRV-A06 | **Per-driver cash limit** with utilisation | PDF p.31; DRV:`earnings` |
| DRV-A07 | Deposit cash by exchange center · bank transfer · hub cashier; amount + reference + receipt photo all required | PDF p.31; DRV:`deposit` |
| DRV-A08 | Exchange-office transfer lets the driver keep collecting immediately while an accountant verifies the receipt afterward | PDF p.31, p.33 |
| DRV-A09 | Pending settlement frees the driver's limit before confirmation | DRV:`earnings` |
| DRV-A10 | Settlement history with ranges and per-settlement receipt | DRV:`depositHistory`,`settlement` |
| DRV-A11 | Courier fee collected from the sender at pickup (cash, or POS with reference + slip) | DRV:`fee`; CUS:`send5` |
| DRV-A12 | End-of-day hub return: hand over parcels **and** settle cash, both confirmed | PDF p.33; DRV:`hubReturn`,`dayEnd` |
| DRV-A13 | End-of-route reconciliation checks cash handed over against what was expected; mismatch investigated before payout | PDF p.31, p.33 |

## PAY — COD, wallet, refund, settlement

| ID | Requirement | Evidence |
|----|-------------|----------|
| PAY-01 | COD counts as paid only when: an online payment succeeded, a POS card payment was approved, or physical cash was collected **and handed to the hub cashier** | PDF p.33 (Confirmed decision) |
| PAY-02 | Includes the case where the receiver comes to a hub in person to collect and pay | PDF p.33 |
| PAY-03 | **No digital wallet payment option for receivers** | PDF p.33 |
| PAY-04 | Cash-transfer-via-exchange-office is **not** a valid way to confirm the original payment (only to settle cash already collected) | PDF p.33 |
| PAY-05 | Merchant running balance reflects confirmed deliveries and reconciled cash | PDF p.31; CUS:`wallet` |
| PAY-06 | Payout on request via bank transfer · Mastercard · money-exchange partner · in-person hub collection | PDF p.31, p.34; CUS:`payout*` |
| PAY-07 | **Exact procedure for each payout method is an Open Item** needing an accountant | PDF p.34, Appendix A p.44 |
| PAY-08 | Refusal ⇒ merchant charged **both** the return-trip fee and the original delivery fee, regardless of reason | PDF p.38, p.39 |
| PAY-09 | Refund only when the receiver paid **Hudhud** directly in advance; otherwise merchant↔receiver | PDF p.39 |
| PAY-10 | Delivery fee is payable by the sender at pickup in cash (per current app) | CUS:`send5`; DRV:`fee` |
| PAY-11 | All money is IQD | DRV `fmtIQD`; CUS COD copy |

## CLM — Claims and compensation

| ID | Requirement | Evidence |
|----|-------------|----------|
| CLM-01 | **Hudhud takes full responsibility for parcel safety while in its custody** and compensates the sender | PDF p.40 (Confirmed decision, reversed from v5) |
| CLM-02 | A compensation claim may be opened by the **sender, receiver, or driver** | PDF p.42 |
| CLM-03 | Hudhud reviews scan and custody records before compensating | PDF p.42 |
| CLM-04 | Outcome: approved ⇒ sender compensated; rejected ⇒ documented reason given | PDF p.43 |
| CLM-05 | Responsibility during an at-the-door open-box inspection stays with Hudhud; it **ends if the receiver takes the parcel inside** to test it | PDF p.37 (Confirmed decision) |
| CLM-06 | There is **no return window after acceptance** at the door — that decision is final | PDF p.34 |
| CLM-07 | Customer-side claim filing and support conversation | CUS:`claimNew`,`support*` |
| CLM-08 | **High-value declared-value threshold is an Open Item** | PDF Appendix A p.44 |

## NTF — Notifications

| ID | Requirement | Evidence |
|----|-------------|----------|
| NTF-01 | On acceptance (pickup **or** hub drop-off) notifications go out immediately | PDF p.20 |
| NTF-02 | Sender gets an app notification confirming acceptance | PDF p.20 |
| NTF-03 | Receiver **always** gets an SMS containing the OTP and a tracking link | PDF p.20 |
| NTF-04 | Receiver also gets an app notification if the app is installed | PDF p.20 |
| NTF-05 | If the receiver's number has WhatsApp, a more detailed WhatsApp message encouraging app install | PDF p.20 |
| NTF-06 | The tracking website shows the same install encouragement | PDF p.20 |
| NTF-07 | Pre-delivery: app notification + SMS + phone call before the driver sets off | PDF p.26 |
| NTF-08 | Intact-seal confirmation sent to the merchant at delivery | PDF p.14 |
| NTF-09 | Driver notification centre from operations | DRV:`notifications` |
| NTF-10 | Customer notification preferences per channel | CUS:`notifPrefs` |

## OPS — Operational workflow and visibility

| ID | Requirement | Evidence |
|----|-------------|----------|
| OPS-01 | Inter-city vehicle tracking, plus the driver device as an independent second location source | PDF p.24 |
| OPS-02 | Route-deviation watch and continuously updated ETA | PDF p.24 |
| OPS-03 | Delivery-goal risk view: which parcels risk missing the 24-hour goal and why | PDF p.43 |
| OPS-04 | **Cash exposure view**: which drivers hold how much, and who is over limit | PDF p.43 |
| OPS-05 | Hub activity view: backlog, ready/delayed/missing parcel groups | PDF p.43 |
| OPS-06 | Returns & claims view | PDF p.43 |
| OPS-07 | Operations resolves driver incidents and hub handoff exceptions | DRV:`hubIssue`,`incidentDone` |
| OPS-08 | Operations decides the next delivery attempt after a failure | DRV:`lmFailed` |
| OPS-09 | Support clears lateness blocks and decides leave requests | DRV:`blocked`,`leaveDone` |
| OPS-10 | Cameras may be added inside inter-city vehicles | PDF p.24 |
| OPS-11 | Audit trail with actor attribution across the journey | PDF ch.10; DRV/CUS timelines |

## Open items — must not be invented (PDF Appendix A, p.44)

| Open item | Blocks |
|-----------|--------|
| Merchant-application data set and approval decision process | MER-01/MER-02 |
| Exact procedure per payout method | PAY-06/PAY-07 |
| High-value declared-value threshold | CLM-08 |
| Showing the customer their exact delivery goal | SHP-02 display |
| Whether Hudhud waives the return-trip fee for merchants | PAY-08 |

App-declared TBDs (explicit in the Driver App source): merchant wait / no-show policy (DRV-P20),
warning acknowledgement by merchant, damage evidence rules at pickup, adding an unscheduled parcel
in the field (DRV-P07), hub handoff exception handling, offline acceptance authorisation.
