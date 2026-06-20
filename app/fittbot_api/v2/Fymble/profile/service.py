"""Business logic for the v2 Profile module.

Three flows:
  1. get_profile          — GET screen data (no email)
  2. update_details       — PUT name/gender/dob/height/lifestyle/goal
                            (recomputes ClientTarget when fitness inputs change)
  3. initiate_contact_change + verify_contact_change
                          — Two-step OTP-protected mobile-number change.
                            OTP is sent to BOTH the existing number AND the
                            new number, and BOTH must be verified before the
                            DB row is touched. Duplicate-number check guards
                            against another client already owning that number.
"""

import json
from datetime import date, datetime
from typing import Optional

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.fittbot_api.v1.client.client_api.home.calculate_macros import (
    activity_multipliers,
    calculate_bmr,
    calculate_macros,
)
from app.utils.logging_utils import FittbotHTTPException
from app.utils.otp import async_send_verification_sms
from app.utils.otp_security import (
    OTP_EXPIRY_SECONDS,
    check_otp_send_allowed,
    secure_generate_otp,
    secure_verify_otp,
)

from .repository import (
    ProfileRepository,
    new_lockout_identifier,
    old_lockout_identifier,
)
from .schemas import (
    InitiateContactChangeResponse,
    ProfileData,
    ProfileResponse,
    UpdateDetailsPayload,
    UpdateDetailsResponse,
    VerifyContactChangeResponse,
)


