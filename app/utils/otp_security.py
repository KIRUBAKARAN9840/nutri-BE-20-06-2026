

import os
import secrets
import time
import json
import logging
from dataclasses import dataclass
from typing import Optional, Tuple

logger = logging.getLogger("otp_security")


# ─── CONFIGURATION (loaded from env, with safe defaults) ────────────────────

OTP_MAX_VERIFY_ATTEMPTS = int(os.getenv("OTP_MAX_VERIFY_ATTEMPTS", "5"))
OTP_SEND_LIMIT_PER_PHONE_PER_HOUR = int(os.getenv("OTP_SEND_LIMIT_PER_PHONE_PER_HOUR", "5"))
OTP_SEND_LIMIT_PER_IP_PER_HOUR = int(os.getenv("OTP_SEND_LIMIT_PER_IP_PER_HOUR", "20"))
OTP_EXPIRY_SECONDS = 300  # 5 minutes, matches existing OTP TTL
OTP_LOCKOUT_DURATIONS = [60, 300, 900, 3600]  # 1min, 5min, 15min, 1hr


def _load_test_numbers() -> dict:

    raw = os.getenv("OTP_TEST_NUMBERS", "")
    mapping = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if ":" in pair:
            phone, otp = pair.split(":", 1)
            mapping[phone.strip()] = otp.strip()
    return mapping


TEST_NUMBERS = _load_test_numbers()

@dataclass
class OTPSendCheck:
    allowed: bool
    reason: str = ""
    retry_after: int = 0


@dataclass
class OTPVerifyResult:
    success: bool
    remaining_attempts: int = 0
    locked: bool = False
    lock_duration: int = 0
    error_message: str = ""


# ─── SECURE OTP GENERATION ───────────────────────────────────────────────────

def secure_generate_otp(phone_number: Optional[str] = None) -> str:

    if phone_number and phone_number in TEST_NUMBERS:
        return TEST_NUMBERS[phone_number]
    return str(secrets.randbelow(900000) + 100000)


# ─── OTP SEND RATE LIMITING ─────────────────────────────────────────────────

async def check_otp_send_allowed(
    redis_client,
    identifier: str,
    client_ip: Optional[str] = None,
) -> OTPSendCheck:
    """
    Check if OTP send is allowed for this phone + IP.
    Call BEFORE generating and sending OTP.

    Checks (in order):
        1. Is phone currently locked out? (from too many wrong verifications)
        2. Per-phone: max sends per hour
        3. Per-IP: max sends per hour
    """
    try:
        # 1. Check lockout
        lockout_key = f"otp_lockout:{identifier}"
        lockout_data = await redis_client.get(lockout_key)
        if lockout_data:
            info = json.loads(lockout_data)
            locked_until = info.get("locked_until", 0)
            now = time.time()
            if now < locked_until:
                retry_after = int(locked_until - now)
                logger.warning(
                    f"[OTP Security] Send blocked - phone locked: "
                    f"identifier={_mask(identifier)}, retry_after={retry_after}s"
                )
                return OTPSendCheck(
                    allowed=False,
                    reason="Too many failed attempts. Please try again later.",
                    retry_after=retry_after,
                )

        # 2. Per-phone hourly limit
        phone_key = f"otp_send_count:{identifier}"
        phone_count = await redis_client.incr(phone_key)
        if phone_count == 1:
            await redis_client.expire(phone_key, 3600)

        if phone_count > OTP_SEND_LIMIT_PER_PHONE_PER_HOUR:
            await redis_client.decr(phone_key)
            ttl = await redis_client.ttl(phone_key)
            logger.warning(
                f"[OTP Security] Send rate limit hit (phone): "
                f"identifier={_mask(identifier)}, count={phone_count}"
            )
            return OTPSendCheck(
                allowed=False,
                reason="Too many OTP requests. Please try again later.",
                retry_after=max(ttl, 60),
            )

        # 3. Per-IP hourly limit
        if client_ip:
            ip_key = f"otp_send_ip:{client_ip}"
            ip_count = await redis_client.incr(ip_key)
            if ip_count == 1:
                await redis_client.expire(ip_key, 3600)

            if ip_count > OTP_SEND_LIMIT_PER_IP_PER_HOUR:
                await redis_client.decr(ip_key)
                await redis_client.decr(phone_key)  # undo phone increment too
                ttl = await redis_client.ttl(ip_key)
                logger.warning(
                    f"[OTP Security] Send rate limit hit (IP): "
                    f"ip={client_ip}, count={ip_count}"
                )
                return OTPSendCheck(
                    allowed=False,
                    reason="Too many OTP requests from this network.",
                    retry_after=max(ttl, 60),
                )

        return OTPSendCheck(allowed=True)

    except Exception as e:
        # Fail open — if Redis is down, allow the request
        logger.error(f"[OTP Security] Redis error in send check: {e}")
        return OTPSendCheck(allowed=True)


