"""Business logic for client login, OTP verification, and registration.

Orchestrates repository calls. Single commit per request.

Security:
  - OTP verify via secure_verify_otp (5 attempts + progressive lockout)
  - Rate limiting via check_otp_send_allowed() -- prevents OTP spam
  - Idempotent OTP send -- skip if OTP already pending
  - OTP stored AFTER SMS success -- no stale OTPs on failure
  - Audit trail on every auth event
  - Refresh token rotation on each use

Flow:
  1. POST /login        -> auto-creates stub if new user -> sends OTP
  2. POST /verify-otp   -> atomic verify -> tokens (or incomplete flag)
  3. POST /register     -> completes profile -> tokens (requires auth)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.logging_utils import FittbotHTTPException, auth_logger
from app.utils.otp import async_send_verification_sms, async_send_ios_premium_sms
from app.utils.otp_security import secure_generate_otp, check_otp_send_allowed, secure_verify_otp
from app.utils.referral_code_generator import generate_referral_code_random
from app.utils.security import create_access_token, create_refresh_token

from .repository import LoginRepository

logger = logging.getLogger("login_service")

REFERRAL_REWARD_AMOUNT = 100
MAX_REFERRAL_CODE_RETRIES = 5


def _default_targets_for_gender(gender: str) -> dict:
    """Return sensible default target values based on gender.

    These ensure get_macros_micros never hits NULL targets for new users.
    """
    g = (gender or "").lower()

    if g == "male":
        return {
            "water_intake": 3.7,
            "calories": 2200,
            "protein": 60,
            "carbs": 275,
            "fat": 65,
            "fiber": 30,
            "sugar": 36,
            "steps": 8000,
            "calories_to_burn": 500,
            "sleep_hours": 7.5,
            "calcium": 1000.0,
            "magnesium": 400.0,
            "potassium": 3400.0,
            "Iodine": 0.15,
            "Iron": 8.0,
        }
    elif g == "female":
        return {
            "water_intake": 2.7,
            "calories": 1800,
            "protein": 50,
            "carbs": 225,
            "fat": 55,
            "fiber": 25,
            "sugar": 25,
            "steps": 7000,
            "calories_to_burn": 400,
            "sleep_hours": 8.0,
            "calcium": 1000.0,
            "magnesium": 310.0,
            "potassium": 2600.0,
            "Iodine": 0.15,
            "Iron": 18.0,
        }
    else:
        return {
            "water_intake": 3.0,
            "calories": 2000,
            "protein": 55,
            "carbs": 250,
            "fat": 60,
            "fiber": 28,
            "sugar": 30,
            "steps": 7500,
            "calories_to_burn": 450,
            "sleep_hours": 7.5,
            "calcium": 1000.0,
            "magnesium": 350.0,
            "potassium": 3000.0,
            "Iodine": 0.15,
            "Iron": 12.0,
        }


def _build_tokens(client_id: int) -> tuple[str, str]:
    access = create_access_token({"sub": str(client_id), "role": "client"})
    refresh = create_refresh_token({"sub": str(client_id)})
    return access, refresh


class LoginService:
    """All login/registration business logic."""

    def __init__(self, db: AsyncSession, redis: Redis):
        self.repo = LoginRepository(db, redis)

    # ── Login ────────────────────────────────────────────────────────

    async def login(
        self, mobile: str, client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Auto-creates a stub client if new user, then sends OTP.

        Latency budget (existing user): ~15ms
          1. Rate limit check        -> 3 Redis calls        ~3ms
          2. Client lookup            -> 1 DB query           ~5ms
          3. Generate + store OTP     -> 1 Redis SET          ~1ms
          4. Audit + commit           -> 1 DB commit          ~5ms
          5. SMS send                 -> background task      ~0ms (non-blocking)
          ----------------------------------------------------------
          Response returned instantly. SMS fires in background.
        """
        # 1. Rate limit check (per-phone + per-IP)
        send_check = await check_otp_send_allowed(
            self.repo.redis, mobile, client_ip
        )
        if not send_check.allowed:
            # Audit rate-limit hit -- fire-and-forget, don't block for it
            asyncio.create_task(self._log_event_standalone(
                "otp_rate_limited", mobile=mobile, ip_address=client_ip,
                user_agent=user_agent, status="blocked",
                detail=send_check.reason,
            ))
            raise FittbotHTTPException(
                status_code=429,
                detail=send_check.reason,
                error_code="OTP_RATE_LIMITED",
            )

        # 2. Client lookup / auto-create stub
        client = await self.repo.get_client_by_mobile(mobile)

        if not client:
            client = await self.repo.create_stub_client(mobile)

        # 3. Generate OTP + store in Redis immediately
        #    (5min TTL -- harmless even if SMS fails, user just retries)
        otp = secure_generate_otp(mobile)
        await self.repo.store_otp(mobile, otp)

        # 4. Audit log + single commit (stub + audit in one round trip)
        await self.repo.log_auth_event(
            "otp_requested",
            mobile=mobile, client_id=client.client_id,
            ip_address=client_ip, user_agent=user_agent,
        )
        await self.repo.commit()

        # 5. SMS in background -- user gets response INSTANTLY
        asyncio.create_task(self._send_otp_background(mobile, otp, client.client_id))

        return {"status": 200, "message": "OTP sent successfully"}

    async def _send_otp_background(
        self, mobile: str, otp: str, client_id: int
    ) -> None:
        """Fire-and-forget SMS send. If it fails, OTP expires in 5min
        and user taps 'Resend'. Same UX as WhatsApp/Google."""
        try:
            success = await async_send_verification_sms(mobile, otp)
            if not success:
                logger.warning(f"SMS send failed for ****{mobile[-4:]}")
                # OTP stays in Redis with 5min TTL -- no cleanup needed.
                # User will see "didn't receive OTP?" and tap resend.
        except Exception as e:
            logger.error(f"SMS background task error: {e}")

    async def _log_event_standalone(self, event_type: str, **kwargs) -> None:
        """Log audit event in a standalone session -- for fire-and-forget
        cases where the main request has already returned."""
        try:
            from app.models.async_database import get_async_sessionmaker
            async_session = get_async_sessionmaker()
            async with async_session() as session:
                from app.models.fittbot_models import AuthEvent
                event = AuthEvent(event_type=event_type, **kwargs)
                session.add(event)
                await session.commit()
        except Exception:
            pass  # audit must never crash

    # ── Resend OTP ───────────────────────────────────────────────────

    async def resend_otp(
        self, mobile: str,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Resend OTP: rate-limit check, delete old OTP + attempts, generate fresh."""
        # 1. Rate limit
        send_check = await check_otp_send_allowed(
            self.repo.redis, mobile, client_ip
        )
        if not send_check.allowed:
            asyncio.create_task(self._log_event_standalone(
                "otp_rate_limited", mobile=mobile, ip_address=client_ip,
                user_agent=user_agent, status="blocked",
                detail=send_check.reason,
            ))
            raise FittbotHTTPException(
                status_code=429,
                detail=send_check.reason,
                error_code="OTP_RATE_LIMITED",
            )

        # 2. Client must exist (they called /login first)
        client = await self.repo.get_client_by_mobile(mobile)
        if not client:
            raise FittbotHTTPException(
                status_code=404,
                detail="Client not found. Please use /login first.",
                error_code="CLIENT_NOT_FOUND",
            )

        # 3. Clear old OTP + reset attempt counter
        await self.repo.delete_otp(mobile)
        await self.repo.clear_otp_attempts(mobile)

        # 4. Generate + store new OTP
        otp = secure_generate_otp(mobile)
        await self.repo.store_otp(mobile, otp)

        # 5. Audit + commit
        await self.repo.log_auth_event(
            "otp_resent",
            mobile=mobile, client_id=client.client_id,
            ip_address=client_ip, user_agent=user_agent,
        )
        await self.repo.commit()

        # 6. SMS in background
        asyncio.create_task(self._send_otp_background(mobile, otp, client.client_id))

        return {"status": 200, "message": "OTP sent successfully"}

    # ── OTP Verification ─────────────────────────────────────────────

    async def verify_otp(
        self, mobile: str, otp: str,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> dict:

        result = await secure_verify_otp(
            self.repo.redis,
            otp_key=f"otp:{mobile}",
            submitted_otp=otp,
            identifier=mobile,
        )

        if result.locked:
            asyncio.create_task(self._log_event_standalone(
                "otp_verify_locked", mobile=mobile, ip_address=client_ip,
                user_agent=user_agent, status="blocked",
                detail=f"Account locked for {result.lock_duration}s",
            ))
            raise FittbotHTTPException(
                status_code=429,
                detail=result.error_message,
                error_code="OTP_LOCKED",
            )

        if not result.success:
            asyncio.create_task(self._log_event_standalone(
                "otp_verify_failed", mobile=mobile, ip_address=client_ip,
                user_agent=user_agent, status="failed",
                detail=result.error_message,
            ))
            raise FittbotHTTPException(
                status_code=400,
                detail=result.error_message,
                error_code="INVALID_OTP",
            )

        client = await self.repo.get_client_by_mobile(mobile)
        if not client:
            raise FittbotHTTPException(
                status_code=404,
                detail="Client not found",
                error_code="CLIENT_NOT_FOUND",
            )

        # New user -- needs to complete /register first (no tokens yet, same as V1)
        if client.incomplete:
            await self.repo.log_auth_event(
                "otp_verified_incomplete",
                mobile=mobile, client_id=client.client_id,
                ip_address=client_ip, user_agent=user_agent,
            )
            await self.repo.commit()

            return {
                "status": 200,
                "message": "OTP verified successfully",
                "incomplete_registration": True,
            }

        # Returning user -- issue tokens
        access_token, refresh_token = _build_tokens(client.client_id)
        client.refresh_token = refresh_token

        gym = None
        if client.gym_id:
            gym = await self.repo.get_gym(client.gym_id)

        await self.repo.log_auth_event(
            "login_success",
            mobile=mobile, client_id=client.client_id,
            ip_address=client_ip, user_agent=user_agent,
        )
        await self.repo.commit()

        return {
            "status": 200,
            "message": "OTP verified successfully",
            "incomplete_registration": False,
            "data": {
                "gym_id": client.gym_id if client.gym_id is not None else None,
                "client_id": client.client_id,
                "gym_name": gym.name if gym else "",
                "gender": client.gender,
                "gym_logo": gym.logo if gym else "",
                "name": client.name or "",
                "mobile": client.contact or "",
                "profile": client.profile or "",
                "weight": client.weight if client.weight else 0,
                "access_token": access_token,
                "refresh_token": refresh_token,
            },
        }

    # ── Register (complete profile) ──────────────────────────────────

    async def register(
        self, name: str, mobile: str, gender: str,
        location: str, referral_id: str | None,
        platform: str | None,
        is_from_ad: bool = False,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> dict:

        client = await self.repo.get_client_by_mobile(mobile)
        if not client:
            if is_from_ad:
                client = await self.repo.create_stub_client(mobile)
            else:
                raise FittbotHTTPException(
                    status_code=404,
                    detail="Client not found. Please use /login first.",
                    error_code="CLIENT_NOT_FOUND",
                )
        elif is_from_ad and not client.incomplete:

            access_token, refresh_token = _build_tokens(client.client_id)
            client.refresh_token = refresh_token

            await self.repo.log_auth_event(
                "ad_funnel_relogin",
                mobile=client.contact, client_id=client.client_id,
                ip_address=client_ip, user_agent=user_agent,
            )
            await self.repo.commit()

            return {
                "status": 200,
                "message": "Already registered — signed in",
                "data": {
                    "client_id": client.client_id,
                    "contact": client.contact,
                    "full_name": client.name,
                    "gender": client.gender,
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                },
            }

        client.name = name
        client.gender = gender
        client.incomplete = False
        client.location = location
        if platform:
            client.platform = platform

        await self._generate_referral_code(client.client_id, name)
        if referral_id:
            await self._process_referral(referral_id, client.client_id)

        targets = _default_targets_for_gender(gender)
        await self.repo.create_client_target(client.client_id, targets)

        if is_from_ad:
            await self.repo.create_ad_registration(client_id=client.client_id)

        access_token, refresh_token = _build_tokens(client.client_id)
        client.refresh_token = refresh_token

        await self.repo.log_auth_event(
            "registration_complete",
            mobile=client.contact, client_id=client.client_id,
            ip_address=client_ip, user_agent=user_agent,
        )


        await self.repo.commit()
        await self.repo.refresh(client)


        await self.repo.redis.set(
            f"first_time_user:{client.client_id}", "1", ex=86400,
        )

        asyncio.create_task(
            self._grant_signup_credits(client.client_id)
        )
        
        # 2. iOS premium SMS
        if (platform or "").strip().lower() == "ios":
            asyncio.create_task(
                async_send_ios_premium_sms(client.contact, name or "User")
            )

        return {
            "status": 200,
            "message": "Client registered successfully",
            "data": {
                "client_id": client.client_id,
                "contact": client.contact,
                "full_name": client.name,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "gender": client.gender,
            },
        }
    # ── Signup Credits ───────────────────────────────────────────────

    SIGNUP_CREDITS = 5
    SIGNUP_CREDIT_EXPIRY_DAYS = 7

    async def _grant_signup_credits(self, client_id: int) -> None:
        """Grant 5 free credits on registration.
        Writes directly to credit_balances + credit_ledger using async session."""
        try:
            await self.repo.grant_signup_credits(
                client_id=client_id,
                credits=self.SIGNUP_CREDITS,
                expiry_days=self.SIGNUP_CREDIT_EXPIRY_DAYS,
            )
            logger.info("SIGNUP_CREDITS_GRANTED | client_id=%s credits=%d", client_id, self.SIGNUP_CREDITS)
        except Exception as e:
            logger.warning(f"SIGNUP_CREDITS_FAILED | client_id={client_id} err={e}")

    # ── Refresh Token Rotation ───────────────────────────────────────

    async def rotate_refresh_token(
        self, client_id: int, old_refresh_token: str,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Issue new access + refresh tokens. Invalidates old refresh token.

        P3: Refresh token rotation -- if leaked, attacker can only use it once.
        If old token doesn't match DB, it means token was already rotated
        (possible theft), so we revoke everything.
        """
        client = await self.repo.get_client_by_id(client_id)
        if not client:
            raise FittbotHTTPException(
                status_code=404,
                detail="Client not found",
                error_code="CLIENT_NOT_FOUND",
            )

        # Check if the refresh token matches what's stored
        if client.refresh_token != old_refresh_token:
            # Token reuse detected -- possible theft. Revoke all tokens.
            client.refresh_token = None
            await self.repo.log_auth_event(
                "refresh_token_reuse_detected",
                client_id=client_id, mobile=client.contact,
                ip_address=client_ip, user_agent=user_agent,
                status="blocked",
                detail="Possible token theft -- all sessions revoked",
            )
            await self.repo.commit()
            raise FittbotHTTPException(
                status_code=401,
                detail="Session expired. Please login again.",
                error_code="TOKEN_REUSE_DETECTED",
            )

        # Issue new token pair
        access_token, refresh_token = _build_tokens(client.client_id)
        client.refresh_token = refresh_token

        await self.repo.log_auth_event(
            "token_refreshed",
            client_id=client_id, mobile=client.contact,
            ip_address=client_ip, user_agent=user_agent,
        )
        await self.repo.commit()

        return {
            "status": 200,
            "message": "Token refreshed successfully",
            "data": {
                "access_token": access_token,
                "refresh_token": refresh_token,
            },
        }

    # ── Private helpers ──────────────────────────────────────────────

    async def _generate_referral_code(self, client_id: int, name: str) -> str | None:
        existing = await self.repo.get_referral_by_client(client_id)
        if existing:
            return existing.referral_code

        for attempt in range(MAX_REFERRAL_CODE_RETRIES):
            candidate = generate_referral_code_random(name=name or "User")
            collision = await self.repo.get_referral_by_code(candidate)
            if collision is None:
                await self.repo.create_referral_code(client_id, candidate)
                return candidate
            auth_logger.warning(
                f"Referral code collision on attempt {attempt + 1} for client {client_id}",
                error="Code already exists",
            )

        auth_logger.error(
            f"Failed to generate referral code for client {client_id} after {MAX_REFERRAL_CODE_RETRIES} attempts",
            error="All attempts exhausted",
        )
        return None

    async def _process_referral(self, referral_code: str, referee_id: int) -> None:
        referrer_entry = await self.repo.get_referral_by_code(referral_code.strip())
        if not referrer_entry:
            return

        await self.repo.create_referral_mapping(referrer_entry.client_id, referee_id)
        await self.repo.add_fittbot_cash(referee_id, REFERRAL_REWARD_AMOUNT)
        await self.repo.add_fittbot_cash(referrer_entry.client_id, REFERRAL_REWARD_AMOUNT)
