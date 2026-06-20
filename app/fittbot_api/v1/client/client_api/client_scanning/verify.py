from __future__ import annotations

import hashlib
import logging
from base64 import b64decode
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.models.dailypass_models import (
    DailyPassDay,
    DailyPassAudit,
    LedgerAllocation,
)
from app.models.fittbot_models import (
    ClassSession,
    Gym,
    SessionBookingAudit,
    SessionBookingDay,
    SessionPurchase,
)
from app.models.fittbot_payments_models import Payment, Payout
from app.utils.aes_encryption import decrypt_gym_id
from app.tasks.notification_tasks import queue_dailypass_checkin_notification, queue_session_checkin_notification, queue_scan_alert_notification

logger = logging.getLogger("client.scanning")

# Maximum length of raw gym_id we log verbatim. Encrypted iv:ct strings are
# typically <80 chars; anything larger is almost certainly garbage and we
# truncate to keep log volume bounded.
_MAX_RAW_LOG = 200


def _truncate(value: Optional[str], limit: int = _MAX_RAW_LOG) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    if len(value) <= limit:
        return value
    return value[:limit] + f"...<truncated {len(value) - limit}>"


def _classify_decrypt_failure(raw) -> str:
    """Bucket the reason `decrypt_gym_id` failed so we can filter on it.

    Possible values:
      empty                  - gym_id was missing or blank
      not_a_string           - pydantic should prevent this; safety net
      plain_int              - looks like a bare integer (producer never encrypted)
      no_colon_separator     - non-empty string without the iv:ct delimiter
      bad_base64             - has ":" but iv or ct fail base64 decoding
      decrypt_or_unpad_failed- correct shape, key/data mismatch
    """
    if raw is None:
        return "empty"
    if not isinstance(raw, str):
        return "not_a_string"
    if raw == "":
        return "empty"
    if ":" not in raw:
        if raw.isdigit():
            return "plain_int"
        return "no_colon_separator"
    try:
        iv_b64, ct_b64 = raw.split(":", 1)
        b64decode(iv_b64 + "==")
        b64decode(ct_b64 + "==")
    except Exception:
        return "bad_base64"
    return "decrypt_or_unpad_failed"


def _hash_token(token: Optional[str]) -> Optional[str]:
    """Short stable hash of a sensitive token, suitable for log correlation."""
    if not token:
        return None
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]

router = APIRouter(prefix="/client_scanning", tags=["Client Scanning - Unified Verify"])

from app.services.timezone_utils import IST, now_ist as _now_ist, today_ist as _today_ist



class ScanVerifyRequest(BaseModel):
    mode: Literal["dailypass", "sessions"] = Field(
        ..., description="Type of scan: 'dailypass' or 'session'"
    )
    gym_id: str = Field(..., description="AES-encrypted gym_id")
    day_id: Optional[str] = Field(None, description="DailyPassDay id (required for mode=dailypass)")
    checkin_token: Optional[str] = Field(None, description="Session checkin token (required for mode=session)")


class ScanVerifyResponse(BaseModel):
    status: int
    message: str
    already_attended: bool = False
    session_id: Optional[int] = None
    mode: str


# ---------------------------------------------------------------------------
# POST ning/verify
# ---------------------------------------------------------------------------

@router.post("/verify")
async def unified_scan_verify(
    body: ScanVerifyRequest,
    db: AsyncSession = Depends(get_async_db),
):
    raw_gym_id = body.gym_id
    raw_len = len(raw_gym_id) if isinstance(raw_gym_id, str) else -1

    logger.info(
        "scan_attempt",
        extra={
            "event": "scan_attempt",
            "mode": body.mode,
            "gym_id_raw": _truncate(raw_gym_id),
            "gym_id_raw_len": raw_len,
            "day_id": body.day_id,
            "checkin_token_hash": _hash_token(body.checkin_token),
        },
    )

    try:
        gym_id: int = decrypt_gym_id(raw_gym_id)
    except ValueError as exc:
        reason = _classify_decrypt_failure(raw_gym_id)
        logger.warning(
            "scan_decrypt_failed",
            extra={
                "event": "scan_decrypt_failed",
                "reason": reason,
                "mode": body.mode,
                "gym_id_raw": _truncate(raw_gym_id),
                "gym_id_raw_len": raw_len,
                "day_id": body.day_id,
                "checkin_token_hash": _hash_token(body.checkin_token),
                "exception": str(exc),
            },
        )
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid encrypted gym_id")

    logger.info(
        "scan_decrypt_ok",
        extra={
            "event": "scan_decrypt_ok",
            "mode": body.mode,
            "gym_id": gym_id,
            "day_id": body.day_id,
            "checkin_token_hash": _hash_token(body.checkin_token),
        },
    )

    if body.mode == "dailypass":
        return await _verify_dailypass(body, gym_id, db)
    else:
        return await _verify_session(body, gym_id, db)
    