# ─── OTP VERIFICATION WITH BRUTE-FORCE PROTECTION ───────────────────────────

async def secure_verify_otp(
    redis_client,
    otp_key: str,
    submitted_otp: str,
    identifier: str,
) -> OTPVerifyResult:

    try:

        lockout_key = f"otp_lockout:{identifier}"
        lockout_data = await redis_client.get(lockout_key)
        if lockout_data:
            info = json.loads(lockout_data)
            locked_until = info.get("locked_until", 0)
            now = time.time()
            if now < locked_until:
                retry_after = int(locked_until - now)
                logger.warning(
                    f"[OTP Security] Verify blocked - locked: "
                    f"identifier={_mask(identifier)}, retry_after={retry_after}s"
                )
                return OTPVerifyResult(
                    success=False,
                    locked=True,
                    lock_duration=retry_after,
                    error_message=f"Account temporarily locked. Try again in {_human_duration(retry_after)}.",
                )

        # 2. Fetch stored OTP
        stored_otp = await redis_client.get(otp_key)
        if not stored_otp:
            return OTPVerifyResult(
                success=False,
                error_message="OTP expired or not found. Please request a new OTP.",
            )

        # 3. Increment attempt counter
        attempt_key = f"otp_attempts:{identifier}"
        attempts = await redis_client.incr(attempt_key)
        if attempts == 1:
            await redis_client.expire(attempt_key, OTP_EXPIRY_SECONDS)

        # Compare OTP first -- correct guess on last attempt should still succeed
        actual_otp = _extract_otp_value(stored_otp)

        if actual_otp == str(submitted_otp).strip():
            await redis_client.delete(otp_key)
            await redis_client.delete(attempt_key)

            level_key = f"otp_lockout_level:{identifier}"
            await redis_client.delete(level_key)

            logger.info(
                f"[OTP Security] OTP verified successfully: "
                f"identifier={_mask(identifier)}, attempts_used={attempts}"
            )
            return OTPVerifyResult(success=True, remaining_attempts=0)

        # Wrong OTP -- check if this was the last allowed attempt
        if attempts >= OTP_MAX_VERIFY_ATTEMPTS:
            await redis_client.delete(otp_key)
            await redis_client.delete(attempt_key)
            lock_duration = await _apply_lockout(redis_client, identifier)

            logger.warning(
                f"[OTP Security] Max attempts exceeded - OTP invalidated: "
                f"identifier={_mask(identifier)}, lock_duration={lock_duration}s"
            )
            return OTPVerifyResult(
                success=False,
                remaining_attempts=0,
                locked=True,
                lock_duration=lock_duration,
                error_message=f"Too many failed attempts. Try again in {_human_duration(lock_duration)}.",
            )

        remaining = OTP_MAX_VERIFY_ATTEMPTS - attempts
        logger.warning(
            f"[OTP Security] Wrong OTP: identifier={_mask(identifier)}, "
            f"attempt={attempts}/{OTP_MAX_VERIFY_ATTEMPTS}, remaining={remaining}"
        )
        return OTPVerifyResult(
            success=False,
            remaining_attempts=max(0, remaining),
            error_message=f"Incorrect OTP. {max(0, remaining)} attempt{'s' if remaining != 1 else ''} remaining.",
        )

    except Exception as e:

        logger.error(f"[OTP Security] Redis error in verify: {e}")
        return await _fallback_verify(redis_client, otp_key, submitted_otp)


