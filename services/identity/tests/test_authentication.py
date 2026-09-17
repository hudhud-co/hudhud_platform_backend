"""OTP sign-in: secrecy, expiry, attempt limits, rate limiting and session minting.

Evidence: Customer App v3 ``authPhone``/``authOtp``; Driver App v8 ``login``/``authOtp``
("OTP sign-in, then a secure session bound to your registered device").
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from identity_fixtures import (
    BASE_TIME,
    OTHER_PHONE,
    PHONE,
    SIGNING_KEY,
    auth_service,
    build_delivery,
    build_store,
    minutes,
    sign_in,
)

from identity.application.authentication_service import (
    OtpPolicy,
    RequestOtpCommand,
    VerifyOtpCommand,
)
from identity.domain.errors import (
    InvalidPhoneNumber,
    OtpRateLimited,
    OtpVerificationFailed,
)
from identity.domain.security import hash_otp_code, hash_phone, normalize_phone
from identity.domain.value_objects import PrincipalStatus, Role


def _request(service, *, phone=PHONE, at=None):
    return service.request_code(RequestOtpCommand(phone=phone, occurred_at=at or BASE_TIME))


# ------------------------------------------------------------ phone handling


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+964 770 182 0934", "+9647701820934"),
        ("00964 770 182 0934", "+9647701820934"),
        ("+9647701820934", "+9647701820934"),
    ],
)
def test_the_same_person_typing_their_number_differently_is_one_identity(raw, expected):
    assert normalize_phone(raw) == expected


def test_a_number_without_a_country_code_is_refused_rather_than_guessed():
    store, delivery = build_store(), build_delivery()
    with pytest.raises(InvalidPhoneNumber):
        _request(auth_service(store, delivery), phone="770 182 0934")


def test_two_spellings_of_one_number_do_not_create_two_principals():
    store, delivery = build_store(), build_delivery()
    service = auth_service(store, delivery)

    first = _request(service, phone="+964 770 182 0934")
    second = _request(service, phone="00964-770-182-0934", at=BASE_TIME + minutes(1))

    assert first.principal_created is True
    assert second.principal_created is False


# ----------------------------------------------------------------- secrecy


def test_the_code_is_never_stored_in_recoverable_form():
    store, delivery = build_store(), build_delivery()
    issued = _request(auth_service(store, delivery))
    code = delivery.code_for(str(issued.challenge_id))

    store.begin()
    challenge = store.otp_challenges.get(issued.challenge_id)
    store.rollback()

    assert challenge is not None
    assert code not in challenge.code_hash
    assert challenge.code_hash != code


def test_the_phone_number_is_never_stored_in_recoverable_form():
    store, delivery = build_store(), build_delivery()
    _request(auth_service(store, delivery))

    store.begin()
    principal = store.principals.find_by_phone_hash(
        hash_phone(PHONE, signing_key=SIGNING_KEY)
    )
    store.rollback()

    assert principal is not None
    assert PHONE not in principal.phone_hash
    assert principal.phone_last4 == "0934"


def test_the_session_token_is_never_stored_in_recoverable_form():
    store, delivery = build_store(), build_delivery()
    session = sign_in(store, delivery)

    store.begin()
    record = store.sessions.get(session.session_id)
    store.rollback()

    assert record is not None
    assert session.token not in record.token_hash


def test_a_code_hash_from_one_challenge_cannot_authenticate_another():
    """Hashes are bound to their challenge, so a stolen hash is not portable."""
    store, delivery = build_store(), build_delivery()
    service = auth_service(store, delivery)
    first = _request(service)
    second = _request(service, phone=OTHER_PHONE, at=BASE_TIME + minutes(1))
    first_code = delivery.code_for(str(first.challenge_id))

    store.begin()
    other = store.otp_challenges.get(second.challenge_id)
    store.rollback()

    assert (
        hash_otp_code(
            first_code, challenge_id=str(second.challenge_id), signing_key=SIGNING_KEY
        )
        != other.code_hash
        or first_code == delivery.code_for(str(second.challenge_id))
    )


# ------------------------------------------------------------- happy path


def test_a_correct_code_mints_a_session_with_the_self_service_role():
    store, delivery = build_store(), build_delivery()

    session = sign_in(store, delivery)

    assert session.token
    assert session.status is PrincipalStatus.ACTIVE
    assert session.roles == (Role.CUSTOMER,)


def test_signing_up_never_grants_a_privileged_role():
    store, delivery = build_store(), build_delivery()

    session = sign_in(store, delivery)

    assert Role.OPERATIONS not in session.roles
    assert Role.PICKUP_DRIVER not in session.roles


# ----------------------------------------------------------------- failure


def test_a_wrong_code_is_rejected():
    store, delivery = build_store(), build_delivery()
    service = auth_service(store, delivery)
    issued = _request(service)
    wrong = "000000" if delivery.code_for(str(issued.challenge_id)) != "000000" else "111111"

    with pytest.raises(OtpVerificationFailed):
        service.verify_code(
            VerifyOtpCommand(
                challenge_id=issued.challenge_id,
                code=wrong,
                occurred_at=BASE_TIME + minutes(1),
            )
        )


def test_a_failed_attempt_is_recorded_even_though_the_request_failed():
    """The counter is the brute-force defence; rolling it back would disable the limit."""
    store, delivery = build_store(), build_delivery()
    service = auth_service(store, delivery)
    issued = _request(service)

    with pytest.raises(OtpVerificationFailed):
        service.verify_code(
            VerifyOtpCommand(
                challenge_id=issued.challenge_id,
                code="999999",
                occurred_at=BASE_TIME + minutes(1),
            )
        )

    store.begin()
    challenge = store.otp_challenges.get(issued.challenge_id)
    store.rollback()
    assert challenge.failed_attempts == 1


def test_the_code_stops_working_after_the_attempt_limit():
    store, delivery = build_store(), build_delivery()
    service = auth_service(store, delivery, policy=OtpPolicy(max_attempts=3))
    issued = _request(service)
    correct = delivery.code_for(str(issued.challenge_id))

    for index in range(3):
        with pytest.raises(OtpVerificationFailed):
            service.verify_code(
                VerifyOtpCommand(
                    challenge_id=issued.challenge_id,
                    code="000000" if correct != "000000" else "111111",
                    occurred_at=BASE_TIME + minutes(index + 1),
                )
            )

    # Even the correct code no longer works once the challenge is burned.
    with pytest.raises(OtpVerificationFailed):
        service.verify_code(
            VerifyOtpCommand(
                challenge_id=issued.challenge_id,
                code=correct,
                occurred_at=BASE_TIME + minutes(5),
            )
        )


def test_an_expired_code_is_rejected():
    store, delivery = build_store(), build_delivery()
    service = auth_service(store, delivery, policy=OtpPolicy(code_ttl_seconds=60))
    issued = _request(service)
    correct = delivery.code_for(str(issued.challenge_id))

    with pytest.raises(OtpVerificationFailed):
        service.verify_code(
            VerifyOtpCommand(
                challenge_id=issued.challenge_id,
                code=correct,
                occurred_at=BASE_TIME + minutes(5),
            )
        )


def test_a_code_works_exactly_once():
    store, delivery = build_store(), build_delivery()
    service = auth_service(store, delivery)
    issued = _request(service)
    correct = delivery.code_for(str(issued.challenge_id))
    service.verify_code(
        VerifyOtpCommand(
            challenge_id=issued.challenge_id,
            code=correct,
            occurred_at=BASE_TIME + minutes(1),
        )
    )

    with pytest.raises(OtpVerificationFailed):
        service.verify_code(
            VerifyOtpCommand(
                challenge_id=issued.challenge_id,
                code=correct,
                occurred_at=BASE_TIME + minutes(2),
            )
        )


def test_requesting_a_new_code_invalidates_the_previous_one():
    """Two live codes for one phone would double the attacker's guessing budget."""
    store, delivery = build_store(), build_delivery()
    service = auth_service(store, delivery)
    first = _request(service)
    first_code = delivery.code_for(str(first.challenge_id))
    _request(service, at=BASE_TIME + minutes(1))

    with pytest.raises(OtpVerificationFailed):
        service.verify_code(
            VerifyOtpCommand(
                challenge_id=first.challenge_id,
                code=first_code,
                occurred_at=BASE_TIME + minutes(2),
            )
        )


