# 01 — Role / Screen / Action Inventory

Extracted from the executable sources. Screen keys are the literal routing keys in each app.

## A. Driver App v8 — 96 screens

`ROOTS = ['home','pickups','collected','history','profile']`; modules `pickup`, `lastmile`, `account`.

### A1. Pickup module (45 screens, `D().screens`)

| Group | Screen keys |
|-------|-------------|
| Entry | `home`, `home:empty` (no assigned work), `home:weak` (weak connection) |
| Work | `pickups` (assigned work — pickups **and** deliveries, seg filter all/pickup/delivery, list/map), `notifications` |
| Stops | `stop`, `navigating`, `arrived`, `progress`, `unavailable`, `stop:cancelled` |
| Scan | `scanner`, `matched`, `scanEx:unknown`, `scanEx:merchant`, `scanEx:pickup`, `scanEx:accepted`, `scanEx:cancelled`, `scanEx:unreadable`, `scanEx:duplicate` |
| Condition | `condition`, `photo`, `refuse`, `notAccepted` |
| Acceptance | `review`, `processing`, `accepted`, `accepted:warning`, `accepted:note` |
| Exceptions | `connLost`, `checking`, `recovered`, `safeRetry` |
| Collected | `collected`, `parcel`, `summary`, `summary:partial`, `stopDone` |
| Hub handoff | `hub`, `hubReady`, `hubProgress`, `hubIncomplete`, `hubIssue`, `hubDone` |
| History | `history`, `histSummary` |
| Profile / Incidents | `profile`, `incident`, `incidentDone` |

### A2. Last-mile module (25 screens)

`lmContext`, `lmArrived`, `lmWait`, `lmOtp`, `lmVerifying`, `lmIdEligible`, `lmIdCapture`,
`lmCannot`, `lmAuthorized`, `lmSeal`, `lmSealed`, `lmOpenBox`, `lmPhotoBefore`, `lmPhotoAfter`,
`lmPayPrepaid`, `lmPayCash`, `lmPayCard`, `lmPayProcessing`, `lmPayApproved`, `lmPayFailed`,
`lmCompleting`, `lmDelivered`, `lmRefuse`, `lmRefused`, `lmFailed`.

### A3. Account module (23 screens, `ACCOUNT_SCREENS`)

| Group | Screens |
|-------|---------|
| Onboarding | `splash`, `language`, `login`, `authOtp` |
| Sign-up | `reg1` driver details, `reg2` vehicle information, `regHours` working shifts, `reg3` terms, `reg4` application submitted |
| Shift & attendance | `shift`, `blocked` (lateness), `leave`, `leaveDone`, `hoursEdit` |
| Money | `earnings` (COD custody), `deposit`, `depositHistory`, `settlement`, `fee` (courier fee — sender pays) |
| Close of day | `hubReturn`, `dayEnd` |
| Account | `editProfile`, `sync` (sync queue) |

### A4. Driver scenarios (36, `D().scenarios` + `ACCOUNT_SCENARIOS`)

01 no assigned work · 02 pickup happy path · 03 multi-parcel · 04 partial pickup · 05 borderline
packaging · 06 pickup refusal · 07 pre-existing damage · 08 barcode mismatch · 09 photo
documentation · 10 acceptance network uncertainty · 11 collected inventory · 12 damage after
acceptance · 13 missing parcel report · 14 origin hub handoff · 15 partial hub handoff (11 of 12) ·
16 handoff mismatch · 17 cancelled pickup · 18 merchant unavailable · 19 OTP happy path · 20 OTP
invalid · 21 ID fallback · 22 ID mismatch · 23 open-box keep · 24 open-box refusal · 25 receiver
absent / 10-minute wait · 26 COD cash · 27 POS success · 28 POS failure → cash · 29 delivery
completed · 30 driver reports damage/loss · 31 new driver onboarding · 32 blocked for lateness ·
33 leave request · 34 cash deposit to exchange center · 35 courier fee — sender pays · 36 end of
day — hub return.

### A5. Driver actions, guards and literals that bind the backend