async def secure_verify_otp_db(
    redis_client,
    identifier: str,
    submitted_otp: str,
    stored_otp: str,
) -> OTPVerifyResult:
    """
    Verify OTP stored in database (not Redis) with brute-force protection.
    Uses Redis only for attempt counting and lockout tracking.

    Used by: admin auth (stores OTP in Admins table, not Redis).
    """
    try:
        # 1. Check lockout
        lockout_key = f"otp_lockout:{identifier}"
        lockout_data = await redis_client.get(lockout_key)
        if lockout_data:
            info = json.loads(lockout_data)
            locked_until = info.get("locked_until", 0)
            now = time.time()
            if now < locked_until:
                retry_after = int(locked_until - now)
                return OTPVerifyResult(
                    success=False,
                    locked=True,
                    lock_duration=retry_after,
                    error_message=f"Account temporarily locked. Try again in {_human_duration(retry_after)}.",
                )

        if not stored_otp:
            return OTPVerifyResult(
                success=False,
                error_message="OTP expired or not found. Please request a new OTP.",
            )

        # 2. Increment attempt counter
        attempt_key = f"otp_attempts:{identifier}"
        attempts = await redis_client.incr(attempt_key)
        if attempts == 1:
            await redis_client.expire(attempt_key, OTP_EXPIRY_SECONDS)

        # 3. Compare first -- correct guess on last attempt should still succeed
        if str(stored_otp).strip() == str(submitted_otp).strip():
            await redis_client.delete(attempt_key)
            level_key = f"otp_lockout_level:{identifier}"
            await redis_client.delete(level_key)

            logger.info(
                f"[OTP Security] OTP verified successfully (DB): "
                f"identifier={_mask(identifier)}, attempts_used={attempts}"
            )
            return OTPVerifyResult(success=True, remaining_attempts=0)

        # 4. Wrong OTP -- check if last attempt
        if attempts >= OTP_MAX_VERIFY_ATTEMPTS:
            await redis_client.delete(attempt_key)
            lock_duration = await _apply_lockout(redis_client, identifier)

            logger.warning(
                f"[OTP Security] Max attempts exceeded (DB OTP): "
                f"identifier={_mask(identifier)}, lock_duration={lock_duration}s"
            )
            return OTPVerifyResult(
                success=False,
                remaining_attempts=0,
                locked=True,
                lock_duration=lock_duration,
                error_message=f"Too many failed attempts. Try again in {_human_duration(lock_duration)}.",
            )

        # 5. Wrong OTP, retries left
        remaining = OTP_MAX_VERIFY_ATTEMPTS - attempts
        logger.warning(
            f"[OTP Security] Wrong OTP (DB): identifier={_mask(identifier)}, "
            f"attempt={attempts}/{OTP_MAX_VERIFY_ATTEMPTS}, remaining={remaining}"
        )
        return OTPVerifyResult(
            success=False,
            remaining_attempts=max(0, remaining),
            error_message=f"Incorrect OTP. {max(0, remaining)} attempt{'s' if remaining != 1 else ''} remaining.",
        )

    except Exception as e:
        logger.error(f"[OTP Security] Redis error in DB verify: {e}")
        # Fail open — basic comparison
        if stored_otp and str(stored_otp).strip() == str(submitted_otp).strip():
            return OTPVerifyResult(success=True)
        return OTPVerifyResult(success=False, error_message="Invalid OTP")


# ─── INTERNAL HELPERS ────────────────────────────────────────────────────────

def _extract_otp_value(stored_value: str) -> str:
    """
    Extract OTP from stored value. Handles both formats:
      - Plain string: "847291"
      - JSON (telecaller pattern): '{"otp": "847291", "mobile_number": "..."}'
    """
    try:
        data = json.loads(stored_value)
        if isinstance(data, dict) and "otp" in data:
            return str(data["otp"])
    except (json.JSONDecodeError, TypeError):
        pass
    return str(stored_value).strip()


async def _apply_lockout(redis_client, identifier: str) -> int:
    """
    Apply progressive lockout. Returns lock duration in seconds.
    Escalation: 60s → 300s → 900s → 3600s (1min → 5min → 15min → 1hr)
    """
    level_key = f"otp_lockout_level:{identifier}"
    level = await redis_client.incr(level_key)
    if level == 1:
        await redis_client.expire(level_key, 86400)  # 24hr window for escalation

    duration_index = min(level - 1, len(OTP_LOCKOUT_DURATIONS) - 1)
    lock_duration = OTP_LOCKOUT_DURATIONS[duration_index]

    lockout_key = f"otp_lockout:{identifier}"
    lockout_data = json.dumps({
        "locked_until": time.time() + lock_duration,
        "lockout_level": level,
    })
    await redis_client.setex(lockout_key, lock_duration, lockout_data)

    return lock_duration


async def _fallback_verify(redis_client, otp_key: str, submitted_otp: str) -> OTPVerifyResult:
    """Fallback: basic OTP comparison when Redis is down (no protection)."""
    stored_otp = None
    try:
        stored_otp = await redis_client.get(otp_key)
    except Exception:
        pass

    if stored_otp and _extract_otp_value(stored_otp) == str(submitted_otp).strip():
        try:
            await redis_client.delete(otp_key)
        except Exception:
            pass
        return OTPVerifyResult(success=True)
    return OTPVerifyResult(success=False, error_message="Invalid OTP")


def _mask(identifier: str) -> str:
    """Mask identifier for safe logging."""
    if len(identifier) <= 4:
        return "****"
    return identifier[:2] + "****" + identifier[-2:]


def _human_duration(seconds: int) -> str:
    """Convert seconds to human-readable duration for error messages."""
    if seconds < 60:
        return f"{seconds} seconds"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = minutes // 60
    return f"{hours} hour{'s' if hours != 1 else ''}"
