# 11 — Requirement traceability: why the total is 160 and not 140

> Machine-checked by `scripts/quality/verify_requirement_traceability.py`, which
> re-derives every figure below from `03-REQUIREMENTS-CATALOG.md`,
> `04-GAP-AND-CHANGE-MATRIX.md` and `requirement-accounting.yaml`. Nothing here is
> restated from memory, and the script fails if any of it drifts.

## The finding

**No requirement was added.** The catalogue has held **160 unique requirement rows**
since it was written, each on exactly one table row, and every one of them is
accounted for exactly once today.

The **140** comes from one place: the summary table in
`04-GAP-AND-CHANGE-MATRIX.md` §Summary. That figure is the sum of its own six status
buckets, and those buckets counted *requirements that had been given a status in that
wave's matrix* — not requirements that exist.

| Bucket in `04` §Summary | Count |
|---|---:|
| `IMPLEMENTED_EXACT` | 21 |
| `PARTIAL` | 7 |
| `MISSING` | 9 |
| `BLOCKED_BY_EXTERNAL_DEPENDENCY` | 96 |
| `UI_ONLY` | 4 |
| `CONFLICT` | 3 |
| **Sum** | **140** |
| Catalogued requirements | **160** |
| **Never given a status there** | **20** |

So the twenty-requirement difference is an **under-classification**, not growth. The
earlier note in `RESUME-HERE.md` explaining it as "the 60 driver requirements were
omitted" was wrong on its own arithmetic — 160 − 60 is 100, not 140 — and has been
corrected.

## Each family traces to one catalogue section

<!-- generated:family-summary -->
| Family | Catalogue section | Count | Complete | Planned | Blocked | Conflict |
|---|---|---:|---:|---:|---:|---:|
| `CLM` | CLM — Claims and compensation | 8 | 7 | 0 | 1 | 0 |
| `CUS` | CUS — Regular customer | 15 | 15 | 0 | 0 | 0 |
| `DRV-A` | DRV-A — Driver account, shift, money | 13 | 13 | 0 | 0 | 0 |
| `DRV-L` | DRV-L — Last-mile | 21 | 19 | 0 | 0 | 2 |
| `DRV-P` | DRV-P — Pickup | 26 | 26 | 0 | 0 | 0 |
| `MER` | MER — Seller / merchant | 23 | 22 | 0 | 1 | 0 |
| `NTF` | NTF — Notifications | 10 | 10 | 0 | 0 | 0 |
| `OPS` | OPS — Operational workflow and visibility | 11 | 10 | 1 | 0 | 0 |
| `PAY` | PAY — COD, wallet, refund, settlement | 11 | 9 | 0 | 2 | 0 |
| `SEC` | SEC — Security, identity, authorization | 9 | 8 | 1 | 0 | 0 |
| `SHP` | SHP — Shared shipment lifecycle | 13 | 10 | 2 | 1 | 0 |
| **Total** | | **160** | **149** | **4** | **5** | **2** |
<!-- /generated:family-summary -->

No family overlaps another: a requirement id belongs to exactly one prefix, and the
counts above sum to the catalogue total. `DRV` is split into its three published
sub-families (`DRV-P` pickup, `DRV-L` last mile, `DRV-A` driver account) exactly as
the catalogue splits them, so no driver requirement is counted twice.

## Every requirement, and the catalogue line it comes from

`Catalogue line` is the line number in `03-REQUIREMENTS-CATALOG.md` where the
requirement is defined. An accounting row whose id does not appear there fails the
verifier as invented; a catalogue row with no accounting entry fails it as dropped.