class ProfileService:
    """Orchestrates profile reads/writes + OTP-gated mobile change."""

    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis
        self.repo = ProfileRepository(db, redis)

    # ── 1. GET profile data (no email) ───────────────────────────────

    async def get_profile(self, client_id: int) -> ProfileResponse:
        client = await self.repo.get_client(client_id)
        if not client:
            raise FittbotHTTPException(
                status_code=404,
                detail="Client not found",
                error_code="PROFILE_CLIENT_NOT_FOUND",
                log_data={"client_id": client_id},
            )
        return ProfileResponse(data=self._client_to_profile_data(client))

    # ── 2. PUT non-contact details ───────────────────────────────────

    async def update_details(
        self, client_id: int, payload: UpdateDetailsPayload
    ) -> UpdateDetailsResponse:
        client = await self.repo.get_client(client_id)
        if not client:
            raise FittbotHTTPException(
                status_code=404,
                detail="Client not found",
                error_code="PROFILE_CLIENT_NOT_FOUND",
                log_data={"client_id": client_id},
            )

        # Track which fitness-relevant inputs actually changed so we know
        # whether to recompute the macro targets at the end.
        fitness_changed = False

        if payload.name is not None:
            client.name = payload.name

        if payload.gender is not None:
            client.gender = payload.gender

        if payload.dob is not None and payload.dob != client.dob:
            client.dob = payload.dob
            client.age = self._calculate_age(payload.dob)
            fitness_changed = True

        if payload.height is not None and payload.height != client.height:
            client.height = payload.height
            fitness_changed = True

        if payload.lifestyle is not None and payload.lifestyle != client.lifestyle:
            client.lifestyle = payload.lifestyle
            fitness_changed = True

        if payload.goal is not None and payload.goal != client.goals:
            client.goals = payload.goal
            fitness_changed = True

        targets_recalculated = False
        try:
            await self.repo.commit()

            if fitness_changed and self._can_calculate_targets(client):
                await self._recalculate_targets(client)
                await self.repo.commit()
                targets_recalculated = True
        except FittbotHTTPException:
            raise
        except Exception as e:
            await self.repo.rollback()
            raise FittbotHTTPException(
                status_code=500,
                detail="Failed to update profile",
                error_code="PROFILE_UPDATE_FAILED",
                log_data={"client_id": client_id, "error": str(e)},
            )

        return UpdateDetailsResponse(
            targets_recalculated=targets_recalculated,
            data=self._client_to_profile_data(client),
        )

    # ── 3a. Initiate contact change (send OTP to old + new) ──────────

    async def initiate_contact_change(
        self,
        client_id: int,
        new_contact: str,
        client_ip: Optional[str] = None,
    ) -> InitiateContactChangeResponse:
        client = await self.repo.get_client(client_id)
        if not client:
            raise FittbotHTTPException(
                status_code=404,
                detail="Client not found",
                error_code="PROFILE_CLIENT_NOT_FOUND",
                log_data={"client_id": client_id},
            )

        old_contact = client.contact
        if not old_contact:
            raise FittbotHTTPException(
                status_code=400,
                detail="No existing contact number on file. Cannot verify change.",
                error_code="PROFILE_NO_OLD_CONTACT",
                log_data={"client_id": client_id},
            )

        if new_contact == old_contact:
            raise FittbotHTTPException(
                status_code=400,
                detail="New mobile number is the same as the current one",
                error_code="PROFILE_CONTACT_UNCHANGED",
                log_data={"client_id": client_id},
            )

        # Authoritative duplicate check — another client must not already
        # own this number. (We re-check at verify time too, in case of races.)
        if await self.repo.is_contact_taken_by_other(new_contact, client_id):
            raise FittbotHTTPException(
                status_code=400,
                detail="This mobile number is already registered with another account",
                error_code="PROFILE_CONTACT_ALREADY_TAKEN",
                log_data={"client_id": client_id, "contact": new_contact},
            )

        # Rate-limit BOTH sends independently — abuse protection per phone+IP.
        old_check = await check_otp_send_allowed(self.redis, old_contact, client_ip)
        if not old_check.allowed:
            raise FittbotHTTPException(
                status_code=429,
                detail=old_check.reason or "Too many OTP requests. Try again later.",
                error_code="PROFILE_OTP_RATE_LIMIT_OLD",
                log_data={"client_id": client_id, "retry_after": old_check.retry_after},
            )
        new_check = await check_otp_send_allowed(self.redis, new_contact, client_ip)
        if not new_check.allowed:
            raise FittbotHTTPException(
                status_code=429,
                detail=new_check.reason or "Too many OTP requests. Try again later.",
                error_code="PROFILE_OTP_RATE_LIMIT_NEW",
                log_data={"client_id": client_id, "retry_after": new_check.retry_after},
            )

        # Generate distinct OTPs (test numbers may pin a fixed value).
        old_otp = secure_generate_otp(old_contact)
        new_otp = secure_generate_otp(new_contact)

        # Persist BOTH OTPs + the pending new-contact value in one round-trip
        # under their own namespace, so they can never collide with the
        # generic `otp:{phone}` keys used by signup / password reset.
        await self.repo.store_pending_contact_change(
            client_id=client_id,
            new_contact=new_contact,
            old_otp=old_otp,
            new_otp=new_otp,
        )

        sent_to_old = await async_send_verification_sms(old_contact, old_otp)
        sent_to_new = await async_send_verification_sms(new_contact, new_otp)

        if not sent_to_old and not sent_to_new:
            # Both providers failed — clean up so the user can retry cleanly.
            await self.repo.clear_pending_contact_change(client_id)
            raise FittbotHTTPException(
                status_code=502,
                detail="Failed to send verification SMS. Please try again.",
                error_code="PROFILE_OTP_SMS_FAILED",
                log_data={"client_id": client_id},
            )

        return InitiateContactChangeResponse(
            expires_in=OTP_EXPIRY_SECONDS,
            sent_to_old=bool(sent_to_old),
            sent_to_new=bool(sent_to_new),
        )

    # ── 3b. Verify both OTPs + commit the change ─────────────────────

    async def verify_contact_change(
        self,
        client_id: int,
        old_otp: str,
        new_otp: str,
    ) -> VerifyContactChangeResponse:
        client = await self.repo.get_client(client_id)
        if not client:
            raise FittbotHTTPException(
                status_code=404,
                detail="Client not found",
                error_code="PROFILE_CLIENT_NOT_FOUND",
                log_data={"client_id": client_id},
            )

        pending_new_contact = await self.repo.get_pending_contact(client_id)
        if not pending_new_contact:
            raise FittbotHTTPException(
                status_code=400,
                detail="No pending mobile-number change found. Please request a new OTP.",
                error_code="PROFILE_NO_PENDING_CHANGE",
                log_data={"client_id": client_id},
            )

        # Verify OLD-number OTP first. Each side has its own lockout
        # identifier so brute-forcing one doesn't poison the other.
        old_result = await secure_verify_otp(
            self.redis,
            self.repo.old_otp_redis_key(client_id),
            old_otp,
            identifier=old_lockout_identifier(client_id),
        )
        if not old_result.success:
            raise FittbotHTTPException(
                status_code=429 if old_result.locked else 400,
                detail=old_result.error_message
                or "Incorrect OTP for current mobile number.",
                error_code="PROFILE_OLD_OTP_INVALID",
                log_data={
                    "client_id": client_id,
                    "remaining_attempts": old_result.remaining_attempts,
                    "locked": old_result.locked,
                },
            )

        # Verify NEW-number OTP.
        new_result = await secure_verify_otp(
            self.redis,
            self.repo.new_otp_redis_key(client_id),
            new_otp,
            identifier=new_lockout_identifier(client_id),
        )
        if not new_result.success:
            raise FittbotHTTPException(
                status_code=429 if new_result.locked else 400,
                detail=new_result.error_message
                or "Incorrect OTP for new mobile number.",
                error_code="PROFILE_NEW_OTP_INVALID",
                log_data={
                    "client_id": client_id,
                    "remaining_attempts": new_result.remaining_attempts,
                    "locked": new_result.locked,
                },
            )

        # Race-guard: between initiate and verify, another client may have
        # claimed this number. Re-check before committing.
        if await self.repo.is_contact_taken_by_other(pending_new_contact, client_id):
            await self.repo.clear_pending_contact_change(client_id)
            raise FittbotHTTPException(
                status_code=409,
                detail="This mobile number was just registered by another account.",
                error_code="PROFILE_CONTACT_RACE_LOST",
                log_data={"client_id": client_id, "contact": pending_new_contact},
            )

        # Commit the change. Mark the verification flag mobile=true since both
        # sides of the change are proven.
        try:
            client.contact = pending_new_contact
            client.verification = json.dumps({"mobile": True, "password": True})
            await self.repo.commit()
        except Exception as e:
            await self.repo.rollback()
            raise FittbotHTTPException(
                status_code=500,
                detail="Failed to update mobile number",
                error_code="PROFILE_CONTACT_COMMIT_FAILED",
                log_data={"client_id": client_id, "error": str(e)},
            )

        await self.repo.clear_pending_contact_change(client_id)

        return VerifyContactChangeResponse(contact=pending_new_contact)

    # ── Internal helpers ─────────────────────────────────────────────

    @staticmethod
    def _client_to_profile_data(client) -> ProfileData:
        return ProfileData(
            name=client.name,
            profile=client.profile,
            contact=client.contact,
            gender=client.gender,
            dob=client.dob,
            age=client.age,
            height=client.height,
            lifestyle=client.lifestyle,
            goal=client.goals,
        )

    @staticmethod
    def _calculate_age(dob: date) -> int:
        today = date.today()
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    @staticmethod
    def _can_calculate_targets(client) -> bool:
        return bool(
            client.weight and client.height and client.age and client.lifestyle
        )

    async def _recalculate_targets(self, client) -> None:
        """Recompute calorie + macro targets after a fitness-field change.

        Mirrors the BMR/TDEE math used by the existing
        profile_pic.update_profile flow so behavior stays consistent.
        """
        bmr = calculate_bmr(client.weight, client.height, client.age)
        tdee = bmr * activity_multipliers.get(client.lifestyle, 1.2)

        if client.goals == "weight_loss":
            tdee -= 500
        elif client.goals == "weight_gain":
            tdee += 500

        protein, carbs, fat, _, _ = calculate_macros(tdee, client.goals or "maintenance")

        await self.repo.upsert_client_target(
            client_id=client.client_id,
            calories=int(tdee),
            protein=protein,
            carbs=carbs,
            fat=fat,
            updated_at=datetime.now(),
        )