@router.get("/get_gym_id")
async def health_check(gym_id: str, db: AsyncSession = Depends(get_async_db)):
    try:
        decrypted_id = decrypt_gym_id(gym_id)
        print("decrypted gym id is",decrypted_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid encrypted gym_id")

    result = await db.execute(select(Gym).where(Gym.gym_id == decrypted_id))
    gym = result.scalars().first()
    if not gym:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gym not found")

    return {"status": 200, "gym_id": decrypted_id, "gym_name": gym.name}

# ---------------------------------------------------------------------------
# Daily-pass verification flow
# ---------------------------------------------------------------------------

async def _verify_dailypass(body: ScanVerifyRequest, gym_id: int, db: AsyncSession) -> ScanVerifyResponse:
    if not body.day_id:
        logger.warning(
            "scan_rejected",
            extra={
                "event": "scan_rejected",
                "reason": "missing_day_id",
                "mode": "dailypass",
                "gym_id": gym_id,
            },
        )
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "day_id is required for mode=dailypass")

    try:
        data_result = await db.execute(
            select(DailyPassDay)
            .where(DailyPassDay.id == body.day_id)
            .with_for_update(nowait=False)
        )
        data: Optional[DailyPassDay] = data_result.scalars().first()

        if not data:
            logger.warning(
                "scan_rejected",
                extra={
                    "event": "scan_rejected",
                    "reason": "day_not_found",
                    "mode": "dailypass",
                    "gym_id": gym_id,
                    "day_id": body.day_id,
                },
            )
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Invalid or expired check-in token")

        if data.status == "attended":
            logger.info(
                "scan_already_attended",
                extra={
                    "event": "scan_already_attended",
                    "mode": "dailypass",
                    "gym_id": gym_id,
                    "day_id": data.id,
                    "client_id": data.client_id,
                    "checkin_at": data.checkin_at.isoformat() if data.checkin_at else None,
                },
            )
            return ScanVerifyResponse(
                status=200,
                already_attended=True,
                message="Already checked-in. Repeat scan ignored.",
                session_id=1,
                mode="dailypass",
            )

        if data.gym_id != str(gym_id):
            logger.warning(
                "scan_rejected",
                extra={
                    "event": "scan_rejected",
                    "reason": "gym_mismatch",
                    "mode": "dailypass",
                    "gym_id_scanned": gym_id,
                    "gym_id_booked": data.gym_id,
                    "day_id": data.id,
                    "client_id": data.client_id,
                },
            )
            try:
                queue_scan_alert_notification(
                    gym_id=int(data.gym_id),
                    client_id=int(data.client_id),
                    reason="their DailyPass is booked for a different gym",
                )
            except Exception:
                pass
            return ScanVerifyResponse(
                status=403,
                message="Pass-day belongs to a different gym",
                mode="dailypass",
            )

        if data.scheduled_date != date.today():
            logger.warning(
                "scan_rejected",
                extra={
                    "event": "scan_rejected",
                    "reason": "date_mismatch",
                    "mode": "dailypass",
                    "gym_id": gym_id,
                    "day_id": data.id,
                    "client_id": data.client_id,
                    "scheduled_date": data.scheduled_date.isoformat() if data.scheduled_date else None,
                    "today": date.today().isoformat(),
                },
            )
            try:
                queue_scan_alert_notification(
                    gym_id=int(data.gym_id),
                    client_id=int(data.client_id),
                    reason="their DailyPass is not scheduled for today",
                )
            except Exception:
                pass
            return ScanVerifyResponse(
                status=409,
                message="Check-in date mismatch. Can only check-in on the booked date.",
                mode="dailypass",
            )

        # Mark attended
        data.status = "attended"
        data.checkin_at = datetime.now()
        db.add(data)

        # Mark ledger allocation ready for payout
        alloc_result = await db.execute(
            select(LedgerAllocation).where(LedgerAllocation.pass_day_id == data.id)
        )
        alloc: Optional[LedgerAllocation] = alloc_result.scalars().first()

        if alloc:
            alloc.status = "ready_for_payout"
            alloc.marked_ready_at = _now_ist()
            db.add(alloc)

        # Audit
        db.add(
            DailyPassAudit(
                daily_pass_id=body.day_id,
                action="checkin",
                details="Checked-in at gym",
                timestamp=_now_ist(),
                client_id=data.client_id,
                actor="gym_scanner",
            )
        )

        await db.commit()

        # Create payout from payment
        payment_result = await db.execute(
            select(Payment).where(Payment.entitlement_id == str(data.id))
        )
        payment_row: Optional[Payment] = payment_result.scalars().first()

        if payment_row:
            locked_payment = await db.execute(
                select(Payment)
                .where(Payment.id == payment_row.id)
                .with_for_update(nowait=False)
            )
            locked_payment.scalars().first()

            existing_payout = await db.execute(
                select(Payout).where(Payout.payment_id == payment_row.id)
            )
            if existing_payout.scalars().first():
                logger.info(
                    "[DAILYPASS_PAYOUT_EXISTS] payment_id=%s, day_id=%s - skipping duplicate",
                    payment_row.id, data.id,
                )
            else:
                payout = Payout(
                    payment_id=payment_row.id,
                    gym_id=int(data.gym_id),
                    gym_owner_id=None,
                    amount_gross=payment_row.amount_net,
                    amount_net=payment_row.amount_net,
                    status="ready_for_transfer",
                )
                db.add(payout)
                await db.commit()
                logger.info(
                    "[DAILYPASS_PAYOUT_CREATED] payment_id=%s, day_id=%s",
                    payment_row.id, data.id,
                )
        else:
            logger.warning("[DAILYPASS_PAYMENT_NOT_FOUND] entitlement_id=%s", data.id)

        # Notify gym owner (fire-and-forget)
        try:
            queue_dailypass_checkin_notification(
                gym_id=int(data.gym_id),
                client_id=int(data.client_id),
            )
        except Exception:
            logger.warning("[DAILYPASS_CHECKIN_NOTIFICATION_ERROR] day_id=%s", data.id)

        logger.info(
            "scan_completed",
            extra={
                "event": "scan_completed",
                "mode": "dailypass",
                "gym_id": gym_id,
                "day_id": data.id,
                "client_id": data.client_id,
            },
        )

        return ScanVerifyResponse(
            status=200,
            already_attended=False,
            message="Check-in recorded & payout flagged",
            session_id=1,
            mode="dailypass",
        )

    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.exception(
            "scan_unexpected_error",
            extra={
                "event": "scan_unexpected_error",
                "mode": "dailypass",
                "gym_id": gym_id,
                "day_id": body.day_id,
                "exception": str(e),
            },
        )
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Scan verify failed: {e}")


