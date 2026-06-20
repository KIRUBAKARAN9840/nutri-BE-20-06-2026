"""
Unit tests for the OTP brute-force protection module.

Tests secure generation, send rate limiting, verify attempt tracking,
progressive lockout, DB OTP verification, and helper functions.

Test mobile: 7373675762
"""

import json
import time
import pytest
from unittest.mock import patch

from app.utils.otp_security import (
    secure_generate_otp,
    check_otp_send_allowed,
    secure_verify_otp,
    secure_verify_otp_db,
    _extract_otp_value,
    _mask,
    _human_duration,
    _apply_lockout,
    OTPSendCheck,
    OTPVerifyResult,
    OTP_MAX_VERIFY_ATTEMPTS,
    OTP_SEND_LIMIT_PER_PHONE_PER_HOUR,
    OTP_SEND_LIMIT_PER_IP_PER_HOUR,
    OTP_LOCKOUT_DURATIONS,
)

PHONE = "7373675762"
OTP_KEY = f"otp:{PHONE}"


# ---------------------------------------------------------------------------
# secure_generate_otp
# ---------------------------------------------------------------------------

class TestSecureGenerateOTP:
    def test_generates_6_digit_string(self):
        otp = secure_generate_otp(PHONE)
        assert isinstance(otp, str)
        assert len(otp) == 6
        assert otp.isdigit()

    def test_always_in_range(self):
        """OTP should be between 100000 and 999999."""
        for _ in range(100):
            otp = int(secure_generate_otp(PHONE))
            assert 100000 <= otp <= 999999

    def test_not_constant(self):
        """Multiple calls should produce different OTPs (not all the same)."""
        otps = {secure_generate_otp(PHONE) for _ in range(20)}
        assert len(otps) > 1

    def test_works_without_phone(self):
        otp = secure_generate_otp()
        assert len(otp) == 6 and otp.isdigit()

    def test_test_numbers_from_env(self):
        with patch.dict("os.environ", {"OTP_TEST_NUMBERS": f"{PHONE}:999888"}):
            # Reload module-level constant
            with patch("app.utils.otp_security.TEST_NUMBERS", {PHONE: "999888"}):
                otp = secure_generate_otp(PHONE)
                assert otp == "999888"

    def test_non_test_number_not_fixed(self):
        with patch("app.utils.otp_security.TEST_NUMBERS", {"0000000000": "111111"}):
            otp = secure_generate_otp(PHONE)
            # Should be a random 6-digit, not "111111"
            assert len(otp) == 6 and otp.isdigit()


# ---------------------------------------------------------------------------
# check_otp_send_allowed
# ---------------------------------------------------------------------------

class TestCheckOTPSendAllowed:
    @pytest.mark.asyncio
    async def test_first_send_allowed(self, fake_redis):
        result = await check_otp_send_allowed(fake_redis, PHONE)
        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_phone_rate_limit_enforced(self, fake_redis):
        """After max sends per phone, next should be blocked."""
        for i in range(OTP_SEND_LIMIT_PER_PHONE_PER_HOUR):
            result = await check_otp_send_allowed(fake_redis, PHONE)
            assert result.allowed is True, f"Send #{i+1} should be allowed"

        # Next one should be blocked
        result = await check_otp_send_allowed(fake_redis, PHONE)
        assert result.allowed is False
        assert "Too many OTP requests" in result.reason
        assert result.retry_after > 0

    @pytest.mark.asyncio
    async def test_ip_rate_limit_enforced(self, fake_redis):
        """After max sends per IP, next should be blocked."""
        test_ip = "192.168.1.100"
        for i in range(OTP_SEND_LIMIT_PER_IP_PER_HOUR):
            # Different phone each time so phone limit isn't hit
            result = await check_otp_send_allowed(fake_redis, f"phone_{i}", test_ip)
            assert result.allowed is True

        result = await check_otp_send_allowed(fake_redis, "phone_extra", test_ip)
        assert result.allowed is False
        assert "network" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_send_blocked_when_locked(self, fake_redis):
        """If phone is locked out, send should be blocked."""
        lockout_data = json.dumps({"locked_until": time.time() + 300})
        await fake_redis.set(f"otp_lockout:{PHONE}", lockout_data)

        result = await check_otp_send_allowed(fake_redis, PHONE)
        assert result.allowed is False
        assert "failed attempts" in result.reason.lower()
        assert result.retry_after > 0

    @pytest.mark.asyncio
    async def test_no_ip_check_when_ip_is_none(self, fake_redis):
        """When client_ip is None, only phone limit applies."""
        for i in range(OTP_SEND_LIMIT_PER_PHONE_PER_HOUR):
            result = await check_otp_send_allowed(fake_redis, PHONE, client_ip=None)
            assert result.allowed is True

        result = await check_otp_send_allowed(fake_redis, PHONE, client_ip=None)
        assert result.allowed is False

    @pytest.mark.asyncio
    async def test_different_phones_independent(self, fake_redis):
        """Rate limits are per-phone, not global."""
        for i in range(OTP_SEND_LIMIT_PER_PHONE_PER_HOUR):
            await check_otp_send_allowed(fake_redis, PHONE)

        # Different phone should still be allowed
        result = await check_otp_send_allowed(fake_redis, "9999999999")
        assert result.allowed is True


