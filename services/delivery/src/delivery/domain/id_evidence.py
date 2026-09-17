"""The ID fallback, and the unresolved contradiction about photographing an ID.

## The conflict (DRV-L07)

* **v6.3 p.26** says the driver "checks the ID against the on-file details **and
  photographs the ID card as a record**".
* **Driver App v8** `lmIdCapture` says the opposite, in the interface the driver actually
  uses: "The ID is checked, **not stored as a photo**. Verification evidence is separate
  from photo documentation."

This is a data-retention question about a government identity document, not a UI detail.
Getting it wrong in one direction loses evidence for a later dispute; getting it wrong in
the other creates a store of ID photographs nobody authorised.

## How this service behaves

**No ID photograph is persisted, and there is no field one could be persisted in.** The
model records what was checked and what the driver concluded — the method, the outcome,
who checked, and when — which is the part both sources agree on.

That is the safer half of the contradiction while it stands: an unrecorded photo can be
taken later if the business decides it must be, but a wrongly retained one cannot be
untaken. `ID_PHOTO_RETENTION_DECIDED` exists so the decision is explicit; until it is set,
an attempt to attach ID imagery is refused rather than quietly dropped.

## What is *not* in doubt

v6.3 p.26 is unambiguous that the fallback works **only for the merchant-named receiver**.
Anyone else needs the code.
"""

from __future__ import annotations

from dataclasses import dataclass

CONFLICT_SOURCES: dict[str, str] = {
    "retain": "v6.3 p.26: the driver 'photographs the ID card as a record'",
    "do_not_retain": (
        "Driver App v8 `lmIdCapture`: 'The ID is checked, not stored as a photo.'"
    ),
}


class IdPhotoRetentionNotDecided(RuntimeError):
    """Raised when something tries to attach an ID photograph.

    Refused rather than silently discarded: a caller that believes it stored evidence and
    did not is worse than one that is told it cannot.
    """

    def __init__(self) -> None:
        super().__init__(
            "whether an ID card is photographed and retained is an unresolved "
            "contradiction between v6.3 p.26 (photograph it) and the Driver App "
            "('checked, not stored as a photo'); no ID imagery is retained until it is "
            "decided"
        )


@dataclass(frozen=True, slots=True)
class IdEvidencePolicy:
    """Whether ID imagery may be retained at all.

    ``False`` is the shipped state and follows the Driver App, which is the narrower and
    reversible reading of the contradiction.
    """

    retention_decided: bool = False

    def assert_may_retain_photo(self) -> None:
        if not self.retention_decided:
            raise IdPhotoRetentionNotDecided()
