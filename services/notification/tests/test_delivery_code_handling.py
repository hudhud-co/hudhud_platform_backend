"""The delivery code: delivered, never kept.

v6.3 p.20 puts the delivery code in the acceptance SMS. That makes this service the one
place in the platform where the code is handled outside Identity and Delivery, so most of
these tests are about where the code is *not*.
"""

from __future__ import annotations

import dataclasses
import logging

import pytest
from notification_fixtures import (
    DELIVERY_CODE,
    RECEIVER_PHONE,
    TRACKING,
    acceptance_context,
    build_store,
    dispatch_service,
    fan_out_service,
    receiver_ref,
    recording_transports,
)

from notification.application.dispatch_service import DispatchService
from notification.domain.entities import Notification
from notification.domain.errors import (
    DeliveryCodeMustNotBeStored,
    TransportUnavailable,
)
from notification.domain.policy import TPL_ACCEPTED_RECEIVER_SMS
from notification.domain.value_objects import Category, Channel, DeliveryStatus
from notification.infrastructure.transports import (
    ConsoleTransport,
    UnavailableTransport,
)
from notification.ports.transport import OutboundMessage


def _planned_sms(uow) -> Notification:
    result = fan_out_service(uow).fan_out(
        category=Category.ACCEPTANCE,
        trigger_reference="pickup-accepted-1",
        recipients=(receiver_ref(),),
        tracking_code=TRACKING,
        context=acceptance_context(),
    )
    return next(
        item for item in result.notifications if item.channel is Channel.SMS
    )


# ------------------------------------------------------------------ delivery


def test_the_code_reaches_the_receiver_in_the_sms() -> None:
    uow = build_store()
    notification = _planned_sms(uow)
    transports = recording_transports()

    dispatch_service(uow, transports).dispatch(
        notification_id=notification.notification_id,
        recipient_address=RECEIVER_PHONE,
        delivery_code=DELIVERY_CODE,
    )

    sent = transports[Channel.SMS].sent[0]
    assert DELIVERY_CODE in sent.body
    assert TRACKING in sent.body


def test_the_sms_also_carries_a_tracking_link() -> None:
    """v6.3 p.20 — "It contains the OTP and a link to track the parcel"."""
    uow = build_store()
    notification = _planned_sms(uow)
    transports = recording_transports()

    dispatch_service(uow, transports).dispatch(
        notification_id=notification.notification_id,
        recipient_address=RECEIVER_PHONE,
        delivery_code=DELIVERY_CODE,
    )

    assert "track.hudhud.iq" in transports[Channel.SMS].sent[0].body


# ------------------------------------------------------------------ not stored


def test_the_notification_record_has_nowhere_to_put_a_code() -> None:
    """The strongest guarantee available: the field does not exist."""
    names = {field.name for field in dataclasses.fields(Notification)}
    # `tracking_code` is public — it is the code printed on the label and texted in the
    # link. `template_code` names a template. Neither is a secret.
    code_fields = {name for name in names if "code" in name} - {
        "tracking_code",
        "template_code",
    }

    assert code_fields == set()
    assert "delivery_code" not in names
    assert "otp" not in names
    # No rendered body either: a stored body would be a second place the code lives.
    assert "body" not in names


def test_the_code_is_not_in_the_stored_notification_after_sending() -> None:
    uow = build_store()
    notification = _planned_sms(uow)

    dispatch_service(uow).dispatch(
        notification_id=notification.notification_id,
        recipient_address=RECEIVER_PHONE,
        delivery_code=DELIVERY_CODE,
    )

    uow.begin()
    stored = uow.notifications.get(notification.notification_id)
    uow.commit()
    assert DELIVERY_CODE not in str(stored)


def test_the_code_is_not_in_any_stored_row_anywhere() -> None:
    uow = build_store()
    notification = _planned_sms(uow)
    dispatch_service(uow).dispatch(
        notification_id=notification.notification_id,
        recipient_address=RECEIVER_PHONE,
        delivery_code=DELIVERY_CODE,
    )

    uow.begin()
    everything = str(
        (
            uow.notifications.list_for_tracking_code(TRACKING),
            uow.centre.list_for_principal(notification.principal_id)
            if notification.principal_id
            else (),
            uow.recipients.get(notification.recipient_key),
        )
    )
    uow.commit()
    assert DELIVERY_CODE not in everything


def test_smuggling_a_code_through_the_context_is_refused() -> None:
    """The one route by which a code could reach a stored field."""
    uow = build_store()
    result = fan_out_service(uow).fan_out(
        category=Category.ACCEPTANCE,
        trigger_reference="pickup-accepted-1",
        recipients=(receiver_ref(),),
        tracking_code=TRACKING,
        context={
            **acceptance_context(),
            "note": f"the code is {DELIVERY_CODE}",
        },
    )
    notification = next(
        item for item in result.notifications if item.channel is Channel.SMS
    )

    with pytest.raises(DeliveryCodeMustNotBeStored) as caught:
        dispatch_service(uow).dispatch(
            notification_id=notification.notification_id,
            recipient_address=RECEIVER_PHONE,
            delivery_code=DELIVERY_CODE,
        )

    assert caught.value.field_name == "context.note"


# ------------------------------------------------------------------ not logged


