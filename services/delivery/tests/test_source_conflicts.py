"""The two documented source contradictions, held open rather than silently resolved.

DRV-L05 — the Customer App says the delivery code is four digits and the Driver App
collects six. DRV-L07 — the Driver App says the ID is checked and not stored, and the
retention schedule says identity evidence is retained.

Neither is settled here. What these tests assert is that the service ships with no
default, refuses only the operation that needs the decision, and keeps the rest working.
"""

from __future__ import annotations

import inspect
from uuid import uuid4

import pytest
from delivery_fixtures import (
    TEST_CODE,
    TEST_HMAC_KEY,
    authorize_at_the_door,
    build_lab,
    drive_to_the_door,
    scan_parcel,
)

from delivery.config import load_settings
from delivery.domain import entities
from delivery.domain.delivery_code import (
    CONFLICT_SOURCES,
    CONFLICTING_LENGTHS,
    DeliveryCodeLengthNotDecided,
    DeliveryCodePolicy,
    hash_delivery_code,
    verify_delivery_code,
)
from delivery.domain.errors import DeliveryCodeIncorrect
from delivery.domain.id_evidence import IdEvidencePolicy, IdPhotoRetentionNotDecided
from delivery.domain.value_objects import EvidenceMediaRef, StopStatus
from delivery.infrastructure.persistence import models

# ------------------------------------------------------- DRV-L05: the length


def test_the_shipped_policy_has_not_decided_a_length() -> None:
    assert DeliveryCodePolicy().length is None
    assert DeliveryCodePolicy().is_decided is False


def test_the_shipped_settings_have_not_decided_a_length() -> None:
    """No default in configuration either — an env var absent means undecided."""
    settings = load_settings(delivery_code_length=None)
    assert settings.delivery_code_length is None
    assert settings.delivery_code_decided is False


def test_both_readings_are_recorded_in_the_code() -> None:
    """The disagreement lives in the source, not only in a document."""
    assert CONFLICTING_LENGTHS == (4, 6)
    assert "4-digit" in CONFLICT_SOURCES[4]
    assert "6 digits" in CONFLICT_SOURCES[6]


def test_verification_refuses_while_the_length_is_undecided() -> None:
    lab = build_lab(code_length=None)
    stop = scan_parcel(lab)
    drive_to_the_door(lab, stop)
    with pytest.raises(DeliveryCodeLengthNotDecided):
        lab.doorstep.verify_with_code(
            stop_id=stop.stop_id, driver_principal_id=lab.driver_id, code=TEST_CODE
        )


def test_an_undecided_length_blocks_only_the_code_path() -> None:
    """Everything that does not depend on the decision keeps working.

    The named-receiver fallback, the seal, the inspection and the payment are all
    reachable with the length still unset.
    """
    lab = build_lab(code_length=None)
    stop = scan_parcel(lab, named_receiver="Zaid Al-Rawi", packaging_seal_code="S-1")
    drive_to_the_door(lab, stop)
    result = lab.doorstep.verify_with_named_receiver_id(
        stop_id=stop.stop_id,
        driver_principal_id=lab.driver_id,
        id_matches_named_receiver=True,
    )
    assert result.authorized is True
    assert result.stop.status is StopStatus.AUTHORIZED


def test_a_decided_length_opens_the_code_path_with_no_code_change() -> None:
    lab = build_lab(code_length=6)
    stop = scan_parcel(lab)
    drive_to_the_door(lab, stop)
    result = lab.doorstep.verify_with_code(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id, code=TEST_CODE
    )
    assert result.authorized is True


def test_either_reading_works_once_it_is_chosen() -> None:
    """Whichever way the business decides, the service already supports it."""
    for length, code in ((4, "4829"), (6, "482913")):
        lab = build_lab(code_length=length)
        stop = scan_parcel(lab, code=code)
        drive_to_the_door(lab, stop)
        result = lab.doorstep.verify_with_code(
            stop_id=stop.stop_id, driver_principal_id=lab.driver_id, code=code
        )
        assert result.authorized is True, length


def test_a_code_of_the_wrong_length_is_rejected_not_truncated() -> None:
    lab = build_lab(code_length=6)
    stop = scan_parcel(lab)
    drive_to_the_door(lab, stop)
    with pytest.raises(DeliveryCodeIncorrect):
        lab.doorstep.verify_with_code(
            stop_id=stop.stop_id, driver_principal_id=lab.driver_id, code="4829"
        )


# ------------------------------------------------------ DRV-L05: the handling


def test_the_code_is_never_stored_in_the_clear() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    stored = lab.uow.stops.get(stop.stop_id)
    assert stored is not None
    assert stored.delivery_code_digest is not None
    assert TEST_CODE not in str(stored.delivery_code_digest)
    assert TEST_CODE not in repr(stored)