async def _verify_session(body: ScanVerifyRequest, gym_id: int, db: AsyncSession) -> ScanVerifyResponse:

    token_hash = _hash_token(body.checkin_token)

    if not body.checkin_token:
        logger.warning(
            "scan_rejected",
            extra={
                "event": "scan_rejected",
                "reason": "missing_checkin_token",
                "mode": "sessions",
                "gym_id": gym_id,
            },
        )
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "checkin_token is required for mode=session")

    today = _today_ist()
    token = body.checkin_token.strip()

    try:
        booking_result = await db.execute(
            select(SessionBookingDay)
            .where(SessionBookingDay.checkin_token == token)
            .with_for_update(nowait=False)
        )
        booking: Optional[SessionBookingDay] = booking_result.scalars().first()

        if not booking:
            logger.warning(
                "scan_rejected",
                extra={
                    "event": "scan_rejected",
                    "reason": "booking_not_found",
                    "mode": "sessions",
                    "gym_id": gym_id,
                    "checkin_token_hash": token_hash,
                },
            )
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Invalid or expired check-in token")

        if booking.gym_id != gym_id:
            logger.warning(
                "scan_rejected",
                extra={
                    "event": "scan_rejected",
                    "reason": "gym_mismatch",
                    "mode": "sessions",
                    "gym_id_scanned": gym_id,
                    "gym_id_booked": booking.gym_id,
                    "booking_id": booking.id,
                    "session_id": booking.session_id,
                    "checkin_token_hash": token_hash,
                },
            )
            try:
                queue_scan_alert_notification(
                    gym_id=booking.gym_id,
                    client_id=int(booking.client_id) if hasattr(booking, 'client_id') else 0,
                    reason="their session is booked for a different gym",
                )
            except Exception:
                pass
            return ScanVerifyResponse(
                status=403,
                message="Booking belongs to a different gym",
                mode="session",
            )

        if booking.status == "attended":
            logger.info(
                "scan_already_attended",
                extra={
                    "event": "scan_already_attended",
                    "mode": "sessions",
                    "gym_id": gym_id,
                    "booking_id": booking.id,
                    "session_id": booking.session_id,
                    "checkin_token_hash": token_hash,
                },
            )
            return ScanVerifyResponse(
                status=200,
                message="Already checked-in. Repeat scan ignored.",
                already_attended=True,
                mode="session",
            )

        if booking.booking_date != today:
            logger.warning(
                "scan_rejected",
                extra={
                    "event": "scan_rejected",
                    "reason": "date_mismatch",
                    "mode": "sessions",
                    "gym_id": gym_id,
                    "booking_id": booking.id,
                    "session_id": booking.session_id,
                    "booking_date": booking.booking_date.isoformat() if booking.booking_date else None,
                    "today": today.isoformat(),
                    "checkin_token_hash": token_hash,
                },
            )
            try:
                queue_scan_alert_notification(
                    gym_id=booking.gym_id,
                    client_id=int(booking.client_id) if hasattr(booking, 'client_id') else 0,
                    reason="their session is not scheduled for today",
                )
            except Exception:
                pass
            return ScanVerifyResponse(
                status=409,
                message="Check-in date mismatch. Can only check-in on the booked date.",
                mode="session",
            )

        # Validate purchase
        purchase_result = await db.execute(
            select(SessionPurchase).where(SessionPurchase.id == booking.purchase_id)
        )
        purchase: Optional[SessionPurchase] = purchase_result.scalars().first()

        if not purchase:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Purchase not found for booking")
        if purchase.status != "paid":
            raise HTTPException(status.HTTP_409_CONFLICT, "Payment not completed for this booking")
        if not purchase.sessions_count or purchase.sessions_count <= 0:
            raise HTTPException(status.HTTP_409_CONFLICT, "Invalid session count on purchase")

        # Validate session exists
        session_result = await db.execute(
            select(ClassSession).where(ClassSession.id == booking.session_id)
        )
        session_row: Optional[ClassSession] = session_result.scalars().first()
        if not session_row:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")

        # Validate payment record
        payment_result = await db.execute(
            select(Payment).where(Payment.entitlement_id == token)
        )
        payment_row: Optional[Payment] = payment_result.scalars().first()

        if not payment_row:
            logger.error("Payment not found for entitlement_id: %s", token)
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment record not found for this booking")

        # Mark attended
        booking.status = "attended"
        booking.scanned_at = _now_ist()
        db.add(booking)

        # Audit
        db.add(
            SessionBookingAudit(
                purchase_id=booking.purchase_id,
                booking_day_id=booking.id,
                event="checkin",
                actor_role="gym_scanner",
                actor_id=gym_id,
                notes={"token": token},
            )
        )

        locked_payment = await db.execute(
            select(Payment)
            .where(Payment.id == payment_row.id)
            .with_for_update(nowait=False)
        )
        locked_payment.scalars().first()

        existing_payout = await db.execute(
            select(Payout).where(Payout.payment_id == payment_row.id)
        )

        if existing_payout.scalars().first():
            logger.info(
                "[SESSION_PAYOUT_EXISTS] payment_id=%s - skipping duplicate",
                payment_row.id,
            )
        else:
            db.add(
                Payout(
                    payment_id=payment_row.id,
                    gym_id=booking.gym_id,
                    gym_owner_id=None,
                    amount_gross=payment_row.amount_net,
                    amount_net=payment_row.amount_net,
                    status="ready_for_transfer",
                )
            )

        await db.commit()

        # Notify gym owner (fire-and-forget)
        try:
            queue_session_checkin_notification(
                gym_id=booking.gym_id,
                client_id=int(purchase.client_id),
                session_name=session_row.internal if session_row.internal else "session",
            )
        except Exception:
            logger.warning("[SESSION_CHECKIN_NOTIFICATION_ERROR] session_id=%s", booking.session_id)

        logger.info(
            "scan_completed",
            extra={
                "event": "scan_completed",
                "mode": "sessions",
                "gym_id": gym_id,
                "booking_id": booking.id,
                "session_id": booking.session_id,
                "client_id": str(purchase.client_id),
                "checkin_token_hash": token_hash,
            },
        )

        return ScanVerifyResponse(
            status=200,
            message="Check-in recorded & payment captured",
            already_attended=False,
            session_id=booking.session_id,
            mode="session",
        )

    except HTTPException:
        await db.rollback()
        raise
    except Exception as exc:
        await db.rollback()
        logger.exception(
            "scan_unexpected_error",
            extra={
                "event": "scan_unexpected_error",
                "mode": "sessions",
                "gym_id": gym_id,
                "checkin_token_hash": token_hash,
                "exception": str(exc),
            },
        )
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to verify session scan")

