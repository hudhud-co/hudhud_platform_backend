"""Profile completion, legal acceptance and notification preferences.

Evidence: Customer App v3 ``authName`` → ``authTerms`` → ``authNotify`` → ``authDone``.
"""

from __future__ import annotations

import pytest
from customer_fixtures import (
    BASE_TIME,
    PRINCIPAL,
    build_store,
    minutes,
    profile_service,
)

from customer.application.profile_service import (
    AcceptLegalCommand,
    LegalPolicy,
    UpsertProfileCommand,
)
from customer.domain.errors import (
    CustomerProfileNotFound,
    DisplayNameRequired,
    LegalDocumentNotAccepted,
)
from customer.domain.value_objects import (
    LegalDocumentKind,
    NotificationChannel,
    ProfileCompletionState,
)


def _accept_all(service, *, principal_id=PRINCIPAL, at=None):
    when = at or BASE_TIME
    for kind in LegalDocumentKind:
        service.accept_legal_document(
            AcceptLegalCommand(
                principal_id=principal_id,
                kind=kind,
                document_version="1.0",
                occurred_at=when,
            )
        )


def test_a_freshly_signed_in_principal_has_an_incomplete_profile_not_an_error():
    store = build_store()

    view = profile_service(store).ensure_profile(
        principal_id=PRINCIPAL, occurred_at=BASE_TIME
    )

    assert view.completion_state is ProfileCompletionState.INCOMPLETE
    assert view.can_use_the_app is False


def test_setting_a_display_name_completes_the_profile():
    store = build_store()
    service = profile_service(store)
    service.ensure_profile(principal_id=PRINCIPAL, occurred_at=BASE_TIME)

    view = service.set_display_name(
        UpsertProfileCommand(
            principal_id=PRINCIPAL,
            display_name="Layla Kadhim",
            occurred_at=BASE_TIME + minutes(1),
        )
    )

    assert view.completion_state is ProfileCompletionState.COMPLETE


def test_a_blank_display_name_is_refused():
    store = build_store()

    with pytest.raises(DisplayNameRequired):
        profile_service(store).set_display_name(
            UpsertProfileCommand(
                principal_id=PRINCIPAL, display_name="   ", occurred_at=BASE_TIME
            )
        )


def test_setting_the_same_name_twice_is_an_idempotent_replay():
    store = build_store()
    service = profile_service(store)
    command = UpsertProfileCommand(
        principal_id=PRINCIPAL, display_name="Layla", occurred_at=BASE_TIME
    )
    first = service.set_display_name(command)

    second = service.set_display_name(command)

    assert second.idempotent_replay is True
    assert second.profile.version == first.profile.version


def test_the_app_is_unusable_until_every_document_in_force_is_accepted():
    store = build_store()
    service = profile_service(store)
    service.set_display_name(
        UpsertProfileCommand(
            principal_id=PRINCIPAL, display_name="Layla", occurred_at=BASE_TIME
        )
    )

    before = service.read_profile(PRINCIPAL)
    _accept_all(service, at=BASE_TIME + minutes(1))
    after = service.read_profile(PRINCIPAL)

    assert before.can_use_the_app is False
    assert after.can_use_the_app is True
    assert after.outstanding_documents == ()


def test_accepting_a_version_that_is_not_in_force_does_not_satisfy_the_requirement():
    """A stale client must not be able to accept an old document forever."""
    store = build_store()
    service = profile_service(store, policy=LegalPolicy(terms_version="2.0"))

    with pytest.raises(LegalDocumentNotAccepted):
        service.accept_legal_document(
            AcceptLegalCommand(
                principal_id=PRINCIPAL,
                kind=LegalDocumentKind.TERMS_OF_SERVICE,
                document_version="1.0",
                occurred_at=BASE_TIME,
            )
        )


def test_publishing_new_terms_requires_a_fresh_acceptance():
    store = build_store()
    old = profile_service(store, policy=LegalPolicy(terms_version="1.0"))
    old.accept_legal_document(
        AcceptLegalCommand(
            principal_id=PRINCIPAL,
            kind=LegalDocumentKind.TERMS_OF_SERVICE,
            document_version="1.0",
            occurred_at=BASE_TIME,
        )
    )

    new = profile_service(store, policy=LegalPolicy(terms_version="2.0"))
    outstanding = new.read_profile(PRINCIPAL).outstanding_documents

    assert (LegalDocumentKind.TERMS_OF_SERVICE, "2.0") in outstanding


def test_accepting_the_same_version_twice_is_a_replay_not_a_second_record():
    store = build_store()
    service = profile_service(store)
    command = AcceptLegalCommand(
        principal_id=PRINCIPAL,
        kind=LegalDocumentKind.TERMS_OF_SERVICE,
        document_version="1.0",
        occurred_at=BASE_TIME,
    )
    service.accept_legal_document(command)

    replay = service.accept_legal_document(command)

    assert replay.idempotent_replay is True
    store.begin()
    records = store.legal_acceptances.list_for_principal(PRINCIPAL)
    store.rollback()
    assert len(records) == 1


def test_assert_legal_current_raises_while_a_document_is_outstanding():
    store = build_store()
    service = profile_service(store)
    service.ensure_profile(principal_id=PRINCIPAL, occurred_at=BASE_TIME)

    with pytest.raises(LegalDocumentNotAccepted):
        service.assert_legal_current(PRINCIPAL)


def test_notification_preferences_default_to_app_and_sms():
    store = build_store()

    view = profile_service(store).ensure_profile(
        principal_id=PRINCIPAL, occurred_at=BASE_TIME
    )

    assert view.profile.notification_channels == frozenset(
        {NotificationChannel.APP, NotificationChannel.SMS}
    )


def test_notification_preferences_can_be_narrowed():
    store = build_store()
    service = profile_service(store)
    service.ensure_profile(principal_id=PRINCIPAL, occurred_at=BASE_TIME)

    view = service.set_notification_channels(
        principal_id=PRINCIPAL,
        channels=frozenset({NotificationChannel.APP}),
        occurred_at=BASE_TIME + minutes(1),
    )

    assert view.profile.notification_channels == frozenset({NotificationChannel.APP})


def test_reading_an_unknown_profile_is_not_found():
    with pytest.raises(CustomerProfileNotFound):
        profile_service(build_store()).read_profile(PRINCIPAL)


def test_an_unknown_document_kind_is_refused():
    store = build_store()

    with pytest.raises(LegalDocumentNotAccepted):
        profile_service(store).accept_legal_document(
            AcceptLegalCommand(
                principal_id=PRINCIPAL,
                kind="COOKIE_POLICY",
                document_version="1.0",
                occurred_at=BASE_TIME,
            )
        )
