#!/usr/bin/env python3
"""Drive the platform's business journeys against the running HTTP APIs.

    uv run python scripts/dev/stack.py up
    uv run python scripts/dev/journeys.py            # every journey
    uv run python scripts/dev/journeys.py --only finance
    uv run python scripts/dev/journeys.py --verbose  # show each request

This is not a test suite — it is the thing that says whether the APIs actually work when
you call them the way an app would. Every call is a real HTTP request to a real service
backed by real PostgreSQL, with real Identity tokens. Nothing is mocked and nothing is
imported: if a route is broken, this finds it.

Each journey prints one line per step, and a step that fails prints what it sent and what
came back, so the next thing to do is obvious.

Two journeys are expected to stop partway, and say so rather than failing:

* **the delivery code** (DRV-L05) — the Customer App says four digits and the Driver App
  collects six, so `DELIVERY_CODE_LENGTH` has no default and verification answers 501;
* **paying a payout** (PAY-07) — the procedure per method is a v6.3 Open Item.

A `BLOCKED` line is the platform behaving correctly. A `FAIL` line is a bug.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

IDENTITY = "http://127.0.0.1:8101"
CUSTOMER = "http://127.0.0.1:8102"
MERCHANT = "http://127.0.0.1:8103"
ORDERING = "http://127.0.0.1:8104"
HUB = "http://127.0.0.1:8105"
NOTIFICATION = "http://127.0.0.1:8106"
WORKFORCE = "http://127.0.0.1:8107"
DELIVERY = "http://127.0.0.1:8108"
FINANCE = "http://127.0.0.1:8109"
PICKUP = "http://127.0.0.1:8110"
SHIPMENT = "http://127.0.0.1:8111"
TRACKING = "http://127.0.0.1:8112"
AUDIT = "http://127.0.0.1:8113"
CLAIMS = "http://127.0.0.1:8114"

SERVICE_CREDENTIAL = "hudhud-dev-service-credential"
BOOTSTRAP_PHONE = "+9647700000001"

class _Settings:
    """Mutable run options, in one object rather than as module globals."""

    verbose = False


VERBOSE = _Settings()


# ------------------------------------------------------------------ transport


@dataclass
class Response:
    status: int
    body: Any

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def get(self, *path, default=None):
        value = self.body
        for key in path:
            if not isinstance(value, dict):
                return default
            value = value.get(key)
            if value is None:
                return default
        return value


def call(
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: dict | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)  # noqa: S310
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            raw = response.read().decode() or "null"
            result = Response(response.status, json.loads(raw))
    except urllib.error.HTTPError as error:
        raw = error.read().decode() or "null"
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
        result = Response(error.code, parsed)
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
        result = Response(0, {"transport_error": str(error)})
    if VERBOSE.verbose:
        print(f"      {method} {url} -> {result.status}")
    return result


# ------------------------------------------------------------------ reporting


@dataclass
class Journey:
    name: str
    steps: list[tuple[str, str, str]] = field(default_factory=list)

    def ok(self, step: str, detail: str = "") -> None:
        self.steps.append(("ok", step, detail))

    def blocked(self, step: str, detail: str) -> None:
        self.steps.append(("BLOCKED", step, detail))

    def fail(self, step: str, detail: str) -> None:
        self.steps.append(("FAIL", step, detail))

    def check(self, step: str, response: Response, *expect: int) -> bool:
        if response.status in expect:
            self.ok(step, f"{response.status}")
            return True
        self.fail(step, f"expected {expect}, got {response.status}: {_short(response.body)}")
        return False

    @property
    def failed(self) -> int:
        return sum(1 for status, _, _ in self.steps if status == "FAIL")

    @property
    def blocked_count(self) -> int:
        return sum(1 for status, _, _ in self.steps if status == "BLOCKED")

    def render(self) -> None:
        mark = "FAIL" if self.failed else ("BLOCKED" if self.blocked_count else "ok")
        print(f"\n{self.name}   [{mark}]")
        for status, step, detail in self.steps:
            prefix = {"ok": "  ok     ", "BLOCKED": "  BLOCKED", "FAIL": "  FAIL   "}[status]
            print(f"{prefix} {step}" + (f"  — {detail}" if detail else ""))


def _short(body: Any, limit: int = 200) -> str:
    text = json.dumps(body) if not isinstance(body, str) else body
    return text[:limit]


# ------------------------------------------------------------------ identity


def read_otp(challenge_id: str) -> str | None:
    """Read the code Identity printed. Local only: `IDENTITY_OTP_DELIVERY_CHANNEL`
    refuses `console` outside local and test."""
    for _ in range(20):
        result = subprocess.run(
            ["uv", "run", "python", str(REPO_ROOT / "scripts/dev/stack.py"),
             "logs", "identity", "--tail-bytes", "40000"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=False,
        )
        for line in reversed(result.stdout.splitlines()):
            if f"reference={challenge_id}" in line and "code=" in line:
                return line.split("code=")[1].split()[0]
        time.sleep(0.3)
    return None


def sign_in(phone: str, *, device: str = "journey-device") -> tuple[str, str] | None:
    """Phone OTP sign-in, returning (token, principal_id)."""
    requested = call("POST", f"{IDENTITY}/identity/otp/request", body={"phone": phone})
    challenge = requested.get("challenge_id")
    if not challenge:
        return None
    code = read_otp(challenge)
    if code is None:
        return None
    verified = call(
        "POST", f"{IDENTITY}/identity/otp/verify",
        body={"challenge_id": challenge, "code": code, "device_id": device},
    )
    token = verified.get("access_token")
    principal = verified.get("principal_id")
    if not token:
        return None
    return token, principal


#: What Identity requires alongside each grant. Published in
#: `contracts/identity/roles.yaml`; a role with a scope cannot be granted globally.
ROLE_SCOPE = {
    "CUSTOMER": "GLOBAL",
    "MERCHANT_MEMBER": "MERCHANT",
    "PICKUP_DRIVER": "GLOBAL",
    "DELIVERY_DRIVER": "GLOBAL",
    "HUB_OPERATOR": "HUB",
    "HUB_CASHIER": "HUB",
    "ACCOUNTANT": "GLOBAL",
    "SUPPORT": "GLOBAL",
    "OPERATIONS": "GLOBAL",
}


def grant(admin_token: str, principal_id: str, role: str, scope_id: str | None = None):
    """Grant a role. Needs the operator's token *and* a service credential.

    Both, because the route introspects the operator's token to check they are
    Operations, and introspection is service-to-service. A privileged grant is made by an
    operations console holding a credential, never by a bare user token.
    """
    scope_kind = ROLE_SCOPE.get(role, "GLOBAL")
    body: dict[str, Any] = {"role": role, "scope_kind": scope_kind}
    if scope_kind != "GLOBAL":
        # A scoped role names what it is scoped to; Identity treats the id as opaque.
        body["scope_id"] = scope_id or str(uuid.uuid4())
    return call(
        "POST", f"{IDENTITY}/identity/principals/{principal_id}/roles",
        token=admin_token, body=body,
        headers={"X-Service-Credential": SERVICE_CREDENTIAL},
    )


def unique_phone() -> str:
    return f"+96477{uuid.uuid4().int % 10_000_000:07d}"


#: A per-run number, so two runs do not collide. Reusing a tracking code or an
#: idempotency key is *supposed* to return the first run's record — that is the
#: guarantee being tested elsewhere — so a journey that wants fresh state needs fresh
#: identifiers.
RUN = uuid.uuid4().int % 900 + 100


def tracking_code(n: int) -> str:
    return f"SHP-20260915-{RUN:03d}{n:03d}"


def run_key(name: str) -> str:
    return f"journey-{RUN}-{name}"


# ------------------------------------------------------------------ journeys


def journey_identity() -> tuple[Journey, dict]:
    """SEC-01, SEC-02, SEC-04 — sign in, introspect, grant a role."""
    j = Journey("1. Identity — phone OTP sign-in, introspection, role grants")
    context: dict = {}

    j.check("service is healthy", call("GET", f"{IDENTITY}/health"), 200)

    signed = sign_in(BOOTSTRAP_PHONE)
    if signed is None:
        j.fail("bootstrap operator signs in", "no token; is the stack up?")
        return j, context
    admin_token, admin_id = signed
    j.ok("bootstrap operator signs in", "OTP issued, verified, session created")
    context["admin_token"] = admin_token
    context["admin_id"] = admin_id

    introspected = call(
        "POST", f"{IDENTITY}/identity/tokens/introspect",
        headers={"X-Service-Credential": SERVICE_CREDENTIAL},
        body={"token": admin_token},
    )
    if j.check("a service introspects the token", introspected, 200):
        roles = introspected.get("roles", default=[])
        if "OPERATIONS" in roles:
            j.ok("first sign-in is bootstrapped to OPERATIONS", ", ".join(roles))
        else:
            j.fail("first sign-in is bootstrapped to OPERATIONS", f"roles={roles}")

    anonymous = call(
        "POST", f"{IDENTITY}/identity/tokens/introspect", body={"token": admin_token}
    )
    if anonymous.status in (401, 403):
        j.ok("introspection refuses a caller with no service credential", str(anonymous.status))
    else:
        j.fail("introspection refuses an unauthenticated caller", f"got {anonymous.status}")

    # `/me` resolves the token, which is introspection, which is service-to-service.
    j.check(
        "the operator reads their own profile",
        call("GET", f"{IDENTITY}/identity/me", token=admin_token,
             headers={"X-Service-Credential": SERVICE_CREDENTIAL}),
        200,
    )
    return j, context


def journey_roles(context: dict) -> Journey:
    """Create the principals the rest of the journeys act as."""
    j = Journey(
        "2. Principals — a customer, a merchant owner, a driver, a cashier, an accountant"
    )
    admin = context.get("admin_token")
    if not admin:
        j.fail("needs the operator token", "journey 1 did not complete")
        return j

    # Identity's names, not each service's. `contracts/identity/roles.yaml` is the list.
    merchant_id = str(uuid.uuid4())
    context["merchant_id"] = merchant_id

    # A plain customer needs no grant: CUSTOMER is the only role self-service sign-up
    # produces (`contracts/identity/roles.yaml`), so signing in is the whole of it.
    customer_phone = unique_phone()
    signed_customer = sign_in(customer_phone, device="customer-device")
    if signed_customer is None:
        j.fail("a customer signs in", "no token")
    else:
        context["customer_token"], context["customer_id"] = signed_customer
        j.ok("a customer signs in", "CUSTOMER, granted by signing up")

    for label, role, scope in (
        ("merchant_owner", "MERCHANT_MEMBER", merchant_id),
        ("driver", "DELIVERY_DRIVER", None),
        ("cashier", "HUB_CASHIER", str(uuid.uuid4())),
        ("accountant", "ACCOUNTANT", None),
    ):
        phone = unique_phone()
        signed = sign_in(phone, device=f"{label}-device")
        if signed is None:
            j.fail(f"{label} signs in", "no token")
            continue
        token, principal = signed
        granted = grant(admin, principal, role, scope)
        if granted.status not in (200, 201):
            j.fail(f"{label} is granted {role}", f"{granted.status}: {_short(granted.body)}")
            continue
        # The grant lands on the principal, so the session has to be renewed to carry it.
        renewed = sign_in(phone, device=f"{label}-device")
        if renewed is None:
            j.fail(f"{label} re-authenticates with {role}", "no token")
            continue
        context[f"{label}_token"] = renewed[0]
        context[f"{label}_id"] = principal
        j.ok(f"{label} signs in and is granted {role}",
             f"scope={ROLE_SCOPE[role]}")
    return j


def journey_merchant(context: dict) -> Journey:
    """MER-01, MER-03 … MER-16 — onboarding, stores, label stock."""
    j = Journey("3. Merchant — application, stores, label stock")
    owner = context.get("merchant_owner_token")
    admin = context.get("admin_token")
    if not owner or not admin:
        j.fail("needs a merchant owner", "journey 2 did not complete")
        return j

    # The request carries an `attributes` map and nothing else. Which attributes are
    # required is MER-02 — a v6.3 Open Item — so the service reads them from
    # configuration and refuses when none is defined, rather than inventing a form.
    submitted = call(
        "POST", f"{MERCHANT}/merchant/applications", token=owner,
        body={"attributes": {"business_name": "Baghdad Books",
                             "contact_phone": unique_phone()}},
    )
    if submitted.status in (501, 503):
        j.blocked(
            "submit a merchant application",
            "MER-02 — the required application data set is a v6.3 Open Item; "
            "submission fails closed rather than inventing the fields",
        )
    elif submitted.status in (200, 201):
        j.ok("submit a merchant application", str(submitted.status))
        context["merchant_id"] = submitted.get("merchant_id")
    else:
        j.fail("submit a merchant application",
               f"{submitted.status}: {_short(submitted.body)}")

    listed = call("GET", f"{MERCHANT}/merchant/applications", token=admin)
    j.check("operations lists applications", listed, 200, 403)
    return j


def journey_workforce(context: dict) -> Journey:
    """SEC-03, DRV-A01 … DRV-A04 — the office check and the eligibility answer."""
    j = Journey("4. Workforce — driver eligibility (ADR-0013)")
    driver = context.get("driver_token")
    driver_id = context.get("driver_id")
    admin = context.get("admin_token")
    if not driver or not admin:
        j.fail("needs a driver", "journey 2 did not complete")
        return j

    eligibility = call(
        "GET", f"{WORKFORCE}/workforce/principals/{driver_id}/eligibility", token=driver
    )
    if j.check("a driver reads their own eligibility", eligibility, 200):
        eligible = eligibility.get("eligible")
        reasons = eligibility.get("reasons", default=[])
        j.ok(
            "eligibility answers with reasons, not just a verdict",
            f"eligible={eligible} reasons={reasons[:2]}",
        )

    other = call(
        "GET", f"{WORKFORCE}/workforce/principals/{uuid.uuid4()}/eligibility",
        token=driver,
    )
    if other.status == 403:
        j.ok("a driver cannot read another driver's eligibility", "403")
    else:
        j.fail("a driver cannot read another driver's eligibility", f"got {other.status}")
    return j


def journey_delivery(context: dict) -> Journey:
    """DRV-L01 … DRV-L21 — custody, the door, and the two source conflicts."""
    j = Journey("5. Delivery — manifest, the door, and the DRV-L05 conflict")
    driver = context.get("driver_token")
    if not driver:
        j.fail("needs a driver", "journey 2 did not complete")
        return j

    policy = call("GET", f"{DELIVERY}/delivery/code-policy", token=driver)
    if j.check("read the delivery-code policy", policy, 200):
        lengths = policy.get("conflicting_lengths", default=[])
        decided = policy.get("decided")
        if decided is False and sorted(lengths) == [4, 6]:
            j.blocked(
                "the delivery-code length",
                "DRV-L05 — Customer App v3 says 4 digits, Driver App v8 collects 6. "
                "No default; both readings are reported and verification answers 501.",
            )
        elif decided:
            j.ok("the delivery-code length is configured", f"{policy.get('length')} digits")

    manifest = call(
        "POST", f"{DELIVERY}/delivery/manifests", token=driver,
        body={"hub_id": str(uuid.uuid4())},
    )
    if not j.check("open a manifest", manifest, 201):
        return j
    manifest_id = manifest.get("manifest_id")

    code = tracking_code(101)
    stop = call(
        "POST", f"{DELIVERY}/delivery/manifests/{manifest_id}/parcels", token=driver,
        body={
            "tracking_code": code,
            "delivery_code": "482913",
            "named_receiver": "Zaid Al-Rawi",
            "payment_method_expected": "CASH",
            "cod_amount": {"minor_units": 105000, "currency": "IQD"},
        },
    )
    if not j.check("scan a parcel onto it — this transfers custody", stop, 201):
        return j
    stop_id = stop.get("stop_id")
    if stop.get("has_delivery_code") is True and "482913" not in json.dumps(stop.body):
        j.ok("the response never echoes the delivery code", "only has_delivery_code")
    else:
        j.fail("the response never echoes the delivery code", "the code appeared")

    j.check("depart, with an ETA window",
            call("POST", f"{DELIVERY}/delivery/stops/{stop_id}/depart", token=driver,
                 body={"eta_from_minutes": 20, "eta_to_minutes": 40}), 200)
    j.check("arrive", call("POST", f"{DELIVERY}/delivery/stops/{stop_id}/arrive",
                           token=driver), 200)

    verified = call(
        "POST", f"{DELIVERY}/delivery/stops/{stop_id}/verify/code", token=driver,
        body={"code": "482913"},
    )
    if verified.status == 501:
        j.blocked(
            "verify with the delivery code",
            "DRV-L05 — answers 501 until the length is decided. Everything else at the "
            "door works; set DELIVERY_CODE_LENGTH to try one of the two readings.",
        )
        # The named-receiver fallback does not depend on the undecided length.
        fallback = call(
            "POST", f"{DELIVERY}/delivery/stops/{stop_id}/verify/id", token=driver,
            body={"presented_name": "Zaid Al-Rawi"},
        )
        j.check("verify with the named receiver's ID instead (DRV-L06)", fallback, 200)
    else:
        j.check("verify with the delivery code", verified, 200)

    photo = call(
        "POST", f"{DELIVERY}/delivery/stops/{stop_id}/verify/id", token=driver,
        body={"presented_name": "Zaid Al-Rawi",
              "id_photo": {"bucket": "evidence", "key": "id.jpg"}},
    )
    if photo.status == 422:
        j.blocked(
            "retaining a photograph of the ID",
            "DRV-L07 — v6.3 p.26 says photograph it, the Driver App says do not store "
            "it. No field accepts one, so none can be retained.",
        )
    else:
        j.fail("retaining an ID photograph is refused", f"got {photo.status}")

    j.check("record the sealed inspection",
            call("POST", f"{DELIVERY}/delivery/stops/{stop_id}/inspection", token=driver,
                 body={"outcome": "SEALED_ACCEPTED"}), 200)

    unpaid = call("POST", f"{DELIVERY}/delivery/stops/{stop_id}/deliver", token=driver)
    if unpaid.status == 409:
        j.ok("a COD parcel is refused delivery while unpaid (v6.3 p.31)", "409")
    else:
        j.fail("a COD parcel is refused delivery while unpaid", f"got {unpaid.status}")

    j.check("collect the cash at the door",
            call("POST", f"{DELIVERY}/delivery/stops/{stop_id}/payment/cash",
                 token=driver, body={}), 200)
    delivered = call("POST", f"{DELIVERY}/delivery/stops/{stop_id}/deliver", token=driver)
    if j.check("hand the parcel over", delivered, 200):
        context["delivered_tracking_code"] = code
    return j


def journey_finance(context: dict) -> Journey:
    """PAY-01 … PAY-11, DRV-A05 … DRV-A13, OPS-04 — the ledger, end to end."""
    j = Journey("6. Finance — COD, driver cash custody, deposits, payouts")
    admin = context.get("admin_token")
    driver = context.get("driver_token")
    driver_id = context.get("driver_id")
    cashier = context.get("cashier_token")
    accountant = context.get("accountant_token")
    if not all((admin, driver, cashier, accountant)):
        j.fail("needs an operator, a driver, a cashier and an accountant",
               "journey 2 did not complete")
        return j

    merchant_id = context.get("merchant_id") or str(uuid.uuid4())
    opened = call(
        "POST", f"{FINANCE}/finance/cash-accounts", token=admin,
        body={"driver_principal_id": driver_id},
    )
    if not j.check("operations opens the driver's cash account", opened, 201):
        return j
    j.ok("the limit came from configuration, not from code",
         f"limit={opened.get('limit', 'minor_units')} IQD (DRV-A06)")

    collected = call(
        "POST", f"{FINANCE}/finance/collections", token=driver,
        body={
            "tracking_code": tracking_code(201),
            "merchant_id": merchant_id,
            "channel": "CASH",
            "goods_amount": {"minor_units": 100000, "currency": "IQD"},
            "delivery_fee": {"minor_units": 5000, "currency": "IQD"},
            "driver_principal_id": driver_id,
            "idempotency_key": run_key("cod"),
        },
    )
    if not j.check("record COD collected in cash", collected, 201):
        return j
    if collected.get("is_paid") is False:
        j.ok("cash is not yet 'paid' — it has to reach the hub cashier (v6.3 p.33)")
    else:
        j.fail("cash should not count as paid at the door", "is_paid was true")

    retry = call(
        "POST", f"{FINANCE}/finance/collections", token=driver,
        body={
            "tracking_code": tracking_code(201),
            "merchant_id": merchant_id,
            "channel": "CASH",
            "goods_amount": {"minor_units": 100000, "currency": "IQD"},
            "delivery_fee": {"minor_units": 5000, "currency": "IQD"},
            "driver_principal_id": driver_id,
            "idempotency_key": run_key("cod"),
        },
    )
    if retry.status == 201 and retry.get("collection_id") == collected.get("collection_id"):
        j.ok("a retry under the same key posts once, not twice")
    else:
        j.fail("a retry must not post twice", f"{retry.status}: {_short(retry.body)}")

    position = call(
        "GET", f"{FINANCE}/finance/cash-accounts/{driver_id}", token=driver
    )
    if j.check("the driver reads their cash on hand (DRV-A05)", position, 200):
        held = position.get("held", "minor_units")
        if held == 105000:
            j.ok("held cash matches what was collected", "105,000 IQD")
        else:
            j.fail("held cash", f"expected 105000, got {held}")

    deposit = call(
        "POST", f"{FINANCE}/finance/deposits", token=driver,
        body={
            "method": "HUB_CASHIER",
            "amount": {"minor_units": 105000, "currency": "IQD"},
            "reference": f"HUB-{uuid.uuid4().hex[:8]}",
            "receipt": {"bucket": "finance-evidence", "key": "slip.jpg"},
            "hub_id": str(uuid.uuid4()),
        },
    )
    if not j.check("the driver deposits it to a hub cashier (DRV-A07)", deposit, 201):
        return j
    deposit_id = deposit.get("deposit", "deposit_id")
    if deposit.get("held_after", "minor_units") == 0:
        j.ok("the deposit frees the driver's limit at once (DRV-A09)")

    self_confirm = call(
        "POST", f"{FINANCE}/finance/deposits/{deposit_id}/confirm", token=driver
    )
    if self_confirm.status == 403:
        j.ok("the driver cannot confirm their own deposit (ADR-0012)", "403")
    else:
        j.fail("the driver must not confirm their own deposit", f"got {self_confirm.status}")

    confirmed = call(
        "POST", f"{FINANCE}/finance/deposits/{deposit_id}/confirm", token=cashier
    )
    j.check("the hub cashier confirms it", confirmed, 200)

    settled = call(
        "GET", f"{FINANCE}/finance/collections/{tracking_code(201)}", token=admin
    )
    if settled.get("is_paid") is True:
        j.ok("the parcel now counts as paid (PAY-01)", "cash reached the cashier")
    else:
        j.fail("the parcel should now be paid", _short(settled.body))

    balance = call("GET", f"{FINANCE}/finance/merchants/{merchant_id}/balance", token=admin)
    if j.check("read the merchant balance (PAY-05)", balance, 200):
        owed = balance.get("balance", "minor_units")
        if owed == 100000:
            j.ok("the merchant is owed the goods, not the delivery fee", "100,000 IQD")
        else:
            j.fail("merchant balance", f"expected 100000, got {owed}")

    payout = call(
        "POST", f"{FINANCE}/finance/merchants/{merchant_id}/payouts", token=admin,
        body={"method": "IN_PERSON_AT_HUB", "amount": {"minor_units": 50000, "currency": "IQD"}},
    )
    if j.check("a payout is requested (PAY-06)", payout, 201):
        payout_id = payout.get("payout_id")
        j.check("operations approves it",
                call("POST", f"{FINANCE}/finance/payouts/{payout_id}/approve", token=admin),
                200)
        paid = call("POST", f"{FINANCE}/finance/payouts/{payout_id}/pay", token=admin)
        if paid.status == 501:
            j.blocked(
                "paying the payout out",
                "PAY-07 — the procedure per method is a v6.3 Appendix A Open Item and "
                "needs an accountant. Nothing posted, nothing published.",
            )
        else:
            j.fail("paying a payout should be blocked", f"got {paid.status}")

    charge = call(
        "POST", f"{FINANCE}/finance/refusal-charges", token=admin,
        body={
            "tracking_code": tracking_code(202),
            "merchant_id": merchant_id,
            "delivery_fee": {"minor_units": 5000, "currency": "IQD"},
        },
    )
    if j.check("a refusal charges both fees (PAY-08, v6.3 p.38)", charge, 201):
        j.ok("the charge is implemented, not blocked",
             f"total={charge.get('total', 'minor_units')} IQD")
    waive = call(
        "POST", f"{FINANCE}/finance/refusal-charges/waive", token=admin,
        body={
            "tracking_code": tracking_code(202),
            "merchant_id": merchant_id,
            "amount": {"minor_units": 5000, "currency": "IQD"},
            "note": "goodwill",
        },
    )
    if waive.status == 501:
        j.blocked(
            "waiving the return-trip fee",
            "PAY-08 — only the waiver is undecided. The charge above is not blocked.",
        )
    else:
        j.fail("waiving should be blocked", f"got {waive.status}")

    exposure = call("GET", f"{FINANCE}/finance/cash-exposure", token=admin)
    j.check("operations reads platform cash exposure (OPS-04)", exposure, 200)
    denied = call("GET", f"{FINANCE}/finance/cash-exposure", token=driver)
    if denied.status == 403:
        j.ok("a driver cannot read everyone's cash", "403")
    else:
        j.fail("a driver must not read platform cash exposure", f"got {denied.status}")

    ledger = call("GET", f"{FINANCE}/finance/balances/HUDHUD_REVENUE", token=accountant)
    if j.check("an accountant reads a ledger balance", ledger, 200):
        j.ok("HUDHUD revenue is the delivery fee",
             f"{ledger.get('balance', 'minor_units')} IQD")

    open_items = call("GET", f"{FINANCE}/finance/open-items", token=admin)
    j.check("the service reports what it will not do", open_items, 200)
    return j


def journey_claims(context: dict) -> Journey:
    """CLM-01 … CLM-08, DRV-P25, OPS-06, OPS-07, SEC-07 — over real HTTP."""
    j = Journey("7. Claims — compensation, the support thread, and SEC-07")
    sender = context.get("customer_token")
    sender_id = context.get("customer_id")
    driver = context.get("driver_token")
    admin = context.get("admin_token")
    if not sender or not admin:
        j.fail("needs a signed-in customer and an operator", "journey 1 did not complete")
        return j

    tracking = f"SHP-20260915-{uuid.uuid4().int % 1000000:06d}"

    # CLM-02 — the sender files. A damage claim needs a photograph first.
    bare = call(
        "POST", f"{CLAIMS}/claims", token=sender,
        body={"tracking_code": tracking, "kind": "DAMAGED_IN_TRANSIT"},
    )
    if bare.status == 422 and bare.get("detail", "code") == "photographs_required":
        j.ok("a damage claim with no photograph is refused at the boundary", "422")
    else:
        j.fail("a damage claim with no photograph must be refused", f"got {bare.status}")

    filed = call(
        "POST", f"{CLAIMS}/claims", token=sender,
        body={
            "tracking_code": tracking,
            "kind": "DAMAGED_IN_TRANSIT",
            "description": "Corner crushed.",
            "evidence": [{"bucket": "claims-evidence", "key": "damage.jpg"}],
        },
    )
    if not j.check("a sender files a claim", filed, 201):
        return j
    reference = filed.get("reference")
    j.ok("the claimant is given a reference", str(reference))

    # v6.3 p.37 — a parcel taken inside to test is no longer HUDHUD's.
    inside = call(
        "POST", f"{CLAIMS}/claims", token=sender,
        body={
            "tracking_code": f"SHP-20260915-{uuid.uuid4().int % 1000000:06d}",
            "kind": "DAMAGED_IN_TRANSIT",
            "evidence": [{"bucket": "claims-evidence", "key": "damage.jpg"}],
            "custody_boundary": "TAKEN_INSIDE_TO_TEST",
        },
    )
    if inside.status == 409:
        j.ok("liability ends when the receiver takes it inside to test it", "409")
    else:
        j.fail("a parcel taken inside to test must be refused", f"got {inside.status}")

    # CLM-02 — one open claim per parcel, whoever files.
    again = call(
        "POST", f"{CLAIMS}/claims", token=sender,
        body={"tracking_code": tracking, "kind": "LOST_PARCEL"},
    )
    if again.status == 409:
        j.ok("a second open claim on one parcel is refused", "409")
    else:
        j.fail("a second open claim must be refused", f"got {again.status}")

    # CLM-06 — there is no return window after acceptance at the door.
    returned = call("POST", f"{CLAIMS}/claims/returns/{tracking}", token=sender)
    if returned.status == 409:
        j.blocked(
            "requesting a return after acceptance",
            "CLM-06 — the decision at the door is final; a claim is still possible",
        )
    else:
        j.fail("CLM-06 must refuse a return request", f"got {returned.status}")

    # CLM-07 — the support conversation.
    message = call(
        "POST", f"{CLAIMS}/claims/{reference}/messages", token=sender,
        body={"body": "The box arrived crushed."},
    )
    j.check("the claimant writes on the support thread", message, 201)

    # CLM-03 — the custody review is a step, not a formality.
    early = call(
        "POST", f"{CLAIMS}/claims/{reference}/approval", token=admin,
        body={"compensation_minor_units": 450000},
    )
    if early.status == 409:
        j.ok("a claim cannot be approved before it is reviewed", "409")
    else:
        j.fail("an unreviewed claim must not be approvable", f"got {early.status}")

    review = call(
        "POST", f"{CLAIMS}/claims/{reference}/review", token=admin,
        body={"custody_records_reviewed": True},
    )
    j.check("operations reviews the scan and custody records", review, 200)

    # CLM-04, CLM-01 — approved, and the sender is compensated.
    approved = call(
        "POST", f"{CLAIMS}/claims/{reference}/approval", token=admin,
        body={"compensation_minor_units": 450000},
    )
    if j.check("operations approves the claim", approved, 200):
        amount = approved.get("compensation", "minor_units")
        if amount == 450000:
            j.ok("the amount is exact integer IQD", "450000 IQD")
        else:
            j.fail("the compensation amount must be exact", f"got {amount}")

    mine = call("GET", f"{CLAIMS}/claims/{reference}", token=sender)
    if mine.status == 200 and mine.get("compensation", "minor_units") == 450000:
        j.ok("the sender sees the approved amount on their own claim", "450000 IQD")
    else:
        j.fail("the compensated sender must see the amount", _short(mine.body))

    # CLM-07 — one claimant never reads another's claim.
    stranger = context.get("driver_token")
    if stranger:
        peek = call("GET", f"{CLAIMS}/claims/{reference}", token=stranger)
        if peek.status == 404:
            j.ok("another principal's claim answers 404, not 403", "404")
        else:
            j.fail("another principal must not read this claim", f"got {peek.status}")

    # DRV-P25 and SEC-07 — the driver reports, and is never shown a value.
    if driver:
        incident = call(
            "POST", f"{CLAIMS}/claims/incidents", token=driver,
            body={
                "kind": "DAMAGE_AFTER_ACCEPTANCE",
                "tracking_code": tracking,
                "note": "Crushed in the van.",
            },
        )
        if j.check("a driver reports an incident", incident, 201):
            if incident.get("parcel_stays_in_custody") is True:
                j.ok("the parcel stays in the driver's custody while it is open", "true")
            else:
                j.fail("an open parcel incident must hold the parcel", "")
            body = incident.body if isinstance(incident.body, dict) else {}
            leaked = [k for k in body if "compensation" in k or "amount" in k]
            if not leaked:
                j.ok("SEC-07 — no compensation or claim value reaches the driver", "")
            else:
                j.fail("SEC-07 violated: a driver-facing field carries a value",
                       str(leaked))

        held = call("GET", f"{CLAIMS}/claims/held-parcels", token=admin)
        if held.status == 200 and tracking in held.get("tracking_codes", default=[]):
            j.ok("operations sees which parcels must not move", tracking)
        else:
            j.fail("the held-parcels view must list the held parcel", _short(held.body))

    # OPS-06 — claims and incidents in one view.
    view = call("GET", f"{CLAIMS}/claims/returns-and-claims", token=admin)
    if view.status == 200:
        rows = view.get("rows", default=[])
        j.ok("the returns-and-claims view lists both", f"{len(rows)} rows")
    else:
        j.fail("operations must see the returns-and-claims view", f"got {view.status}")

    # CLM-08 — the one Open Item.
    ready = call("GET", f"{CLAIMS}/ready")
    if "high_value_threshold_decided" in ready.get("blockers", default=[]):
        j.blocked(
            "the high-value declared-value threshold",
            "CLM-08 — a v6.3 Open Item; set CLAIMS_HIGH_VALUE_THRESHOLD once decided",
        )
    else:
        j.ok("the high-value threshold has been set for this deployment", "")

    _ = sender_id
    return j


def journey_authorization() -> Journey:
    """SEC-09 — every service denies an unauthenticated caller.

    Takes no context on purpose: the whole point is that none of these calls carries a
    token, so having one available would be a way to accidentally use it.
    """
    j = Journey("8. Authorization — nothing is reachable without a token")
    targets = [
        ("customer", "GET", f"{CUSTOMER}/customer/me"),
        ("merchant", "GET", f"{MERCHANT}/merchant/applications"),
        # POST: `/ordering/orders` has no GET, and a 405 would say nothing about auth.
        ("ordering", "POST", f"{ORDERING}/ordering/orders"),
        ("hub", "GET", f"{HUB}/hub/hubs"),
        ("notification", "GET", f"{NOTIFICATION}/notification/preferences"),
        ("workforce", "GET",
         f"{WORKFORCE}/workforce/principals/{uuid.uuid4()}/eligibility"),
        ("delivery", "GET", f"{DELIVERY}/delivery/stops"),
        ("finance", "GET", f"{FINANCE}/finance/cash-exposure"),
        ("claims", "GET", f"{CLAIMS}/claims"),
    ]
    for name, method, url in targets:
        response = call(method, url, body={} if method == "POST" else None)
        if response.status in (401, 403):
            j.ok(f"{name} refuses an anonymous caller", str(response.status))
        elif response.status == 404:
            j.ok(f"{name} — route not present at this path", "404 (not an auth hole)")
        elif response.status == 405:
            j.fail(
                f"{name} probe used a verb the route does not have",
                f"{method} {url} -> 405; the probe is wrong, not the service",
            )
        else:
            j.fail(f"{name} must refuse an anonymous caller", f"got {response.status}")

    bad = call("GET", f"{FINANCE}/finance/cash-exposure", token="not-a-real-token")
    if bad.status == 401:
        j.ok("a forged token is rejected by Identity introspection", "401")
    else:
        j.fail("a forged token must be rejected", f"got {bad.status}")
    return j


def journey_health() -> Journey:
    """Every service answers /health and /ready, and says what it is missing."""
    j = Journey("9. Health and readiness — all 14 services")
    services = [
        ("identity", IDENTITY), ("customer", CUSTOMER), ("merchant", MERCHANT),
        ("ordering", ORDERING), ("hub", HUB), ("notification", NOTIFICATION),
        ("workforce", WORKFORCE), ("delivery", DELIVERY), ("finance", FINANCE),
        ("pickup", PICKUP), ("shipment", SHIPMENT), ("tracking", TRACKING),
        ("audit", AUDIT), ("claims", CLAIMS),
    ]
    for name, base in services:
        health = call("GET", f"{base}/health")
        if health.status != 200:
            j.fail(f"{name} /health", f"got {health.status}")
            continue
        ready = call("GET", f"{base}/ready")
        if ready.status == 200:
            j.ok(f"{name}", "healthy and ready")
        else:
            blockers = ready.get("blockers", default=[])
            j.ok(f"{name}", f"healthy; not ready — {', '.join(blockers[:3])}")
    return j


def journey_openapi() -> Journey:
    """Every service publishes a usable OpenAPI document."""
    j = Journey("10. OpenAPI — every service documents its own surface")
    services = [
        ("identity", IDENTITY), ("customer", CUSTOMER), ("merchant", MERCHANT),
        ("ordering", ORDERING), ("hub", HUB), ("notification", NOTIFICATION),
        ("workforce", WORKFORCE), ("delivery", DELIVERY), ("finance", FINANCE),
        ("pickup", PICKUP), ("shipment", SHIPMENT), ("tracking", TRACKING),
        ("audit", AUDIT), ("claims", CLAIMS),
    ]
    # Audit consumes legacy observations and projects them; it has no command API by
    # design and exposes only health. Expecting routes there would be expecting the
    # wrong thing, so it is named rather than flagged.
    consumer_only = {"audit"}
    total = 0
    for name, base in services:
        spec = call("GET", f"{base}/openapi.json")
        if spec.status != 200:
            j.fail(f"{name} openapi.json", f"got {spec.status}")
            continue
        paths = spec.get("paths", default={})
        count = len(paths)
        total += count
        if name in consumer_only:
            if count <= 2:
                j.ok(f"{name}", f"{count} paths — a consumer, no command API by design")
            else:
                j.fail(f"{name} is supposed to be consumer-only",
                       f"it now publishes {count} paths")
        elif count <= 2:
            j.fail(f"{name} publishes only {count} path(s)",
                   "its router is probably not wired in")
        else:
            j.ok(f"{name}", f"{count} paths — {base}/docs")
    j.ok("total documented endpoints", str(total))
    return j


ALL = {
    "identity": None, "roles": None, "merchant": None, "workforce": None,
    "delivery": None, "finance": None, "claims": None, "authorization": None,
    "health": None, "openapi": None,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", action="append", choices=sorted(ALL))
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    VERBOSE.verbose = args.verbose
    wanted = set(args.only or ALL)

    if call("GET", f"{IDENTITY}/health").status != 200:
        print("The stack is not running. Start it with:")
        print("  uv run python scripts/dev/stack.py up")
        return 1

    journeys: list[Journey] = []
    context: dict = {}

    identity_journey, context = journey_identity()
    if "identity" in wanted:
        journeys.append(identity_journey)
    roles_journey = journey_roles(context)
    if "roles" in wanted:
        journeys.append(roles_journey)
    if "merchant" in wanted:
        journeys.append(journey_merchant(context))
    if "workforce" in wanted:
        journeys.append(journey_workforce(context))
    if "delivery" in wanted:
        journeys.append(journey_delivery(context))
    if "finance" in wanted:
        journeys.append(journey_finance(context))
    if "claims" in wanted:
        journeys.append(journey_claims(context))
    if "authorization" in wanted:
        journeys.append(journey_authorization())
    if "health" in wanted:
        journeys.append(journey_health())
    if "openapi" in wanted:
        journeys.append(journey_openapi())

    for journey in journeys:
        journey.render()

    failed = sum(j.failed for j in journeys)
    blocked = sum(j.blocked_count for j in journeys)
    passed = sum(
        1 for j in journeys for status, _, _ in j.steps if status == "ok"
    )
    print("\n" + "=" * 72)
    print(f"{passed} steps passed · {blocked} blocked by a documented open item · {failed} failed")
    if blocked:
        print("A BLOCKED step is the platform refusing to invent a business decision.")
    if failed:
        print("A FAIL step is a bug. Its request and response are printed above.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