# ---------------------------------------------------------------------------
# secure_verify_otp — success path
# ---------------------------------------------------------------------------

class TestSecureVerifyOTPSuccess:
    @pytest.mark.asyncio
    async def test_correct_otp_succeeds(self, fake_redis):
        await fake_redis.set(OTP_KEY, "482917")
        result = await secure_verify_otp(fake_redis, OTP_KEY, "482917", identifier=PHONE)
        assert result.success is True
        assert result.remaining_attempts == 0

    @pytest.mark.asyncio
    async def test_otp_deleted_after_success(self, fake_redis):
        await fake_redis.set(OTP_KEY, "482917")
        await secure_verify_otp(fake_redis, OTP_KEY, "482917", identifier=PHONE)

        stored = await fake_redis.get(OTP_KEY)
        assert stored is None

    @pytest.mark.asyncio
    async def test_attempt_counter_cleared_after_success(self, fake_redis):
        await fake_redis.set(OTP_KEY, "482917")
        # Make 2 wrong attempts first
        await secure_verify_otp(fake_redis, OTP_KEY, "000000", identifier=PHONE)
        await secure_verify_otp(fake_redis, OTP_KEY, "000001", identifier=PHONE)
        # Then correct
        result = await secure_verify_otp(fake_redis, OTP_KEY, "482917", identifier=PHONE)
        assert result.success is True

        counter = await fake_redis.get(f"otp_attempts:{PHONE}")
        assert counter is None

    @pytest.mark.asyncio
    async def test_lockout_level_reset_on_success(self, fake_redis):
        """Successful verify should reset progressive lockout level."""
        await fake_redis.set(f"otp_lockout_level:{PHONE}", "2")
        await fake_redis.set(OTP_KEY, "482917")

        result = await secure_verify_otp(fake_redis, OTP_KEY, "482917", identifier=PHONE)
        assert result.success is True

        level = await fake_redis.get(f"otp_lockout_level:{PHONE}")
        assert level is None


# ---------------------------------------------------------------------------
# secure_verify_otp — wrong OTP with remaining attempts
# ---------------------------------------------------------------------------

