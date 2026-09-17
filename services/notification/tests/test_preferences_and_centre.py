"""Per-channel preferences, the notification centre and the rest of the fan-outs.

Covers NTF-07 (pre-delivery), NTF-08 (intact-seal confirmation), NTF-09 (driver
bulletins) and NTF-10 / CUS-14 (preferences and the centre).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from notification_fixtures import (
    DRIVER,
    MERCHANT,
    RECEIVER,
    TRACKING,
    acceptance_context,
    build_store,
    fan_out_service,
    merchant_ref,
    new_id,
    preference_service,
    receiver_ref,
    sender_ref,
)

from notification.application.dispatch_service import CODE_BEARING_TEMPLATES
from notification.application.fan_out_service import RecipientRef
from notification.domain.entities import ChannelPreference
from notification.domain.errors import (
    CentreEntryNotFound,
    MandatoryNotificationCannotBeDisabled,
)
from notification.domain.policy import (
    TPL_PRE_DELIVERY_RECEIVER_APP,
    TPL_PRE_DELIVERY_RECEIVER_CALL,
    TPL_PRE_DELIVERY_RECEIVER_SMS,
    TPL_SEAL_INTACT_MERCHANT_APP,
)
from notification.domain.value_objects import (
    APP_INSTALL_ENCOURAGEMENT,
    Audience,
    Category,
    Channel,
    DeliveryStatus,
)

# ------------------------------------------------------------------ NTF-07


def test_the_pre_delivery_fan_out_uses_three_channels_at_once() -> None:
    """v6.3 p.26 — "you are notified three ways at once"."""
    uow = build_store()

    result = fan_out_service(uow).fan_out(
        category=Category.PRE_DELIVERY,
        trigger_reference="setting-off-1",
        recipients=(
            receiver_ref(principal_id=RECEIVER, app_installed=True),
        ),
        tracking_code=TRACKING,
        context={"eta": "16:00 – 20:00"},
    )

    assert result.plan.channels == {Channel.APP, Channel.SMS, Channel.PHONE_CALL}
    for template in (
        TPL_PRE_DELIVERY_RECEIVER_APP,
        TPL_PRE_DELIVERY_RECEIVER_SMS,
        TPL_PRE_DELIVERY_RECEIVER_CALL,
    ):
        assert any(code == template for _, _, code in result.plan.planned)


def test_the_phone_call_still_happens_without_the_app() -> None:
    """A call reaches anyone with a phone; it is not conditional on an install."""
    uow = build_store()

    result = fan_out_service(uow).fan_out(
        category=Category.PRE_DELIVERY,
        trigger_reference="setting-off-1",
        recipients=(receiver_ref(),),
        tracking_code=TRACKING,
        context={"eta": "16:00 – 20:00"},
    )

    assert Channel.PHONE_CALL in result.plan.channels
    assert Channel.APP not in result.plan.channels


def test_the_pre_delivery_message_carries_no_delivery_code() -> None:
    """The code went out at acceptance; repeating it widens its exposure."""
    assert TPL_PRE_DELIVERY_RECEIVER_SMS not in CODE_BEARING_TEMPLATES
    assert TPL_PRE_DELIVERY_RECEIVER_APP not in CODE_BEARING_TEMPLATES


# ------------------------------------------------------------------ NTF-08


def test_the_merchant_is_told_when_the_seal_arrived_intact() -> None:
    """v6.3 p.14 — an intact-seal confirmation is sent to the merchant."""
    uow = build_store()

    result = fan_out_service(uow).fan_out(
        category=Category.SEAL_CONFIRMATION,
        trigger_reference="delivered-1",
        recipients=(merchant_ref(),),
        tracking_code=TRACKING,
        context={"summary": "Seal intact on delivery."},
    )

    assert (
        Audience.MERCHANT,
        Channel.APP,
        TPL_SEAL_INTACT_MERCHANT_APP,
    ) in result.plan.planned


def test_the_seal_confirmation_does_not_go_to_the_receiver() -> None:
    uow = build_store()

    result = fan_out_service(uow).fan_out(
        category=Category.SEAL_CONFIRMATION,
        trigger_reference="delivered-1",
        recipients=(receiver_ref(principal_id=RECEIVER, app_installed=True),),
        tracking_code=TRACKING,
    )

    assert result.plan.planned == ()


# ------------------------------------------------------------------ NTF-09


def test_operations_can_reach_a_driver_in_their_notification_centre() -> None:
    uow = build_store()

    result = fan_out_service(uow).fan_out(
        category=Category.OPERATIONS_BULLETIN,
        trigger_reference="bulletin-1",
        recipients=(
            RecipientRef(
                phone="+9647704444444",
                audience=Audience.DRIVER,
                principal_id=DRIVER,
                app_installed=True,
            ),
        ),
        context={"summary": "Hub cut-off moves to 19:00 tonight."},
    )

    assert Channel.APP in result.plan.channels
    entries = preference_service(uow).list_centre(principal_id=DRIVER)
    assert len(entries) == 1
    assert entries[0].body == "Hub cut-off moves to 19:00 tonight."


# ------------------------------------------------------------------ NTF-10


def test_a_receiver_can_switch_off_a_non_mandatory_channel() -> None:
    uow = build_store()
    preference_service(uow).set_preference(
        principal_id=RECEIVER,
        category=Category.PRE_DELIVERY,
        channel=Channel.APP,
        enabled=False,
    )

    result = fan_out_service(uow).fan_out(
        category=Category.PRE_DELIVERY,
        trigger_reference="setting-off-1",
        recipients=(receiver_ref(principal_id=RECEIVER, app_installed=True),),
        tracking_code=TRACKING,
        context={"eta": "16:00 – 20:00"},
    )

    assert Channel.APP not in result.plan.channels
    assert any(channel is Channel.APP for _, channel, _ in result.plan.suppressed)
    assert Channel.SMS in result.plan.channels


def test_the_acceptance_sms_can_never_be_switched_off() -> None:
    """NTF-03 — it carries the delivery code (v6.3 p.20)."""
    uow = build_store()

    with pytest.raises(MandatoryNotificationCannotBeDisabled) as caught:
        preference_service(uow).set_preference(
            principal_id=RECEIVER,
            category=Category.ACCEPTANCE,
            channel=Channel.SMS,
            enabled=False,
            audience=Audience.RECEIVER,
        )

    assert caught.value.category == "ACCEPTANCE"
    assert caught.value.channel == "SMS"


def test_even_a_stored_opt_out_cannot_suppress_the_acceptance_sms() -> None:
    """Defence in depth: the fan-out does not consult preferences for it at all."""
    uow = build_store()
    # Write the opt-out directly, bypassing the service that would refuse it.
    uow.begin()
    uow.preferences.save(
        ChannelPreference(
            preference_id=uuid4(),
            principal_id=RECEIVER,
            category=Category.ACCEPTANCE,
            channel=Channel.SMS,
            enabled=False,
        )
    )
    uow.commit()

    result = fan_out_service(uow).fan_out(
        category=Category.ACCEPTANCE,
        trigger_reference="pickup-accepted-1",
        recipients=(receiver_ref(principal_id=RECEIVER),),
        tracking_code=TRACKING,
        context=acceptance_context(),
    )

    assert Channel.SMS in result.plan.channels


def test_a_suppressed_message_is_recorded_so_its_absence_is_explainable() -> None:
    uow = build_store()
    preference_service(uow).set_preference(
        principal_id=RECEIVER,
        category=Category.PRE_DELIVERY,
        channel=Channel.APP,
        enabled=False,
    )
    fan_out_service(uow).fan_out(
        category=Category.PRE_DELIVERY,
        trigger_reference="setting-off-1",
        recipients=(receiver_ref(principal_id=RECEIVER, app_installed=True),),
        tracking_code=TRACKING,
        context={"eta": "16:00"},
    )

    uow.begin()
    stored = uow.notifications.list_for_tracking_code(TRACKING)
    uow.commit()
    suppressed = [
        item for item in stored if item.status is DeliveryStatus.SUPPRESSED
    ]
    assert len(suppressed) == 1
    assert suppressed[0].channel is Channel.APP


def test_a_preference_can_be_switched_back_on() -> None:
    uow = build_store()
    service = preference_service(uow)
    service.set_preference(
        principal_id=RECEIVER,
        category=Category.PRE_DELIVERY,
        channel=Channel.APP,
        enabled=False,
    )

    preference = service.set_preference(
        principal_id=RECEIVER,
        category=Category.PRE_DELIVERY,
        channel=Channel.APP,
        enabled=True,
    )

    assert preference.enabled is True


def test_preferences_are_listed_per_principal() -> None:
    uow = build_store()
    service = preference_service(uow)
    service.set_preference(
        principal_id=RECEIVER,
        category=Category.PRE_DELIVERY,
        channel=Channel.APP,
        enabled=False,
    )
    service.set_preference(
        principal_id=MERCHANT,
        category=Category.STATUS_CHANGE,
        channel=Channel.APP,
        enabled=False,
    )

    assert len(service.list_preferences(principal_id=RECEIVER)) == 1


# ------------------------------------------------------------------ CUS-14


def test_the_centre_lists_newest_first_and_counts_unread() -> None:
    uow = build_store()
    service = fan_out_service(uow)
    service.fan_out(
        category=Category.ACCEPTANCE,
        trigger_reference="accepted-1",
        recipients=(sender_ref(),),
        tracking_code=TRACKING,
        context=acceptance_context(),
    )
    service.fan_out(
        category=Category.ACCEPTANCE,
        trigger_reference="accepted-2",
        recipients=(sender_ref(),),
        tracking_code="SHP-20260914-000202",
        context=acceptance_context(),
    )

    preferences = preference_service(uow)
    assert len(preferences.list_centre(principal_id=sender_ref().principal_id)) == 2
    assert preferences.unread_count(principal_id=sender_ref().principal_id) == 2


def test_marking_an_entry_read_reduces_the_unread_count() -> None:
    uow = build_store()
    result = fan_out_service(uow).fan_out(
        category=Category.ACCEPTANCE,
        trigger_reference="accepted-1",
        recipients=(sender_ref(),),
        tracking_code=TRACKING,
        context=acceptance_context(),
    )
    entry = result.centre_entries[0]
    service = preference_service(uow)

    service.mark_read(entry_id=entry.entry_id, principal_id=entry.principal_id)

    assert service.unread_count(principal_id=entry.principal_id) == 0


def test_someone_elses_centre_entry_answers_not_found() -> None:
    """404 rather than 403 — confirming the id exists would leak their history."""
    uow = build_store()
    result = fan_out_service(uow).fan_out(
        category=Category.ACCEPTANCE,
        trigger_reference="accepted-1",
        recipients=(sender_ref(),),
        tracking_code=TRACKING,
        context=acceptance_context(),
    )
    entry = result.centre_entries[0]

    with pytest.raises(CentreEntryNotFound):
        preference_service(uow).mark_read(
            entry_id=entry.entry_id, principal_id=new_id()
        )


def test_marking_an_entry_read_twice_is_harmless() -> None:
    uow = build_store()
    result = fan_out_service(uow).fan_out(
        category=Category.ACCEPTANCE,
        trigger_reference="accepted-1",
        recipients=(sender_ref(),),
        tracking_code=TRACKING,
        context=acceptance_context(),
    )
    entry = result.centre_entries[0]
    service = preference_service(uow)
    first = service.mark_read(entry_id=entry.entry_id, principal_id=entry.principal_id)

    second = service.mark_read(entry_id=entry.entry_id, principal_id=entry.principal_id)

    assert second.read_at == first.read_at


# ------------------------------------------------------------------ NTF-06


def test_the_install_encouragement_is_one_shared_wording() -> None:
    """v6.3 p.20 — WhatsApp and the tracking website show "the same encouragement"."""
    assert "time window" in APP_INSTALL_ENCOURAGEMENT
    assert "exact address" in APP_INSTALL_ENCOURAGEMENT