def test_a_redacted_message_reveals_neither_recipient_nor_body() -> None:
    message = OutboundMessage(
        channel=Channel.SMS,
        recipient=RECEIVER_PHONE,
        body=f"Your delivery code is {DELIVERY_CODE}",
        delivery_code=DELIVERY_CODE,
    )

    redacted = message.redacted()

    assert DELIVERY_CODE not in redacted
    assert RECEIVER_PHONE not in redacted


def test_the_console_transport_never_prints_a_code(caplog) -> None:
    """A development aid that leaked codes into scrollback would be worse than none."""
    transport = ConsoleTransport(Channel.SMS)
    message = OutboundMessage(
        channel=Channel.SMS,
        recipient=RECEIVER_PHONE,
        body=f"Your delivery code is {DELIVERY_CODE}",
        delivery_code=DELIVERY_CODE,
    )

    with caplog.at_level(logging.INFO):
        transport.send(message)

    assert DELIVERY_CODE not in caplog.text
    assert RECEIVER_PHONE not in caplog.text


def test_the_console_transport_is_never_production_ready() -> None:
    assert ConsoleTransport(Channel.SMS).is_production_ready is False


# ------------------------------------------------------------------ transports


def test_a_channel_with_no_transport_refuses_rather_than_pretending() -> None:
    """Marking something sent that never left is how a receiver loses their parcel."""
    uow = build_store()
    notification = _planned_sms(uow)

    with pytest.raises(TransportUnavailable):
        DispatchService(
            uow,
            transports={Channel.SMS: UnavailableTransport(Channel.SMS)},
            templates={TPL_ACCEPTED_RECEIVER_SMS: "{delivery_code}"},
        ).dispatch(
            notification_id=notification.notification_id,
            recipient_address=RECEIVER_PHONE,
            delivery_code=DELIVERY_CODE,
        )


def test_a_failed_send_is_recorded_as_failed_not_sent() -> None:
    uow = build_store()
    notification = _planned_sms(uow)

    with pytest.raises(TransportUnavailable):
        DispatchService(
            uow,
            transports={Channel.SMS: UnavailableTransport(Channel.SMS)},
            templates={TPL_ACCEPTED_RECEIVER_SMS: "{delivery_code}"},
        ).dispatch(
            notification_id=notification.notification_id,
            recipient_address=RECEIVER_PHONE,
            delivery_code=DELIVERY_CODE,
        )

    uow.begin()
    stored = uow.notifications.get(notification.notification_id)
    uow.commit()
    assert stored.status is DeliveryStatus.FAILED
    assert stored.failure_reason == "transport_unavailable"


def test_a_rejected_send_is_recorded_with_the_providers_reason() -> None:
    uow = build_store()
    notification = _planned_sms(uow)
    transports = recording_transports(accept=False)

    outcome = dispatch_service(uow, transports).dispatch(
        notification_id=notification.notification_id,
        recipient_address=RECEIVER_PHONE,
        delivery_code=DELIVERY_CODE,
    )

    assert outcome.sent is False
    assert outcome.notification.status is DeliveryStatus.FAILED
    assert outcome.reason == "provider_rejected"


def test_a_sent_notification_is_never_sent_again() -> None:
    uow = build_store()
    notification = _planned_sms(uow)
    transports = recording_transports()
    service = dispatch_service(uow, transports)
    service.dispatch(
        notification_id=notification.notification_id,
        recipient_address=RECEIVER_PHONE,
        delivery_code=DELIVERY_CODE,
    )

    outcome = service.dispatch(
        notification_id=notification.notification_id,
        recipient_address=RECEIVER_PHONE,
        delivery_code=DELIVERY_CODE,
    )

    assert outcome.sent is False
    assert outcome.reason == "already_sent"
    assert len(transports[Channel.SMS].sent) == 1


def test_a_failed_notification_can_be_retried() -> None:
    uow = build_store()
    notification = _planned_sms(uow)
    rejecting = recording_transports(accept=False)
    dispatch_service(uow, rejecting).dispatch(
        notification_id=notification.notification_id,
        recipient_address=RECEIVER_PHONE,
        delivery_code=DELIVERY_CODE,
    )

    accepting = recording_transports()
    outcome = dispatch_service(uow, accepting).dispatch(
        notification_id=notification.notification_id,
        recipient_address=RECEIVER_PHONE,
        delivery_code=DELIVERY_CODE,
    )

    assert outcome.sent is True
    assert outcome.notification.attempt_count == 2


# ------------------------------------------------------------------ recipients


def test_a_recipient_is_stored_as_a_keyed_hash_not_a_phone_number() -> None:
    uow = build_store()
    notification = _planned_sms(uow)

    assert RECEIVER_PHONE not in notification.recipient_key
    assert "2222222" not in notification.recipient_key
    assert len(notification.recipient_key) == 64


def test_the_same_number_always_hashes_to_the_same_key() -> None:
    """Otherwise deduplication across two deliveries of one fact would not work."""
    uow = build_store()
    first = _planned_sms(uow)
    second_uow = build_store()
    second = _planned_sms(second_uow)

    assert first.recipient_key == second.recipient_key


def test_only_the_last_four_digits_are_kept_for_display() -> None:
    uow = build_store()
    notification = _planned_sms(uow)

    uow.begin()
    profile = uow.recipients.get(notification.recipient_key)
    uow.commit()
    assert profile.phone_last4 == "2222"
    assert RECEIVER_PHONE not in str(profile)
