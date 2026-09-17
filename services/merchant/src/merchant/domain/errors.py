"""Domain errors for the Merchant service."""

from __future__ import annotations


class MerchantError(Exception):
    """Base Merchant domain error."""


# ------------------------------------------------------------------ application


class ApplicationNotFound(MerchantError):
    def __init__(self, application_id: str) -> None:
        self.application_id = application_id
        super().__init__(f"merchant application not found: {application_id}")


class ApplicationAlreadyOpen(MerchantError):
    """One application at a time (Customer App v3 ``storeReview``)."""

    def __init__(self, application_id: str) -> None:
        self.application_id = application_id
        super().__init__(f"an application is already open: {application_id}")


class ApplicationTransitionNotAllowed(MerchantError):
    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"cannot move a {current} application to {target}")


class ApplicationDataSetNotDefined(MerchantError):
    """MER-02 — the one operation that genuinely cannot proceed.

    v6.3 p.10 records the merchant-application data set as an open item that "needs a
    deliberate answer before the application flow can be built". Everything around the
    application is implemented; submitting one is refused until an operator configures
    which attributes are required, because guessing them would invent a KYC policy.
    """

    def __init__(self) -> None:
        super().__init__(
            "the merchant-application data set is an unresolved v6.3 open item "
            "(Appendix A p.44); set MERCHANT_APPLICATION_REQUIRED_ATTRIBUTES to the "
            "approved field list before applications may be submitted"
        )


class ApplicationAttributesMissing(MerchantError):
    def __init__(self, missing: tuple[str, ...]) -> None:
        self.missing = missing
        super().__init__("application is missing required details: " + ", ".join(missing))


class DecisionReasonRequired(MerchantError):
    """A "Needs changes" decision without a reason is not actionable by the applicant."""

    def __init__(self) -> None:
        super().__init__("a changes-requested decision must carry a reason")


# ------------------------------------------------------------------ merchant


class MerchantNotFound(MerchantError):
    def __init__(self, merchant_id: str) -> None:
        self.merchant_id = merchant_id
        super().__init__(f"merchant not found: {merchant_id}")


class MerchantNotActive(MerchantError):
    def __init__(self, merchant_id: str) -> None:
        self.merchant_id = merchant_id
        super().__init__(f"merchant is not active: {merchant_id}")


class MerchantCodeAlreadyIssued(MerchantError):
    def __init__(self, merchant_code: str) -> None:
        self.merchant_code = merchant_code
        super().__init__(f"merchant code already issued: {merchant_code}")


class SenderMayNotSetPlatformPolicy(MerchantError):
    """v6.3 p.13 "Role boundary — Sender" (MER-15).

    The sender decides commercial terms and add-ons. Return-fee ownership, refund
    ownership, the 3-day hold, the 10-minute wait and Hudhud's liability position are
    fixed company-wide rules, so the API refuses them rather than storing a value that
    would later be read as authoritative.
    """

    def __init__(self, keys: tuple[str, ...]) -> None:
        self.keys = keys
        super().__init__(
            "these are company-wide rules a sender cannot set: " + ", ".join(keys)
        )


# ------------------------------------------------------------------ store


class StoreNotFound(MerchantError):
    def __init__(self, store_id: str) -> None:
        self.store_id = store_id
        super().__init__(f"store not found: {store_id}")


class StoreArchived(MerchantError):
    def __init__(self, store_id: str) -> None:
        self.store_id = store_id
        super().__init__(f"store is archived: {store_id}")


class LastStoreCannotBeArchived(MerchantError):
    def __init__(self) -> None:
        super().__init__("the only active store cannot be archived")


class UnknownGovernorate(MerchantError):
    def __init__(self, governorate: str) -> None:
        self.governorate = governorate
        super().__init__(f"unknown governorate: {governorate}")


# ------------------------------------------------------------------ team


class MembershipNotFound(MerchantError):
    def __init__(self, membership_id: str) -> None:
        self.membership_id = membership_id
        super().__init__(f"team membership not found: {membership_id}")


class MembershipAlreadyExists(MerchantError):
    def __init__(self, phone_last4: str) -> None:
        self.phone_last4 = phone_last4
        super().__init__(f"this number already has a live membership: ***{phone_last4}")


class MembershipNotPending(MerchantError):
    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"membership is {status}, not pending")


class MembershipStoresRequired(MerchantError):
    """Customer App v3 ``teamForm``: "BRANCHES · ONE OR MORE"."""

    def __init__(self) -> None:
        super().__init__("a team member must be assigned at least one branch")


class ForbiddenTeamCapability(MerchantError):
    """No store team role may ever carry shipment authoring or money visibility."""

    def __init__(self, capability: str) -> None:
        self.capability = capability
        super().__init__(f"a store team member may never hold {capability}")


class InvalidPhoneNumber(MerchantError):
    def __init__(self) -> None:
        super().__init__("a valid phone number is required")


# ------------------------------------------------------------------ labels


class PrinterAuthorizationNotFound(MerchantError):
    def __init__(self, authorization_id: str) -> None:
        self.authorization_id = authorization_id
        super().__init__(f"printer authorization not found: {authorization_id}")


class SelfPrintingNotAuthorized(MerchantError):
    """MER-04 — self-printed stock without Hudhud-supplied equipment is refused."""

    def __init__(self) -> None:
        super().__init__(
            "self-printed label stock requires an active Hudhud printer authorization; "
            "no other printer or label stock may be used (v6.3 p.12)"
        )


class LabelStockExhausted(MerchantError):
    def __init__(self, allocation_id: str) -> None:
        self.allocation_id = allocation_id
        super().__init__(f"label stock allocation is exhausted: {allocation_id}")


class LabelCountInvalid(MerchantError):
    def __init__(self) -> None:
        super().__init__("a label stock allocation must contain at least one label")


# ------------------------------------------------------------------ catalogue


class ProductNotFound(MerchantError):
    def __init__(self, product_id: str) -> None:
        self.product_id = product_id
        super().__init__(f"product not found: {product_id}")


class ProductNameRequired(MerchantError):
    def __init__(self) -> None:
        super().__init__("a product name is required")


class CategoryNotFound(MerchantError):
    def __init__(self, category_id: str) -> None:
        self.category_id = category_id
        super().__init__(f"product category not found: {category_id}")


class CategoryInUse(MerchantError):
    def __init__(self, product_count: int) -> None:
        self.product_count = product_count
        super().__init__(
            f"category still holds {product_count} product(s) and cannot be deleted"
        )


# ------------------------------------------------------------------ concurrency


class StaleMerchantRecord(MerchantError):
    """A concurrent writer changed the row since it was read."""

    def __init__(self, table: str) -> None:
        self.table = table
        super().__init__(f"{table} changed concurrently")