<!-- generated:requirement-rows -->
| ID | Catalogue line | Owner | Wave | Status |
|---|---:|---|---|---|
| `CLM-01` | 183 | `claims` | W28 | complete |
| `CLM-02` | 184 | `claims` | W28 | complete |
| `CLM-03` | 185 | `claims` | W28 | complete |
| `CLM-04` | 186 | `claims` | W28 | complete |
| `CLM-05` | 187 | `claims` | W28 | complete |
| `CLM-06` | 188 | `claims` | W28 | complete |
| `CLM-07` | 189 | `claims` | W28 | complete |
| `CLM-08` | 190 | `claims` | W28 | **blocked** |
| `CUS-01` | 24 | `ordering` | W22 | complete |
| `CUS-02` | 25 | `ordering` | W22 | complete |
| `CUS-03` | 26 | `hub` | W23 | complete |
| `CUS-04` | 27 | `hub` | W23 | complete |
| `CUS-05` | 28 | `hub` | W23 | complete |
| `CUS-06` | 29 | `hub` | W23 | complete |
| `CUS-07` | 30 | `hub` | W23 | complete |
| `CUS-08` | 31 | `ordering` | W22 | complete |
| `CUS-09` | 32 | `tracking` | W17 | complete |
| `CUS-10` | 33 | `customer` | W20-B | complete |
| `CUS-11` | 34 | `delivery` | W26 | complete |
| `CUS-12` | 35 | `delivery` | W26 | complete |
| `CUS-13` | 36 | `delivery` | W26 | complete |
| `CUS-14` | 37 | `notification` | W24 | complete |
| `CUS-15` | 38 | `customer` | W20-B | complete |
| `DRV-A01` | 149 | `workforce` | W25 | complete |
| `DRV-A02` | 150 | `workforce` | W25 | complete |
| `DRV-A03` | 151 | `workforce` | W25 | complete |
| `DRV-A04` | 152 | `workforce` | W25 | complete |
| `DRV-A05` | 153 | `finance` | W27 | complete |
| `DRV-A06` | 154 | `finance` | W27 | complete |
| `DRV-A07` | 155 | `finance` | W27 | complete |
| `DRV-A08` | 156 | `finance` | W27 | complete |
| `DRV-A09` | 157 | `finance` | W27 | complete |
| `DRV-A10` | 158 | `finance` | W27 | complete |
| `DRV-A11` | 159 | `finance` | W27 | complete |
| `DRV-A12` | 160 | `finance` | W27 | complete |
| `DRV-A13` | 161 | `finance` | W27 | complete |
| `DRV-L01` | 123 | `delivery` | W26 | complete |
| `DRV-L02` | 124 | `delivery` | W26 | complete |
| `DRV-L03` | 125 | `delivery` | W26 | complete |
| `DRV-L04` | 126 | `delivery` | W26 | complete |
| `DRV-L05` | 127 | `delivery` | W26 | **conflict** |
| `DRV-L06` | 128 | `delivery` | W26 | complete |
| `DRV-L07` | 129 | `delivery` | W26 | **conflict** |
| `DRV-L08` | 130 | `delivery` | W26 | complete |
| `DRV-L09` | 131 | `delivery` | W26 | complete |
| `DRV-L10` | 132 | `delivery` | W26 | complete |
| `DRV-L11` | 133 | `delivery` | W26 | complete |
| `DRV-L12` | 134 | `delivery` | W26 | complete |
| `DRV-L13` | 135 | `delivery` | W26 | complete |
| `DRV-L14` | 136 | `delivery` | W26 | complete |
| `DRV-L15` | 137 | `delivery` | W26 | complete |
| `DRV-L16` | 138 | `delivery` | W26 | complete |
| `DRV-L17` | 139 | `delivery` | W26 | complete |
| `DRV-L18` | 140 | `delivery` | W26 | complete |
| `DRV-L19` | 141 | `delivery` | W26 | complete |
| `DRV-L20` | 142 | `delivery` | W26 | complete |
| `DRV-L21` | 143 | `delivery` | W26 | complete |
| `DRV-P01` | 92 | `pickup` | W19-C | complete |
| `DRV-P02` | 93 | `pickup` | W19-C | complete |
| `DRV-P03` | 94 | `pickup` | W19-C | complete |
| `DRV-P04` | 95 | `pickup` | W19-C | complete |
| `DRV-P05` | 96 | `pickup` | W19-C | complete |
| `DRV-P06` | 97 | `pickup` | W19-C | complete |
| `DRV-P07` | 98 | `pickup` | W19-C | complete |
| `DRV-P08` | 99 | `pickup` | W19-C | complete |
| `DRV-P09` | 100 | `pickup` | W19-C | complete |
| `DRV-P10` | 101 | `pickup` | W19-C | complete |
| `DRV-P11` | 102 | `pickup` | W19-C | complete |
| `DRV-P12` | 103 | `pickup` | W19-C | complete |
| `DRV-P13` | 104 | `pickup` | W19-C | complete |
| `DRV-P14` | 105 | `pickup` | W19-C | complete |
| `DRV-P15` | 106 | `pickup` | W19-C | complete |
| `DRV-P16` | 107 | `pickup` | W19-C | complete |
| `DRV-P17` | 108 | `pickup` | W19-C | complete |
| `DRV-P18` | 109 | `pickup` | W19-C | complete |
| `DRV-P19` | 110 | `pickup` | W19-C | complete |
| `DRV-P20` | 111 | `pickup` | W19-C | complete |
| `DRV-P21` | 112 | `pickup` | W19-C | complete |
| `DRV-P22` | 113 | `pickup` | W19-C | complete |
| `DRV-P23` | 114 | `pickup` | W19-C | complete |
| `DRV-P24` | 115 | `pickup` | W19-C | complete |
| `DRV-P25` | 116 | `claims` | W28 | complete |
| `DRV-P26` | 117 | `pickup` | W19-C | complete |
| `MER-01` | 44 | `merchant` | W21 | complete |
| `MER-02` | 45 | `merchant` | W21 | **blocked** |
| `MER-03` | 46 | `merchant` | W21 | complete |
| `MER-04` | 47 | `merchant` | W21 | complete |
| `MER-05` | 48 | `ordering` | W22 | complete |
| `MER-06` | 49 | `customer` | W20-B | complete |
| `MER-07` | 50 | `customer` | W20-B | complete |
| `MER-08` | 51 | `ordering` | W22 | complete |
| `MER-09` | 52 | `ordering` | W22 | complete |
| `MER-10` | 53 | `merchant` | W21 | complete |
| `MER-11` | 54 | `merchant` | W21 | complete |
| `MER-12` | 55 | `merchant` | W21 | complete |
| `MER-13` | 56 | `ordering` | W22 | complete |
| `MER-14` | 57 | `merchant` | W21 | complete |
| `MER-15` | 58 | `ordering` | W22 | complete |
| `MER-16` | 59 | `merchant` | W21 | complete |
| `MER-17` | 60 | `merchant` | W21 | complete |
| `MER-18` | 61 | `ordering` | W22 | complete |
| `MER-19` | 62 | `ordering` | W22 | complete |
| `MER-20` | 63 | `merchant` | W21 | complete |
| `MER-21` | 64 | `merchant` | W21 | complete |
| `MER-22` | 65 | `ordering` | W22 | complete |
| `MER-23` | 66 | `ordering` | W22 | complete |
| `NTF-01` | 196 | `notification` | W24 | complete |
| `NTF-02` | 197 | `notification` | W24 | complete |
| `NTF-03` | 198 | `notification` | W24 | complete |
| `NTF-04` | 199 | `notification` | W24 | complete |
| `NTF-05` | 200 | `notification` | W24 | complete |
| `NTF-06` | 201 | `notification` | W24 | complete |
| `NTF-07` | 202 | `notification` | W24 | complete |
| `NTF-08` | 203 | `notification` | W24 | complete |
| `NTF-09` | 204 | `notification` | W24 | complete |
| `NTF-10` | 205 | `notification` | W24 | complete |
| `OPS-01` | 211 | `hub` | W23 | complete |
| `OPS-02` | 212 | `hub` | W23 | complete |
| `OPS-03` | 213 | `tracking` | W17 | planned |
| `OPS-04` | 214 | `finance` | W27 | complete |
| `OPS-05` | 215 | `hub` | W23 | complete |
| `OPS-06` | 216 | `claims` | W28 | complete |
| `OPS-07` | 217 | `claims` | W28 | complete |
| `OPS-08` | 218 | `delivery` | W26 | complete |
| `OPS-09` | 219 | `workforce` | W25 | complete |
| `OPS-10` | 220 | `hub` | W23 | complete |
| `OPS-11` | 221 | `audit` | W17 | complete |
| `PAY-01` | 167 | `finance` | W27 | complete |
| `PAY-02` | 168 | `finance` | W27 | complete |
| `PAY-03` | 169 | `finance` | W27 | complete |
| `PAY-04` | 170 | `finance` | W27 | complete |
| `PAY-05` | 171 | `finance` | W27 | complete |
| `PAY-06` | 172 | `finance` | W27 | complete |
| `PAY-07` | 173 | `finance` | W27 | **blocked** |
| `PAY-08` | 174 | `finance` | W27 | **blocked** |
| `PAY-09` | 175 | `finance` | W27 | complete |
| `PAY-10` | 176 | `finance` | W27 | complete |
| `PAY-11` | 177 | `finance` | W27 | complete |
| `SEC-01` | 10 | `identity` | W20-A | complete |
| `SEC-02` | 11 | `identity` | W20-A | complete |
| `SEC-03` | 12 | `workforce` | W25 | complete |
| `SEC-04` | 13 | `customer` | W20-B | complete |
| `SEC-05` | 14 | `identity` | W20-A | planned |
| `SEC-06` | 15 | `pickup` | W19-C | complete |
| `SEC-07` | 16 | `claims` | W28 | complete |
| `SEC-08` | 17 | `delivery` | W26 | complete |
| `SEC-09` | 18 | `audit` | W17 | complete |
| `SHP-01` | 72 | `ordering` | W22 | complete |
| `SHP-02` | 73 | `ordering` | W22 | **blocked** |
| `SHP-03` | 74 | `shipment` | W18 | complete |
| `SHP-04` | 75 | `shipment` | W18 | planned |
| `SHP-05` | 76 | `hub` | W23 | complete |
| `SHP-06` | 77 | `hub` | W23 | complete |
| `SHP-07` | 78 | `hub` | W23 | complete |
| `SHP-08` | 79 | `hub` | W23 | complete |
| `SHP-09` | 80 | `shipment` | W18 | planned |
| `SHP-10` | 81 | `ordering` | W22 | complete |
| `SHP-11` | 82 | `ordering` | W22 | complete |
| `SHP-12` | 83 | `ordering` | W22 | complete |
| `SHP-13` | 84 | `hub` | W23 | complete |
<!-- /generated:requirement-rows -->