class TestSecureVerifyOTPWrongAttempts:
    @pytest.mark.asyncio
    async def test_wrong_otp_returns_remaining(self, fake_redis):
        await fake_redis.set(OTP_KEY, "482917")
        result = await secure_verify_otp(fake_redis, OTP_KEY, "000000", identifier=PHONE)

        assert result.success is False
        assert result.locked is False
        assert result.remaining_attempts == OTP_MAX_VERIFY_ATTEMPTS - 1
        assert "Incorrect OTP" in result.error_message
        assert str(OTP_MAX_VERIFY_ATTEMPTS - 1) in result.error_message

    @pytest.mark.asyncio
    async def test_remaining_decrements_each_attempt(self, fake_redis):
        await fake_redis.set(OTP_KEY, "482917")

        for i in range(1, OTP_MAX_VERIFY_ATTEMPTS + 1):
            result = await secure_verify_otp(fake_redis, OTP_KEY, "000000", identifier=PHONE)
            if i <= OTP_MAX_VERIFY_ATTEMPTS:
                expected_remaining = max(0, OTP_MAX_VERIFY_ATTEMPTS - i)
                assert result.remaining_attempts == expected_remaining

    @pytest.mark.asyncio
    async def test_otp_not_deleted_on_wrong_attempt(self, fake_redis):
        await fake_redis.set(OTP_KEY, "482917")
        await secure_verify_otp(fake_redis, OTP_KEY, "000000", identifier=PHONE)

        stored = await fake_redis.get(OTP_KEY)
        assert stored == "482917"

    @pytest.mark.asyncio
    async def test_expired_otp_returns_not_found(self, fake_redis):
        """No OTP in Redis simulates expiry."""
        result = await secure_verify_otp(fake_redis, OTP_KEY, "000000", identifier=PHONE)
        assert result.success is False
        assert "expired" in result.error_message.lower() or "not found" in result.error_message.lower()


# ---------------------------------------------------------------------------
# secure_verify_otp — brute force lockout (5 wrong = lock)
# ---------------------------------------------------------------------------

class TestSecureVerifyOTPBruteForce:
    @pytest.mark.asyncio
    async def test_lockout_after_max_attempts(self, fake_redis):
        """After 5 wrong attempts, 6th should trigger lockout."""
        await fake_redis.set(OTP_KEY, "482917")

        # Make max_attempts wrong tries
        for i in range(OTP_MAX_VERIFY_ATTEMPTS):
            result = await secure_verify_otp(fake_redis, OTP_KEY, "000000", identifier=PHONE)
            assert result.success is False
            assert result.locked is False, f"Should NOT be locked on attempt {i+1}"

        # The next attempt (max+1) should trigger lockout
        result = await secure_verify_otp(fake_redis, OTP_KEY, "000000", identifier=PHONE)
        assert result.success is False
        assert result.locked is True
        assert result.lock_duration > 0
        assert "Too many failed attempts" in result.error_message

    @pytest.mark.asyncio
    async def test_otp_invalidated_after_lockout(self, fake_redis):
        """OTP should be deleted when lockout triggers."""
        await fake_redis.set(OTP_KEY, "482917")

        # Exhaust attempts + trigger lockout
        for _ in range(OTP_MAX_VERIFY_ATTEMPTS + 1):
            await secure_verify_otp(fake_redis, OTP_KEY, "000000", identifier=PHONE)

        stored = await fake_redis.get(OTP_KEY)
        assert stored is None

    @pytest.mark.asyncio
    async def test_locked_user_blocked_immediately(self, fake_redis):
        """Once locked, even correct OTP should be blocked."""
        # Set lockout
        lockout_data = json.dumps({"locked_until": time.time() + 300})
        await fake_redis.set(f"otp_lockout:{PHONE}", lockout_data)
        await fake_redis.set(OTP_KEY, "482917")

        result = await secure_verify_otp(fake_redis, OTP_KEY, "482917", identifier=PHONE)
        assert result.success is False
        assert result.locked is True
        assert "locked" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_correct_otp_on_last_attempt_succeeds(self, fake_redis):
        """If user enters correct OTP on the 5th (last) attempt, it should succeed."""
        await fake_redis.set(OTP_KEY, "482917")

        # Make max-1 wrong attempts
        for _ in range(OTP_MAX_VERIFY_ATTEMPTS - 1):
            result = await secure_verify_otp(fake_redis, OTP_KEY, "000000", identifier=PHONE)
            assert result.success is False
            assert result.locked is False

        # 5th attempt with correct OTP
        result = await secure_verify_otp(fake_redis, OTP_KEY, "482917", identifier=PHONE)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_full_brute_force_simulation_with_phone_7373675762(self, fake_redis):
        """
        Full brute force simulation using phone 7373675762:
        - Store OTP
        - Try 5 wrong OTPs (should get decreasing remaining attempts)
        - 6th wrong attempt triggers lockout
        - After lockout, even correct OTP is blocked
        """
        otp = secure_generate_otp(PHONE)
        await fake_redis.set(OTP_KEY, otp)

        # 5 wrong attempts
        for i in range(1, 6):
            result = await secure_verify_otp(fake_redis, OTP_KEY, "000000", identifier=PHONE)
            assert result.success is False
            assert result.locked is False
            assert result.remaining_attempts == 5 - i

        # 6th attempt triggers lockout
        result = await secure_verify_otp(fake_redis, OTP_KEY, "000000", identifier=PHONE)
        assert result.success is False
        assert result.locked is True
        assert result.lock_duration == OTP_LOCKOUT_DURATIONS[0]  # 60s first lockout

        # OTP should be deleted
        stored = await fake_redis.get(OTP_KEY)
        assert stored is None

        # Even if OTP is re-set, locked user is blocked
        await fake_redis.set(OTP_KEY, otp)
        result = await secure_verify_otp(fake_redis, OTP_KEY, otp, identifier=PHONE)
        assert result.success is False
        assert result.locked is True