def test_an_unknown_challenge_fails_the_same_way_as_a_wrong_code():
    store, delivery = build_store(), build_delivery()
    service = auth_service(store, delivery)

    with pytest.raises(OtpVerificationFailed):
        service.verify_code(
            VerifyOtpCommand(
                challenge_id=uuid4(), code="123456", occurred_at=BASE_TIME
            )
        )


def test_a_suspended_principal_cannot_complete_a_sign_in():
    store, delivery = build_store(), build_delivery()
    session = sign_in(store, delivery)
    store.begin()
    principal = store.principals.get(session.principal_id)
    principal.status = PrincipalStatus.SUSPENDED
    store.principals.save(principal)
    store.commit()

    service = auth_service(store, delivery)
    issued = _request(service, at=BASE_TIME + minutes(10))
    code = delivery.code_for(str(issued.challenge_id))

    with pytest.raises(OtpVerificationFailed):
        service.verify_code(
            VerifyOtpCommand(
                challenge_id=issued.challenge_id,
                code=code,
                occurred_at=BASE_TIME + minutes(11),
            )
        )


# -------------------------------------------------------------- rate limit


def test_a_phone_cannot_request_unlimited_codes():
    store, delivery = build_store(), build_delivery()
    service = auth_service(store, delivery, policy=OtpPolicy(max_codes_per_window=3))
    for index in range(3):
        _request(service, at=BASE_TIME + minutes(index))

    with pytest.raises(OtpRateLimited):
        _request(service, at=BASE_TIME + minutes(4))


def test_the_rate_limit_window_eventually_reopens():
    store, delivery = build_store(), build_delivery()
    service = auth_service(
        store, delivery, policy=OtpPolicy(max_codes_per_window=2, rate_limit_window_seconds=600)
    )
    _request(service)
    _request(service, at=BASE_TIME + minutes(1))

    later = _request(service, at=BASE_TIME + minutes(30))

    assert later.challenge_id is not None


def test_rate_limiting_one_phone_does_not_limit_another():
    store, delivery = build_store(), build_delivery()
    service = auth_service(store, delivery, policy=OtpPolicy(max_codes_per_window=1))
    _request(service)

    other = _request(service, phone=OTHER_PHONE, at=BASE_TIME + minutes(1))

    assert other.challenge_id is not None


# ---------------------------------------------------------------- delivery


def test_a_failed_delivery_leaves_no_challenge_behind():
    """A recorded code nobody received would leave the user waiting forever."""

    class Broken:
        is_production_ready = True

        def send_code(self, *, phone_last4, code, reference):  # noqa: ARG002
            msg = "sms gateway down"
            raise RuntimeError(msg)

    store = build_store()
    service = auth_service(store, Broken())

    with pytest.raises(RuntimeError):
        _request(service)

    store.begin()
    challenges = store.otp_challenges.list_open_for_phone(
        hash_phone(PHONE, signing_key=SIGNING_KEY)
    )
    store.rollback()
    assert challenges == ()