## The seven that remain

Five v6.3 Appendix A executive Open Items and two source contradictions. Both lists
are fixed sets in `scripts/quality/verify_requirement_accounting.py`, so neither can
grow without a deliberate edit to that file.

| ID | Kind | What is blocked | What is implemented around it |
|---|---|---|---|
| `MER-02` | Open Item | Submitting a merchant application | The whole application, store and policy model; submission fails closed because `MERCHANT_APPLICATION_REQUIRED_ATTRIBUTES` has no default |
| `SHP-02` | Open Item | Showing the customer an exact delivery goal | Tracking, statuses and the public tracking view |
| `PAY-07` | Open Item | `POST /finance/payouts/{id}/pay` | Request, method, destination, balance check, approval, rejection and both facts |
| `PAY-08` | Open Item | `POST /finance/refusal-charges/waive` | The charge itself — v6.3 p.38/p.39 settle it — posted to the ledger and tested |
| `CLM-08` | Open Item | Asking whether a parcel is high value | Everything else in `claims`: filing, evidence, the custody boundary, review, approval, rejection, the support thread and both operations views. `CLAIMS_HIGH_VALUE_THRESHOLD` has no default and `is_high_value` refuses |
| `DRV-L05` | Source conflict | Verifying a delivery code | The whole doorstep: the ten-minute wait, the ID fallback, the seal, the inspection, payment and all three outcomes |
| `DRV-L07` | Source conflict | Retaining an ID photograph | The named-receiver ID verification itself; no column exists to retain one in |

---

Regenerate the checks with:

```bash
uv run python scripts/quality/verify_requirement_traceability.py
uv run python scripts/quality/verify_requirement_accounting.py
```

The two tables above are generated from `requirement-accounting.yaml`. The verifier fails
if they disagree with it; rewrite them with:

```bash
uv run python scripts/quality/verify_requirement_traceability.py --write
```
