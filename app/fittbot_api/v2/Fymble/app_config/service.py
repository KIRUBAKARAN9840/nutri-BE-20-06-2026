"""Business logic for the unified app config check.

Single API call on app launch → returns one of:
  1. maintenance  — app is under maintenance (blocks everything)
  2. redirect     — app is suspended, go to store
  3. force_update — new version required
  4. ok           — let the user in
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from .repository import AppConfigRepository


def _parse_version(value: Optional[str]) -> list[int]:
    if not value:
        return [0]
    parts = []
    for segment in str(value).split("."):
        digits = "".join(ch for ch in segment if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return parts or [0]


def _is_version_lower(current: str, minimum: str) -> bool:
    cur = _parse_version(current)
    min_ = _parse_version(minimum)
    length = max(len(cur), len(min_))
    cur += [0] * (length - len(cur))
    min_ += [0] * (length - len(min_))
    return cur < min_


class AppConfigService:

    def __init__(self, db: AsyncSession):
        self.repo = AppConfigRepository(db)

    async def check(self, app: str, current_version: str, platform: Optional[str]) -> dict:
        """Single entry point — checks maintenance, redirect, then version."""

        # ── 1. Maintenance / Redirect ───────────────────────────────
        redirect = await self.repo.get_active_redirect(app)

        if redirect:
            return {
                "status": 200,
                "action": redirect.type,            # "maintenance" or "redirect"
                "message": redirect.message,
                "play_store_url": redirect.play_store_url,
                "app_store_url": redirect.app_store_url,
            }

        # ── 2. Force Update ─────────────────────────────────────────
        platform_key = app if app == "business" else "fittbot"
        version_record = await self.repo.get_version_record(platform_key)

        if version_record:
            needs_update = version_record.force_update
            if version_record.min_supported_version:
                needs_update = needs_update or _is_version_lower(
                    current_version, version_record.min_supported_version
                )

            if needs_update:
                return {
                    "status": 200,
                    "action": "force_update",
                    "message": version_record.message,
                    "update_url": version_record.update_url,
                    "button_label": version_record.button_label,
                    "current_version": version_record.current_version,
                }

        # ── 3. All clear ────────────────────────────────────────────
        return {
            "status": 200,
            "action": "ok",
        }