# ---------------------------------------------------------------------------
# Progressive lockout escalation
# ---------------------------------------------------------------------------

class TestProgressiveLockout:
    @pytest.mark.asyncio
    async def test_first_lockout_is_60s(self, fake_redis):
        duration = await _apply_lockout(fake_redis, PHONE)
        assert duration == 60

    @pytest.mark.asyncio
    async def test_second_lockout_is_300s(self, fake_redis):
        await _apply_lockout(fake_redis, PHONE)
        duration = await _apply_lockout(fake_redis, PHONE)
        assert duration == 300

    @pytest.mark.asyncio
    async def test_third_lockout_is_900s(self, fake_redis):
        await _apply_lockout(fake_redis, PHONE)
        await _apply_lockout(fake_redis, PHONE)
        duration = await _apply_lockout(fake_redis, PHONE)
        assert duration == 900

    @pytest.mark.asyncio
    async def test_fourth_lockout_is_3600s(self, fake_redis):
        for _ in range(3):
            await _apply_lockout(fake_redis, PHONE)
        duration = await _apply_lockout(fake_redis, PHONE)
        assert duration == 3600

    @pytest.mark.asyncio
    async def test_lockout_caps_at_max(self, fake_redis):
        """After 4+ lockouts, duration stays at 3600s."""
        for _ in range(10):
            duration = await _apply_lockout(fake_redis, PHONE)
        assert duration == 3600

    @pytest.mark.asyncio
    async def test_lockout_stores_in_redis(self, fake_redis):
        await _apply_lockout(fake_redis, PHONE)
        lockout_data = await fake_redis.get(f"otp_lockout:{PHONE}")
        assert lockout_data is not None
        info = json.loads(lockout_data)
        assert "locked_until" in info
        assert info["locked_until"] > time.time()


# ---------------------------------------------------------------------------
# secure_verify_otp_db — DB-stored OTP
# ---------------------------------------------------------------------------

class TestSecureVerifyOTPDB:
    @pytest.mark.asyncio
    async def test_correct_otp_succeeds(self, fake_redis):
        result = await secure_verify_otp_db(fake_redis, PHONE, "482917", "482917")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_wrong_otp_returns_remaining(self, fake_redis):
        result = await secure_verify_otp_db(fake_redis, PHONE, "000000", "482917")
        assert result.success is False
        assert result.remaining_attempts == OTP_MAX_VERIFY_ATTEMPTS - 1

    @pytest.mark.asyncio
    async def test_lockout_after_max_attempts(self, fake_redis):
        for _ in range(OTP_MAX_VERIFY_ATTEMPTS):
            result = await secure_verify_otp_db(fake_redis, PHONE, "000000", "482917")
            assert result.locked is False

        result = await secure_verify_otp_db(fake_redis, PHONE, "000000", "482917")
        assert result.locked is True
        assert result.lock_duration > 0

    @pytest.mark.asyncio
    async def test_none_stored_otp(self, fake_redis):
        result = await secure_verify_otp_db(fake_redis, PHONE, "000000", None)
        assert result.success is False
        assert "expired" in result.error_message.lower() or "not found" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_locked_user_blocked(self, fake_redis):
        lockout_data = json.dumps({"locked_until": time.time() + 300})
        await fake_redis.set(f"otp_lockout:{PHONE}", lockout_data)

        result = await secure_verify_otp_db(fake_redis, PHONE, "482917", "482917")
        assert result.success is False
        assert result.locked is True

    @pytest.mark.asyncio
    async def test_success_clears_attempt_counter(self, fake_redis):
        # 2 wrong, then correct
        await secure_verify_otp_db(fake_redis, PHONE, "000000", "482917")
        await secure_verify_otp_db(fake_redis, PHONE, "000001", "482917")
        result = await secure_verify_otp_db(fake_redis, PHONE, "482917", "482917")
        assert result.success is True

        counter = await fake_redis.get(f"otp_attempts:{PHONE}")
        assert counter is None


