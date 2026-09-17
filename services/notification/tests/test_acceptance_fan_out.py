"""The acceptance fan-out (NTF-01 … NTF-06)."""

from __future__ import annotations

import pytest
from notification_fixtures import (
    RECEIVER,
    TRACKING,
    acceptance_context,
    build_store,
    fan_out_service,
    merchant_ref,
    preference_service,
    receiver_ref,
    sender_ref,
)

from notification.domain.errors import InvalidPhoneNumber
from notification.domain.policy import (
    TPL_ACCEPTED_RECEIVER_APP,
    TPL_ACCEPTED_RECEIVER_SMS,
    TPL_ACCEPTED_RECEIVER_WHATSAPP,
    TPL_ACCEPTED_SENDER_APP,
)
from notification.domain.value_objects import (
    Audience,
    Category,
    Channel,
    DeliveryStatus,
)


def _accept(uow, *, recipients=None, trigger="pickup-accepted-1"):
    return fan_out_service(uow).fan_out(
        category=Category.ACCEPTANCE,
        trigger_reference=trigger,
        recipients=recipients or (sender_ref(), receiver_ref()),
        tracking_code=TRACKING,
        context=acceptance_context(),
    )


# ------------------------------------------------------------------ NTF-02/03


def test_the_sender_gets_an_app_notification_confirming_acceptance() -> None:
    """v6.3 p.20."""
    uow = build_store()

    result = _accept(uow)

    assert (Audience.SENDER, Channel.APP, TPL_ACCEPTED_SENDER_APP) in result.plan.planned


def test_the_receiver_always_gets_an_sms() -> None:
    """v6.3 p.20 — "The receiver always gets an SMS"."""
    uow = build_store()

    result = _accept(uow)

    assert (
        Audience.RECEIVER,
        Channel.SMS,
        TPL_ACCEPTED_RECEIVER_SMS,
    ) in result.plan.planned


def test_the_receiver_gets_the_sms_even_with_no_account_and_no_app() -> None:
    """The default case: v6.3 texts a receiver before they have ever opened the app."""
    uow = build_store()

    result = _accept(uow, recipients=(receiver_ref(),))

    assert result.plan.channels == {Channel.SMS}


# ------------------------------------------------------------------ NTF-04


def test_the_receiver_also_gets_an_app_notification_if_the_app_is_installed() -> None:
    uow = build_store()

    result = _accept(
        uow, recipients=(receiver_ref(principal_id=RECEIVER, app_installed=True),)
    )

    assert Channel.APP in result.plan.channels
    assert (
        Audience.RECEIVER,
        Channel.APP,
        TPL_ACCEPTED_RECEIVER_APP,
    ) in result.plan.planned


def test_no_app_notification_when_the_app_is_not_installed() -> None:
    uow = build_store()

    result = _accept(uow, recipients=(receiver_ref(app_installed=False),))

    assert Channel.APP not in result.plan.channels
    assert any(channel is Channel.APP for _, channel, _ in result.plan.unreachable)


# ------------------------------------------------------------------ NTF-05


def test_whatsapp_is_used_when_the_number_has_an_account() -> None:
    uow = build_store()

    result = _accept(uow, recipients=(receiver_ref(whatsapp_available=True),))

    assert (
        Audience.RECEIVER,
        Channel.WHATSAPP,
        TPL_ACCEPTED_RECEIVER_WHATSAPP,
    ) in result.plan.planned


def test_no_whatsapp_when_the_number_has_no_account() -> None:
    uow = build_store()

    result = _accept(uow, recipients=(receiver_ref(whatsapp_available=False),))

    assert Channel.WHATSAPP not in result.plan.channels


def test_all_four_acceptance_messages_go_out_when_everything_is_reachable() -> None:
    uow = build_store()

    result = _accept(
        uow,
        recipients=(
            sender_ref(),
            receiver_ref(
                principal_id=RECEIVER, app_installed=True, whatsapp_available=True
            ),
        ),
    )

    assert result.plan.channels == {Channel.APP, Channel.SMS, Channel.WHATSAPP}
    assert len(result.notifications) == 4


# ------------------------------------------------------------------ idempotency


def test_the_same_acceptance_fact_replayed_sends_nothing_twice() -> None:
    """At-least-once delivery must not become at-least-once SMS."""
    uow = build_store()
    first = _accept(uow)

    second = _accept(uow)

    assert len(first.notifications) == 2
    assert second.notifications == ()
    assert second.replayed is True
    assert len(uow.as_committed().notifications.list_pending()) == 2


def test_two_different_facts_produce_their_own_messages() -> None:
    uow = build_store()
    _accept(uow, trigger="pickup-accepted-1")

    second = _accept(uow, trigger="pickup-accepted-2")

    assert len(second.notifications) == 2


def test_every_planned_message_starts_pending_and_is_not_sent_here() -> None:
    """A transport outage must never roll back the fact that a parcel was accepted."""
    uow = build_store()

    result = _accept(uow)

    assert all(
        notification.status is DeliveryStatus.PENDING
        for notification in result.notifications
    )


# ------------------------------------------------------------------ atomicity


def test_a_bad_phone_number_writes_nothing_at_all() -> None:
    uow = build_store()

    with pytest.raises(InvalidPhoneNumber):
        _accept(uow, recipients=(sender_ref(), receiver_ref(phone="12")))

    assert uow.as_committed().notifications.list_pending() == ()


# ------------------------------------------------------------------ centre


def test_the_app_copy_appears_in_the_notification_centre() -> None:
    uow = build_store()

    _accept(uow)

    entries = preference_service(uow).list_centre(principal_id=sender_ref().principal_id)
    assert len(entries) == 1
    assert entries[0].tracking_code == TRACKING


def test_a_receiver_without_an_account_gets_no_centre_entry() -> None:
    """There is nowhere to read it, and inventing an account would be wrong."""
    uow = build_store()

    result = _accept(uow, recipients=(receiver_ref(),))

    assert result.centre_entries == ()


def test_an_sms_alone_never_creates_a_centre_entry() -> None:
    uow = build_store()

    result = _accept(
        uow, recipients=(receiver_ref(principal_id=RECEIVER, app_installed=False),)
    )

    assert result.centre_entries == ()


# ------------------------------------------------------------------ audience


def test_a_merchant_is_not_part_of_the_acceptance_fan_out() -> None:
    """v6.3 p.20 names the sender and the receiver; the merchant hears at delivery (p.14)."""
    uow = build_store()

    result = _accept(uow, recipients=(merchant_ref(),))

    assert result.plan.planned == ()
