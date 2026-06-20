

import json
import logging
from typing import Optional

from fastapi import APIRouter, Request

from .schemas import NutritionAdVisitResponse
from .service import NutritionAdService

router = APIRouter(
    prefix="/nutrition_ad",
    tags=["Fymble Nutrition Ad Tracking"],
)

logger = logging.getLogger("nutrition_ad")

# Column widths in app.models.nutrition_models.NutritionAd -- truncate to
# match so a too-long header can't trip MySQL.
_MAX_VISITOR = 64
_MAX_IP = 45
_MAX_UA = 512
_MAX_REF = 500
_MAX_LANG = 100


def _client_ip(request: Request) -> Optional[str]:
    try:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            ip = forwarded.split(",")[0].strip()
            return ip[:_MAX_IP] if ip else None
        if request.client and request.client.host:
            return request.client.host[:_MAX_IP]
    except Exception:
        pass
    return None


def _trim(value: Optional[str], limit: int) -> Optional[str]:
    if not value:
        return None
    try:
        return value[:limit]
    except Exception:
        return None


async def _parse_visitor_id(request: Request) -> Optional[str]:
    """Read visitor_id from the body without ever raising."""
    try:
        body_bytes = await request.body()
        if not body_bytes:
            return None
        payload = json.loads(body_bytes)
        if not isinstance(payload, dict):
            return None
        vid = payload.get("visitor_id")
        if isinstance(vid, str) and vid.strip():
            return vid.strip()[:_MAX_VISITOR]
    except Exception:
        pass
    return None


@router.post("/visit", response_model=NutritionAdVisitResponse)
async def record_nutrition_ad_visit(request: Request) -> NutritionAdVisitResponse:
    visitor_id = await _parse_visitor_id(request)

    headers = request.headers
    ip_address = _client_ip(request)
    user_agent = _trim(headers.get("user-agent"), _MAX_UA)
    referrer = _trim(headers.get("referer"), _MAX_REF)
    accept_language = _trim(headers.get("accept-language"), _MAX_LANG)

    visit_id: Optional[int] = None
    try:
        visit_id = await NutritionAdService().record_visit(
            visitor_id=visitor_id,
            ip_address=ip_address,
            user_agent=user_agent,
            referrer=referrer,
            accept_language=accept_language,
        )
    except Exception as exc:
        logger.warning("nutrition_ad insert failed: %s", exc)

    return NutritionAdVisitResponse(visit_id=visit_id)