| Action | Guard / rule (verbatim from source) | Screen |
|--------|--------------------------------------|--------|
| Scan label | 8 outcomes: `valid`, `unknown`, `merchant`, `pickup`, `accepted`, `cancelled`, `unreadable`, `duplicate` | `scanner` |
| Unknown barcode | "This code is not registered in HUDHUD. An unregistered parcel cannot become a shipment in the field." | `scanEx:unknown` |
| Wrong merchant | "This label belongs to a different merchant's shipment." | `scanEx:merchant` |
| Not part of this pickup | "registered to *M* but is not expected at *PU-id*"; **"Add to this pickup"** button is `disabled` — "field additions are not defined yet" | `scanEx:pickup` |
| Already accepted | "Acceptance is recorded once — scanning it again changes nothing. No duplicate was created." | `scanEx:accepted` |
| Cancelled shipment | "The merchant cancelled this shipment before pickup. It cannot enter custody." | `scanEx:cancelled` |
| Label unreadable | "Typing the code or attaching a replacement label is **not allowed**." | `scanEx:unreadable` |
| Duplicate scan | "already being processed. Nothing was submitted twice." | `scanEx:duplicate` |
| Condition check | 4 outcomes `good` / `borderline` / `weak` / `damage`. `good`→Continue; `borderline`→Accept with warning **or** Refuse; `weak`→**Refuse only**; `damage`→Accept with note **or** Refuse | `condition` |
| Photo add-on | "Acceptance is blocked until one photo is attached"; `review` Accept button `disabled` with reason "Photo documentation is required before acceptance" | `photo`, `review` |
| Refuse pickup | reasons `weak` / `damaged` / `mismatch`; "The parcel stays with the merchant. Nothing enters HUDHUD custody." | `refuse`, `notAccepted` |
| Not presented | "The merchant did not hand over this parcel. It stays expected and unpicked — no penalty for anyone." | `progress` sheet |
| Accept | "Accepting moves *code* into HUDHUD custody. This is the only custody event and it is recorded once." | `review` |
| Complete stop | `disabled` while `cnt.pending > 0` — "every expected parcel needs an outcome" | `progress` |
| Cancelled pickup | scanning + acceptance disabled; "No parcel from this stop can enter HUDHUD custody." | `stop:cancelled` |
| Acceptance network loss | "Do not accept again — check the status first. Nothing is submitted twice." → `checking` → `recovered` ("Recorded once — no duplicate") or `safeRetry` ("The server has no record… Retrying will not create a duplicate") | `connLost` |
| Merchant unavailable | "The merchant no-show policy is **not defined yet**" (explicit TBD chip) | `unavailable` |
| Hub handoff | "Unscanned parcels stay in your custody"; Finish `disabled` — "handoff cannot be overridden"; issue → "stays in your inventory as a handoff issue" | `hubProgress`, `hubIssue`, `hubIncomplete` |
| Incident | 5 types: Damage after acceptance · Parcel missing · Label unreadable · Vehicle or safety issue · Handoff mismatch. "The parcel stays in your custody while the report is open." "No compensation or claim value is shown to the driver." | `incident` |
| Last-mile wait | 600 s countdown; "Record failed attempt" `disabled` until expiry | `lmWait` |
| Last-mile OTP | 6 digits; verify against shipment code; error "Code does not match — ask the receiver to check the latest SMS" | `lmOtp` |
| ID fallback | "ID fallback works only for the named receiver… Anyone else cannot take this parcel without the code." "The ID is checked, **not stored as a photo**." | `lmIdEligible`, `lmIdCapture` |
| Parcel seal | intact → continue; broken → "Stop and report" → incident | `lmSeal` |
| Open-box | merchant-enabled; keep → payment; refuse after inspection → `lmRefuse` | `lmOpenBox` |
| Payment | prepaid / cash / POS card. POS decline → "Fall back to cash" or retry. POS approval requires proof: transaction number **or** receipt photo (Complete `disabled` otherwise). "Card payments go straight to HUDHUD — this amount is not added to your cash on hand." | `lmPay*` |
| Cash COD | adds to driver cash on hand; "must be settled at a hub or approved exchange center before the end of your shift" | `lmPayCash` |
| Refusal | reasons changed / wrong / damaged, optional; "The parcel stays in HUDHUD custody. The return process begins outside the driver app." | `lmRefuse`, `lmRefused` |
| Cash deposit | methods exchange center / bank transfer / hub cashier; amount + reference + receipt photo all required | `deposit` |
| Cash limit | gauge vs `limit`; pending settlement frees the limit before confirmation | `earnings` |
| Shift | "Starting a shift records your attendance. Late starts are recorded and can temporarily block assignment." | `shift`, `blocked` |
| Sync queue | "Your actions are saved on the device and sent as soon as the connection returns. Nothing is lost and nothing is submitted twice." | `sync` |

## B. Customer App v3 — 105 screens

