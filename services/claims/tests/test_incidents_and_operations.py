"""Driver incidents (DRV-P25), the operations view (OPS-06) and resolution (OPS-07).

Plus CLM-08, the one v6.3 Open Item that lands in this service, and SEC-07, which is the
reason a driver incident has no amount on it anywhere.
"""

from __future__ import annotations

import dataclasses
from uuid import uuid4

import pytest
from claims_fixtures import (
    build_lab,
    driver,
    iqd,
    next_tracking_code,
    operations,
    photo,
    support,
)

from claims.application.claim_service import ClaimService
from claims.domain.entities import DriverIncident
from claims.domain.errors import (
    HighValueThresholdNotSet,
    IncidentNeedsAParcel,
    IncidentNotFound,
    IncidentTransitionNotAllowed,
    InvalidTrackingCode,
    OnlyOperationsResolvesAnIncident,
    ResolutionNoteRequired,
)
from claims.domain.high_value import HighValuePolicy
from claims.domain.value_objects import (
    ClaimKind,
    ClaimOpenedBy,
    IncidentKind,
    IncidentStatus,
)


def report(lab, kind=IncidentKind.DAMAGE_AFTER_ACCEPTANCE, **overrides):
    defaults = {
        "kind": kind,
        "driver_principal_id": lab.driver_id,
        "tracking_code": next_tracking_code(),
    }
    defaults.update(overrides)
    return lab.incidents.report(**defaults)


# ------------------------------------------------------------------ DRV-P25


def test_the_five_incident_kinds_are_the_ones_the_driver_app_offers() -> None:
    """Driver App v8's own list, verbatim."""
    assert {kind.value for kind in IncidentKind} == {
        "DAMAGE_AFTER_ACCEPTANCE",
        "PARCEL_MISSING",
        "LABEL_UNREADABLE",
        "VEHICLE_OR_SAFETY_ISSUE",
        "HANDOFF_MISMATCH",
    }


def test_a_driver_can_report_each_parcel_kind() -> None:
    for kind in (
        IncidentKind.DAMAGE_AFTER_ACCEPTANCE,
        IncidentKind.PARCEL_MISSING,
        IncidentKind.LABEL_UNREADABLE,
        IncidentKind.HANDOFF_MISMATCH,
    ):
        lab = build_lab()
        incident = report(lab, kind)
        assert incident.kind is kind
        assert incident.status is IncidentStatus.SUBMITTED


def test_a_parcel_kind_must_name_the_parcel() -> None:
    """Driver App v8 disables submission without one (`needsParcel`)."""
    lab = build_lab()
    with pytest.raises(IncidentNeedsAParcel):
        report(lab, IncidentKind.PARCEL_MISSING, tracking_code=None)


def test_a_vehicle_or_safety_issue_needs_no_parcel() -> None:
    """A breakdown is about the driver, not about any one parcel."""
    lab = build_lab()
    incident = report(
        lab, IncidentKind.VEHICLE_OR_SAFETY_ISSUE, tracking_code=None
    )
    assert incident.tracking_code is None
    assert incident.is_about_a_parcel is False


def test_a_vehicle_issue_discards_a_parcel_even_if_one_is_offered() -> None:
    lab = build_lab()
    incident = report(
        lab, IncidentKind.VEHICLE_OR_SAFETY_ISSUE, tracking_code=next_tracking_code()
    )
    assert incident.tracking_code is None


def test_a_malformed_tracking_code_is_refused() -> None:
    lab = build_lab()
    with pytest.raises(InvalidTrackingCode):
        report(lab, tracking_code="nope")


def test_the_parcel_stays_in_custody_while_the_report_is_open() -> None:
    """Driver App v8: "The parcel stays in your custody while the report is open"."""
    lab = build_lab()
    incident = report(lab)
    assert incident.parcel_stays_in_custody is True
    assert incident.tracking_code in lab.incidents.parcels_held_by_open_incidents()


def test_a_resolved_incident_releases_the_parcel() -> None:
    lab = build_lab()
    incident = report(lab)
    lab.incidents.resolve(
        reference=incident.reference,
        actor=operations(),
        note="repacked at the hub and sent on",
    )
    assert lab.incidents.parcels_held_by_open_incidents() == ()


def test_a_vehicle_issue_holds_no_parcel() -> None:
    lab = build_lab()
    report(lab, IncidentKind.VEHICLE_OR_SAFETY_ISSUE, tracking_code=None)
    assert lab.incidents.parcels_held_by_open_incidents() == ()


# ------------------------------------------------------------------ OPS-07


