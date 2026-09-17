"""Domain errors for the Workforce service."""

from __future__ import annotations


class WorkforceError(Exception):
    """Base Workforce domain error."""


# ------------------------------------------------------------------ lookup


class ApplicationNotFound(WorkforceError):
    def __init__(self, application_id: str) -> None:
        self.application_id = application_id
        super().__init__(f"driver application not found: {application_id}")


class DriverNotFound(WorkforceError):
    def __init__(self, driver_id: str) -> None:
        self.driver_id = driver_id
        super().__init__(f"driver not found: {driver_id}")


class LeaveRequestNotFound(WorkforceError):
    def __init__(self, leave_id: str) -> None:
        self.leave_id = leave_id
        super().__init__(f"leave request not found: {leave_id}")


class BlockNotFound(WorkforceError):
    def __init__(self, block_id: str) -> None:
        self.block_id = block_id
        super().__init__(f"lateness block not found: {block_id}")


# ------------------------------------------------------------------ onboarding


class ApplicationAlreadyOpen(WorkforceError):
    def __init__(self, reference: str) -> None:
        self.reference = reference
        super().__init__(f"an application is already open: {reference}")


class ApplicationTransitionNotAllowed(WorkforceError):
    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"cannot move a {current} application to {target}")


class OfficeVerificationRequired(WorkforceError):
    """SEC-03 — the physical check is not optional.

    Driver App v8 ``reg4``: "You must visit the nearest HUDHUD office to complete
    physical verification of your identity, vehicle and documents before tasks can be
    assigned."
    """

    def __init__(self) -> None:
        super().__init__(
            "a driver becomes assignable only after physical verification of identity, "
            "vehicle and documents at a HUDHUD office"
        )


class DocumentsIncomplete(WorkforceError):
    def __init__(self, missing: tuple[str, ...]) -> None:
        self.missing = missing
        super().__init__("office verification is missing: " + ", ".join(missing))


class ShiftSelectionRequired(WorkforceError):
    """Driver App v8 ``regHours``: "Pick at least one shift"."""

    def __init__(self) -> None:
        super().__init__(
            "a driver must name at least one shift they can work — it drives their "
            "daily parcel assignment"
        )


class TermsNotAccepted(WorkforceError):
    def __init__(self) -> None:
        super().__init__("the driver agreement must be accepted before applying")


# ------------------------------------------------------------------ attendance


class NoShiftScheduled(WorkforceError):
    def __init__(self, shift_date: str) -> None:
        self.shift_date = shift_date
        super().__init__(f"this driver has no shift scheduled on {shift_date}")


class ShiftAlreadyStarted(WorkforceError):
    def __init__(self, shift_date: str) -> None:
        self.shift_date = shift_date
        super().__init__(f"the shift on {shift_date} has already been started")


class DriverNotAssignable(WorkforceError):
    """Raised when something tries to start work a driver may not be given.

    Carries the reasons so the caller can tell the driver what to do next.
    """

    def __init__(self, reasons: tuple[str, ...]) -> None:
        self.reasons = reasons
        super().__init__("this driver cannot be assigned work: " + ", ".join(reasons))


# ------------------------------------------------------------------ blocks


class BlockAlreadyCleared(WorkforceError):
    def __init__(self, block_id: str) -> None:
        self.block_id = block_id
        super().__init__(f"this block has already been cleared: {block_id}")


class OnlySupportMayClearABlock(WorkforceError):
    """OPS-09 — "Assignment is paused until support clears the record"."""

    def __init__(self) -> None:
        super().__init__(
            "a lateness block is cleared by support or operations, never by the driver "
            "it applies to"
        )


# ------------------------------------------------------------------ leave


class LeaveTransitionNotAllowed(WorkforceError):
    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"cannot move a {current} leave request to {target}")


class OnlySupportMayDecideLeave(WorkforceError):
    """OPS-09 — support decides leave requests; a driver cannot approve their own."""

    def __init__(self) -> None:
        super().__init__(
            "a leave request is decided by support or operations, never by the driver "
            "who raised it"
        )


class OverlappingLeaveRequest(WorkforceError):
    def __init__(self, reference: str) -> None:
        self.reference = reference
        super().__init__(f"leave already requested for these dates: {reference}")


class LeaveDecisionNoteRequired(WorkforceError):
    def __init__(self) -> None:
        super().__init__("a declined leave request must say why")


# ------------------------------------------------------------------ misc


class InvalidVehicleDetails(WorkforceError):
    def __init__(self, detail: str) -> None:
        super().__init__(f"invalid vehicle details: {detail}")


class StaleWorkforceRecord(WorkforceError):
    def __init__(self, table: str) -> None:
        self.table = table
        super().__init__(f"{table} changed concurrently")