# ---------------------------------------------------------------------------
# JSON OTP extraction (telecaller format)
# ---------------------------------------------------------------------------

class TestExtractOTPValue:
    def test_plain_string(self):
        assert _extract_otp_value("482917") == "482917"

    def test_json_format(self):
        stored = json.dumps({"otp": "482917", "mobile_number": PHONE})
        assert _extract_otp_value(stored) == "482917"

    def test_json_with_extra_fields(self):
        stored = json.dumps({
            "otp": "123456",
            "mobile_number": PHONE,
            "user_type": "manager",
            "created_at": "2026-01-01",
        })
        assert _extract_otp_value(stored) == "123456"

    def test_whitespace_stripped(self):
        assert _extract_otp_value("  482917  ") == "482917"

    def test_numeric_otp_in_json(self):
        stored = json.dumps({"otp": 482917})
        assert _extract_otp_value(stored) == "482917"


# ---------------------------------------------------------------------------
# _mask helper
# ---------------------------------------------------------------------------

class TestMask:
    def test_standard_phone(self):
        assert _mask(PHONE) == "73****62"

    def test_short_identifier(self):
        assert _mask("abc") == "****"

    def test_four_chars(self):
        assert _mask("abcd") == "****"

    def test_five_chars(self):
        assert _mask("abcde") == "ab****de"

    def test_email(self):
        result = _mask("user@example.com")
        assert result.startswith("us")
        assert result.endswith("om")
        assert "****" in result


# ---------------------------------------------------------------------------
# _human_duration helper
# ---------------------------------------------------------------------------

class TestHumanDuration:
    def test_seconds(self):
        assert _human_duration(45) == "45 seconds"

    def test_one_minute(self):
        assert _human_duration(60) == "1 minute"

    def test_multiple_minutes(self):
        assert _human_duration(300) == "5 minutes"

    def test_one_hour(self):
        assert _human_duration(3600) == "1 hour"

    def test_multiple_hours(self):
        assert _human_duration(7200) == "2 hours"


# ---------------------------------------------------------------------------
# Verify with JSON-stored OTP (telecaller pattern)
# ---------------------------------------------------------------------------

class TestVerifyJSONStoredOTP:
    @pytest.mark.asyncio
    async def test_verify_json_otp_format(self, fake_redis):
        """Telecaller stores OTP as JSON — verify should handle it."""
        otp_data = json.dumps({
            "otp": "482917",
            "mobile_number": PHONE,
            "user_type": "telecaller",
        })
        otp_key = f"telecaller:otp:{PHONE}"
        await fake_redis.set(otp_key, otp_data)

        result = await secure_verify_otp(fake_redis, otp_key, "482917", identifier=PHONE)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_verify_json_otp_wrong(self, fake_redis):
        otp_data = json.dumps({"otp": "482917", "mobile_number": PHONE})
        otp_key = f"telecaller:otp:{PHONE}"
        await fake_redis.set(otp_key, otp_data)

        result = await secure_verify_otp(fake_redis, otp_key, "000000", identifier=PHONE)
        assert result.success is False
        assert "Incorrect OTP" in result.error_message