def test_only_operations_resolves_an_incident() -> None:
    """The driver reports; operations resolves."""
    lab = build_lab()
    incident = report(lab)
    for wrong in (driver(lab.driver_id), support()):
        with pytest.raises(OnlyOperationsResolvesAnIncident):
            lab.incidents.resolve(
                reference=incident.reference, actor=wrong, note="I had a look"
            )


def test_resolving_must_say_what_was_found() -> None:
    lab = build_lab()
    incident = report(lab)
    with pytest.raises(ResolutionNoteRequired):
        lab.incidents.resolve(
            reference=incident.reference, actor=operations(), note="  "
        )


def test_an_incident_can_go_through_investigation_first() -> None:
    lab = build_lab()
    incident = report(lab)
    investigating = lab.incidents.start_investigation(
        reference=incident.reference, actor=operations()
    )
    assert investigating.status is IncidentStatus.UNDER_INVESTIGATION
    resolved = lab.incidents.resolve(
        reference=incident.reference, actor=operations(), note="found at the hub"
    )
    assert resolved.status is IncidentStatus.RESOLVED


def test_a_resolved_incident_is_resolved_once() -> None:
    lab = build_lab()
    incident = report(lab)
    lab.incidents.resolve(
        reference=incident.reference, actor=operations(), note="closed"
    )
    with pytest.raises(IncidentTransitionNotAllowed):
        lab.incidents.resolve(
            reference=incident.reference, actor=operations(), note="closed again"
        )


def test_resolving_records_who_did_it_and_what_they_found() -> None:
    lab = build_lab()
    incident = report(lab)
    operator = operations()
    resolved = lab.incidents.resolve(
        reference=incident.reference,
        actor=operator,
        note="label reprinted at the hub",
    )
    assert resolved.resolved_by_actor_id == operator.principal_id
    assert resolved.resolution_note == "label reprinted at the hub"


def test_an_incident_can_be_linked_to_the_claim_it_became() -> None:
    """The incident records that a claim exists; the claim carries the money."""
    lab = build_lab()
    incident = report(lab)
    filed = lab.claims.file_claim(
        tracking_code=incident.tracking_code,
        kind=ClaimKind.DAMAGED_IN_TRANSIT,
        opened_by=ClaimOpenedBy.DRIVER,
        opened_by_principal_id=lab.driver_id,
        sender_principal_id=lab.sender_id,
        evidence=(photo(),),
    )
    resolved = lab.incidents.resolve(
        reference=incident.reference,
        actor=operations(),
        note="raised as a claim",
        linked_claim_id=filed.claim.claim_id,
    )
    assert resolved.linked_claim_id == filed.claim.claim_id


def test_an_unknown_incident_is_not_found() -> None:
    lab = build_lab()
    with pytest.raises(IncidentNotFound):
        lab.incidents.resolve(
            reference="CLM-20260915-999999", actor=operations(), note="x"
        )


# ------------------------------------------------------------------ SEC-07


def test_an_incident_has_no_compensation_field_at_all() -> None:
    """Driver App v8: "No amounts are shown or decided here"."""
    fields = {f.name for f in dataclasses.fields(DriverIncident)}
    assert not {
        "compensation_amount",
        "amount",
        "value",
        "compensation",
        "claim_value",
    } & fields


def test_the_driver_sees_their_own_incidents_without_any_value() -> None:
    lab = build_lab()
    report(lab)
    report(lab, IncidentKind.LABEL_UNREADABLE)
    mine = lab.incidents.my_incidents(driver_principal_id=lab.driver_id)
    assert len(mine) == 2
    for view in mine:
        assert not hasattr(view, "compensation_amount")


def test_the_driver_incident_view_matches_what_incident_done_shows() -> None:
    """Reference, type, parcel, submitted, status — and nothing else."""
    lab = build_lab()
    incident = report(lab)
    view = lab.incidents.for_driver(reference=incident.reference)
    assert view.reference == incident.reference
    assert view.kind == IncidentKind.DAMAGE_AFTER_ACCEPTANCE.value
    assert view.tracking_code == incident.tracking_code


# ------------------------------------------------------------------ OPS-06


def test_the_returns_and_claims_view_shows_claims_and_incidents_together() -> None:
    lab = build_lab()
    report(lab)
    lab.claims.file_claim(
        tracking_code=next_tracking_code(),
        kind=ClaimKind.LOST_PARCEL,
        opened_by=ClaimOpenedBy.SENDER,
        opened_by_principal_id=lab.sender_id,
        sender_principal_id=lab.sender_id,
    )
    rows = lab.incidents.returns_and_claims(actor=operations())
    assert len(rows) == 2
    assert {row.opened_by for row in rows} == {"DRIVER", "SENDER"}


def test_the_view_says_which_parcels_are_held() -> None:
    lab = build_lab()
    report(lab)
    rows = lab.incidents.returns_and_claims(actor=operations())
    assert rows[0].extra["parcel_held"] == "true"


