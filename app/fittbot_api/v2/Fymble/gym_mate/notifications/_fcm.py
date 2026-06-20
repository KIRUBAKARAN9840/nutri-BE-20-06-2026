"""Thin wrapper around firebase-admin for gym_mate push delivery.

Reuses the same FCM SDK that powers `app.fittbot_api.v1.notifications` —
no second initialization. The wrapper centralises payload building so
all gym_mate pushes look the same on the device (channel, priority,
data shape).
"""

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import firebase_admin
from firebase_admin import credentials, messaging


logger = logging.getLogger("gymmate.notifications.fcm")


# ── Initialisation ─────────────────────────────────────────────────────────

def _init_firebase() -> None:
    """Idempotent — no-op if firebase_admin is already initialised in
    this process (which it will be in production, by the v1 notifications
    module's startup hook). Repeats the same service-account path so
    Celery workers that import only this module still get a working SDK.
    """
    if firebase_admin._apps:
        return
    sa_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "..", "..", "..", "..",  # → repo root
        "firebase",
        "fittbot-c72eb-firebase-adminsdk-fbsvc-bfc6a7f7e9.json",
    )
    sa_path = os.path.normpath(sa_path)
    if not os.path.exists(sa_path):
        logger.warning(
            "gymmate FCM: service account not found at %s — pushes will fail",
            sa_path,
        )
        return
    cred = credentials.Certificate(sa_path)
    firebase_admin.initialize_app(cred)


# Initialise on import so Celery workers don't need to call this
# explicitly when they import _tasks → _fcm.
_init_firebase()


# ── Sender ────────────────────────────────────────────────────────────────

GYMMATE_ANDROID_CHANNEL = "gymmate_notifications"


def _is_expo_token(token: str) -> bool:
    return token.startswith("ExponentPushToken[") or token.startswith("ExpoPushToken[")


def _send_expo(
    tokens: List[str],
    *,
    title: str,
    body: Optional[str],
    data: Optional[Dict[str, Any]] = None,
) -> Tuple[int, int, List[str], List[str]]:
    """Push to Expo tokens via exponent_server_sdk. Same return shape as
    the FCM path so the caller can merge results transparently."""
    try:
        from exponent_server_sdk import PushClient, PushMessage, PushServerError
    except ImportError:
        return (0, len(tokens), ["exponent_server_sdk not installed"], [])

    messages = [
        PushMessage(
            to=t, title=title, body=body or "",
            sound="default", priority="high",
            channel_id=GYMMATE_ANDROID_CHANNEL,
            data=data or {},
        )
        for t in tokens
    ]
    try:
        responses = PushClient().publish_multiple(messages)
    except Exception as exc:
        logger.exception("gymmate Expo push failed")
        return (0, len(tokens), [str(exc)], [])

    success = 0
    failure = 0
    errors: List[str] = []
    invalid: List[str] = []
    for tok, resp in zip(tokens, responses):
        if resp.status == "ok":
            success += 1
        else:
            failure += 1
            err = getattr(resp.details, "error", None) if resp.details else None
            errors.append(str(err or resp.status))
            if err in ("DeviceNotRegistered", "InvalidCredentials"):
                invalid.append(tok)
    return (success, failure, errors, invalid)


def send_multicast(
    tokens: List[str],
    *,
    title: str,
    body: Optional[str],
    data: Optional[Dict[str, Any]] = None,
) -> Tuple[int, int, List[str], List[str]]:
    """Send one push payload to many tokens. Routes Expo-shaped tokens
    (ExponentPushToken[...]) through the Expo Push Service and raw FCM
    tokens through Firebase Admin — caller doesn't have to know which.

    Returns: (success_count, failure_count, error_messages, invalid_tokens)
        invalid_tokens — tokens the provider said are unregistered /
                         not found; caller should drop them from the DB.
    """
    if not tokens:
        return (0, 0, [], [])

    # Split by provider
    expo_tokens = [t for t in tokens if _is_expo_token(t)]
    fcm_tokens = [t for t in tokens if not _is_expo_token(t)]

    success = failure = 0
    errors: List[str] = []
    invalid: List[str] = []

    if expo_tokens:
        s, f, e, inv = _send_expo(
            expo_tokens, title=title, body=body, data=data,
        )
        success += s
        failure += f
        errors.extend(e)
        invalid.extend(inv)

    if not fcm_tokens:
        return (success, failure, errors, invalid)

    if not firebase_admin._apps:
        return (
            success,
            failure + len(fcm_tokens),
            errors + ["firebase not initialised"],
            invalid,
        )

    notification = messaging.Notification(title=title, body=body or "")
    android_config = messaging.AndroidConfig(
        priority="high",
        notification=messaging.AndroidNotification(
            channel_id=GYMMATE_ANDROID_CHANNEL,
            sound="default",
        ),
    )
    apns_config = messaging.APNSConfig(
        payload=messaging.APNSPayload(
            aps=messaging.Aps(
                alert=messaging.ApsAlert(title=title, body=body or ""),
                mutable_content=True,
                sound="default",
            ),
        ),
    )

    # FCM data: must be flat dict of strings. The frontend deep-link
    # handler reads `data` and switches on `type` to pick a screen.
    str_data: Dict[str, str] = {}
    if data:
        for k, v in data.items():
            if v is None:
                continue
            # Nested dicts → JSON-encoded strings (frontend parses).
            if isinstance(v, (dict, list)):
                import json
                str_data[k] = json.dumps(v)
            else:
                str_data[k] = str(v)

    message = messaging.MulticastMessage(
        tokens=fcm_tokens,
        notification=notification,
        android=android_config,
        apns=apns_config,
        data=str_data,
    )

    try:
        response = messaging.send_each_for_multicast(message)
    except Exception as exc:
        logger.exception("gymmate FCM: multicast send failed")
        return (success, failure + len(fcm_tokens), errors + [str(exc)], invalid)

    for i, resp in enumerate(response.responses):
        if resp.exception:
            err = str(resp.exception)
            errors.append(err)
            upper = err.upper()
            if "UNREGISTERED" in upper or "NOT_FOUND" in upper or "INVALID_ARGUMENT" in upper:
                invalid.append(fcm_tokens[i])

    return (
        success + int(response.success_count),
        failure + int(response.failure_count),
        errors,
        invalid,
    )
