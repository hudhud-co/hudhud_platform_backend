"""Domain errors for the Customer service."""

from __future__ import annotations


class CustomerError(Exception):
    """Base Customer domain error."""


class CustomerProfileNotFound(CustomerError):
    def __init__(self, principal_id: str) -> None:
        self.principal_id = principal_id
        super().__init__(f"customer profile not found: {principal_id}")


class DisplayNameRequired(CustomerError):
    def __init__(self) -> None:
        super().__init__("a display name is required to complete the profile")


class UnknownGovernorate(CustomerError):
    def __init__(self, governorate: str) -> None:
        self.governorate = governorate
        super().__init__(f"unknown governorate: {governorate}")


class ReceiverContactIncomplete(CustomerError):
    """Phone and governorate are the minimum HUDHUD needs to reach and route."""

    def __init__(self, missing: tuple[str, ...]) -> None:
        self.missing = missing
        super().__init__("receiver contact incomplete: " + ", ".join(missing))


class AddressNotFound(CustomerError):
    def __init__(self, address_id: str) -> None:
        self.address_id = address_id
        super().__init__(f"address not found: {address_id}")


class AddressNotOwnedByCustomer(CustomerError):
    def __init__(self, address_id: str) -> None:
        self.address_id = address_id
        super().__init__(f"address is not owned by this customer: {address_id}")


class AddressArchived(CustomerError):
    def __init__(self, address_id: str) -> None:
        self.address_id = address_id
        super().__init__(f"address is archived: {address_id}")


class LastAddressCannotBeArchived(CustomerError):
    """Archiving the only default would leave the customer unable to send anything."""

    def __init__(self) -> None:
        super().__init__("the only default address cannot be archived")


class LegalDocumentNotAccepted(CustomerError):
    def __init__(self, *, kind: str, required_version: str) -> None:
        self.kind = kind
        self.required_version = required_version
        super().__init__(f"{kind} version {required_version} has not been accepted")


class ContactNotFound(CustomerError):
    def __init__(self, contact_id: str) -> None:
        self.contact_id = contact_id
        super().__init__(f"contact not found: {contact_id}")