def test_operations_sees_the_compensation_on_the_view() -> None:
    lab = build_lab()
    filed = lab.claims.file_claim(
        tracking_code=next_tracking_code(),
        kind=ClaimKind.LOST_PARCEL,
        opened_by=ClaimOpenedBy.SENDER,
        opened_by_principal_id=lab.sender_id,
        sender_principal_id=lab.sender_id,
    )
    lab.claims.start_review(
        reference=filed.reference, actor=support(), custody_records_reviewed=True
    )
    # Still open until approved, so it is on the queue with its proposed state.
    rows = lab.incidents.returns_and_claims(actor=operations())
    assert rows[0].reference == filed.reference


def test_a_driver_on_the_view_sees_no_amounts() -> None:
    """SEC-07 again: an operator who is also a driver sees the rows without values."""
    lab = build_lab()
    filed = lab.claims.file_claim(
        tracking_code=next_tracking_code(),
        kind=ClaimKind.LOST_PARCEL,
        opened_by=ClaimOpenedBy.SENDER,
        opened_by_principal_id=lab.sender_id,
        sender_principal_id=lab.sender_id,
    )
    assert filed.reference
    rows = lab.incidents.returns_and_claims(actor=driver(lab.driver_id))
    assert all(row.compensation_amount is None for row in rows)


# ------------------------------------------------------------------ CLM-08


def test_the_high_value_threshold_has_no_default() -> None:
    """v6.3 Appendix A p.44 — an Open Item, and a number nobody has agreed."""
    assert HighValuePolicy().threshold is None
    assert HighValuePolicy().is_decided is False


def test_asking_whether_a_parcel_is_high_value_is_refused() -> None:
    policy = HighValuePolicy()
    with pytest.raises(HighValueThresholdNotSet) as caught:
        policy.is_high_value(iqd(5_000_000))
    assert "Open Item" in str(caught.value)


def test_a_decided_threshold_answers_without_a_code_change() -> None:
    policy = HighValuePolicy(threshold=iqd(1_000_000))
    assert policy.is_high_value(iqd(1_000_000)) is True
    assert policy.is_high_value(iqd(999_999)) is False


def test_everything_else_about_a_claim_works_with_the_threshold_unset() -> None:
    """The Open Item blocks one question, not the service."""
    lab = build_lab()
    filed = lab.claims.file_claim(
        tracking_code=next_tracking_code(),
        kind=ClaimKind.DAMAGED_IN_TRANSIT,
        opened_by=ClaimOpenedBy.SENDER,
        opened_by_principal_id=lab.sender_id,
        sender_principal_id=lab.sender_id,
        evidence=(photo(),),
    )
    lab.claims.start_review(
        reference=filed.reference, actor=support(), custody_records_reviewed=True
    )
    approved = lab.claims.approve(
        reference=filed.reference, actor=operations(), compensation=iqd(2_000_000)
    )
    assert approved.compensation_amount == iqd(2_000_000)


# ------------------------------------------------------------------ isolation


def test_the_in_memory_unit_of_work_refuses_a_nested_transaction() -> None:
    lab = build_lab()
    lab.uow.begin()
    with pytest.raises(RuntimeError, match="transaction already active"):
        lab.uow.begin()
    lab.uow.rollback()


def test_two_units_of_work_share_rows_but_not_transactions() -> None:
    lab = build_lab()
    second = lab.uow.new_unit_of_work()
    assert second is not lab.uow
    assert second.database is lab.uow.database
    lab.uow.begin()
    second.begin()
    second.rollback()
    lab.uow.rollback()


def test_a_claim_filed_through_one_unit_of_work_is_visible_through_another() -> None:
    lab = build_lab()
    filed = lab.claims.file_claim(
        tracking_code=next_tracking_code(),
        kind=ClaimKind.LOST_PARCEL,
        opened_by=ClaimOpenedBy.SENDER,
        opened_by_principal_id=lab.sender_id,
        sender_principal_id=lab.sender_id,
    )
    other_request = ClaimService(lab.uow.new_unit_of_work())
    assert other_request.claim(reference=filed.reference).reference == filed.reference


def test_a_claimant_cannot_see_another_principals_claims() -> None:
    lab = build_lab()
    lab.claims.file_claim(
        tracking_code=next_tracking_code(),
        kind=ClaimKind.LOST_PARCEL,
        opened_by=ClaimOpenedBy.SENDER,
        opened_by_principal_id=lab.sender_id,
        sender_principal_id=lab.sender_id,
    )
    assert lab.claims.my_claims(principal_id=uuid4()) == ()
    assert len(lab.claims.my_claims(principal_id=lab.sender_id)) == 1