def test_the_digest_is_bound_to_the_stop() -> None:
    """A code lifted from one parcel cannot be replayed against another."""
    digest = hash_delivery_code(TEST_CODE, key=TEST_HMAC_KEY, stop_reference="SHP-A")
    assert not verify_delivery_code(
        candidate=TEST_CODE,
        expected_digest=digest,
        key=TEST_HMAC_KEY,
        stop_reference="SHP-B",
    )


def test_no_entity_has_a_field_that_could_hold_the_code() -> None:
    offenders = [
        f"{name}.{field}"
        for name, obj in vars(entities).items()
        if isinstance(obj, type) and hasattr(obj, "__dataclass_fields__")
        for field in obj.__dataclass_fields__
        if "code" in field
        and not field.endswith("_digest")
        and field not in {"tracking_code", "packaging_seal_code", "code_attempt_count"}
    ]
    assert offenders == []


def test_no_model_column_could_hold_the_code() -> None:
    offenders = [
        f"{table.name}.{column.name}"
        for table in models.Base.metadata.tables.values()
        for column in table.columns
        if "code" in column.name
        and not column.name.endswith("_digest")
        and column.name
        not in {
            "tracking_code",
            "packaging_seal_code",
            "code_attempt_count",
            "last_error_code",
        }
    ]
    assert offenders == []


# --------------------------------------------------- DRV-L07: the ID evidence


def test_the_shipped_policy_has_not_decided_retention() -> None:
    assert IdEvidencePolicy().retention_decided is False


def test_the_shipped_settings_have_not_decided_retention() -> None:
    assert load_settings().id_photo_retention_decided is False


def test_offering_an_id_photograph_is_refused() -> None:
    lab = build_lab()
    stop = scan_parcel(lab, named_receiver="Zaid Al-Rawi")
    drive_to_the_door(lab, stop)
    with pytest.raises(IdPhotoRetentionNotDecided):
        lab.doorstep.verify_with_named_receiver_id(
            stop_id=stop.stop_id,
            driver_principal_id=lab.driver_id,
            id_matches_named_receiver=True,
            id_photo=EvidenceMediaRef(bucket="evidence", key="id.jpg"),
        )


def test_the_id_fallback_itself_works_without_a_photograph() -> None:
    """The verification is not blocked — only retaining imagery of it is."""
    lab = build_lab()
    stop = scan_parcel(lab, named_receiver="Zaid Al-Rawi")
    drive_to_the_door(lab, stop)
    result = lab.doorstep.verify_with_named_receiver_id(
        stop_id=stop.stop_id,
        driver_principal_id=lab.driver_id,
        id_matches_named_receiver=True,
    )
    assert result.authorized is True
    attempts = lab.doorstep.verifications_for(stop_id=stop.stop_id)
    assert attempts[-1].id_matched_named_receiver is True


def test_no_entity_has_a_field_that_could_hold_an_id_photograph() -> None:
    """An unrecorded photo can be taken later; a wrongly retained one cannot be untaken."""
    offenders = [
        f"{name}.{field}"
        for name, obj in vars(entities).items()
        if isinstance(obj, type) and hasattr(obj, "__dataclass_fields__")
        for field in obj.__dataclass_fields__
        if "id_photo" in field or "identity_document" in field or "id_image" in field
    ]
    assert offenders == []


def test_no_model_column_could_hold_an_id_photograph() -> None:
    offenders = [
        f"{table.name}.{column.name}"
        for table in models.Base.metadata.tables.values()
        for column in table.columns
        if "id_photo" in column.name
        or "identity_document" in column.name
        or "id_image" in column.name
    ]
    assert offenders == []


def test_the_id_photo_parameter_exists_only_to_be_refused() -> None:
    """It is accepted so the refusal is explicit rather than a silent drop."""
    signature = inspect.signature(
        type(build_lab().doorstep).verify_with_named_receiver_id
    )
    assert "id_photo" in signature.parameters
    assert signature.parameters["id_photo"].default is None


def test_a_decided_retention_would_allow_it_without_a_code_change() -> None:
    lab = build_lab(retention_decided=True)
    stop = scan_parcel(lab, named_receiver="Zaid Al-Rawi")
    drive_to_the_door(lab, stop)
    result = lab.doorstep.verify_with_named_receiver_id(
        stop_id=stop.stop_id,
        driver_principal_id=lab.driver_id,
        id_matches_named_receiver=True,
        id_photo=EvidenceMediaRef(bucket="evidence", key="id.jpg"),
    )
    assert result.authorized is True
    # Still not persisted: enabling retention unblocks the operation, and where the
    # image is kept is the storage decision that comes with it.
    stored = lab.uow.stops.get(stop.stop_id)
    assert "id.jpg" not in repr(stored)


def test_the_rest_of_the_delivery_is_unaffected_by_both_conflicts() -> None:
    """The instruction was to continue all unrelated delivery implementation."""
    lab = build_lab()
    stop = scan_parcel(lab)
    authorize_at_the_door(lab, stop)
    lab.payments.confirm_prepaid(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    result = lab.outcomes.complete_delivery(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    assert result.stop.status is StopStatus.DELIVERED
    assert result.delivered_event_id != uuid4()