| Group (nav registry) | Screens |
|---|---|
| GET IN | `splashCust`, `splashCour`, `authSplash`, `authPhone` (phone · consent), `authOtp` (SMS code), `authName`, `authTerms`, `authNotify`, `authDone` |
| CORE | `home`, `parcels`, `pendLabel`, `courierSearch`, `parcelDetail`, `bulkDetail`, `bulkTransit`, `parcelTransit`, `parcelDelivered`, `shipmentTimeline`, `rateCourier`, `incoming`, `refused` |
| EDIT A SHIPMENT | `failed`, `editPick`, `editForm`, `editWarn`, `editSaved`, `editLimited`, `editTicket`, `editTicketDone` |
| TRACK | `track`, `trackResult` |
| SCHEDULE | `pickupTime` |
| SEND | `send1` sender, `send2` pickup, `send3` receiver, `send4` parcel, `send4store` contents, `send5` money, `created` |
| SAVED PRODUCTS | `pList`, `pSearch`, `pDetail`, `pAdd`, `pEdit`, `pDelete`, `pPicker`, `pCats`, `pCatAdd`, `pCatEdit`, `pCatDelete`, `pCatPick` |
| WORKING AT A STORE | `invite`, `jobAccepted`, `jobHome`, `jobParcel` (read-only) |
| STORE TEAM | `team`, `teamMember`, `teamForm`, `teamSent`, `teamDelete` |
| SELLER SEND | `sendStore`, `sendBulk`, `bulkRow`, `goodsCat`, `prohibited`, `sendStoreCreated` |
| ADDRESSES | `addressBook`, `addressBookMerchant`, `addContact`, `pickContact`, `pickAddress`, `contactAddr`, `addPickup`, `mapPicker` |
| SUPPORT | `support`, `supportNew`, `supportDetail`, `claimNew` |
| ACCOUNT | `notifications`, `notifPrefs`, `profile`, `editProfile` |
| ADD A STORE | `storeIntro`, `storeForm` (1/3), `storeAddress` (2/3), `storeReview` (3/3), `storeSubmitted`, `storeRejected`, `storeLive` |
| STORE | `seller`, `storeShipments`, `storeShipDetail`, `wallet`, `payout`, `payoutDest`, `payoutLog`, `payoutDone` |
| HANDOVER CEREMONY (sender half, reached from parcel detail) | `courierScan`, `courierSearch`, `courierWait`, `courierVerified`, `courierMismatch`, `rvVerify`, `rvError`, `rvCamera`, `rvReview` |
| EXCEPTIONS | `excReview`, `excCancelled`, `incomingReport`, `txnDetail`, `walletTxns` |

### B1. Customer actions, guards and literals that bind the backend

| Action | Rule (verbatim) | Screen |
|--------|------------------|--------|
| Sign in | phone + consent → SMS code → name → terms → notifications | `authPhone`…`authDone` |
| Personal send | "Personal parcels are handed in at a hub. No courier pickup" | `send2` |
| Hub drop deadline | "Unclaimed orders are cancelled after 3 days"; "the hub weighs the parcel and prints the label" | `created` |
| Label assignment | "Scan the QR on one of your pre-printed HUDHUD labels and stick it on the box. Couriers no longer bring labels" | `pendLabel` |
| Label gates pickup | "A courier is booked only once every parcel in this order carries a label"; "pickup time opens once every parcel carries a label" | `sendBulk` |
| No measuring | "No weighing or measuring needed — the courier checks it at pickup." | `send4` |
| Courier fee | "You pay this to the courier in cash when the parcels are collected." | `send5` |
| COD change window | "Must reach the courier before he is at the door. After that the old amount stands and the difference is settled through a claim." | `editForm` |
| Edit releases courier | "Changing the pickup time or address releases Yousif and the shipment waits for a new courier." | `editForm` |
| Edit lock | "Contents, dimensions and the pickup address are locked once the parcel is with the courier." | `editLimited` |
| Handover verification | sender verifies courier by 6-digit code (`otpSubmit`, `v === '482913'`) or scan; mismatch → `courierMismatch` | `rvVerify`, `courierScan` |
| Delivery code | "Give this 4-digit code to the courier so the drop can be confirmed"; "Provide the delivery code to anyone at the door, or present your matching ID" | `parcelTransit` |
| 10-minute wait | "Couriers will wait a maximum of 10 minutes at the location before rescheduling." | `parcelTransit` |
| Receiver report | reasons: damaged · wrong amount · wrong parcel · missing items · courier issue · other | `incomingReport` |
| Report courier | reasons: never came · very late · damaged · wrong COD · other | `supportNew` |
| Rate courier | 1–5 stars + tags; "Your rating stays private… The courier never sees your name or your note." | `rateCourier` |
| Claim | file a claim for damage/loss | `claimNew` |
| Merchant application | business (1/3) → address (2/3) → review (3/3) → under review → approved / needs changes | `storeForm`… |
| Wallet | COD "Collected by the courier and paid into your wallet" | `wallet` |
| Payout | destination, request, log | `payout*` |
| Team | invite member, roles, branch staff, remove | `team*` |
