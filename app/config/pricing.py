from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config.settings import settings

_IST = ZoneInfo("Asia/Kolkata")

WALKAWAY_DISCOUNT_PERCENT = 5

def get_markup_percent() -> int:
    """Return the platform markup percentage (e.g. 10 for 10%)."""
    return settings.platform_markup_percent

def get_markup_multiplier() -> float:
    """Return the multiplier to apply to base prices (e.g. 1.10 for 10%)."""
    return 1 + (settings.platform_markup_percent / 100)

DAILYPASS_PRICE_FLOOR_RUPEES = 90

def compute_dailypass_price_rupees(owner_base_paise: int) -> int:

    base_rupees = max((owner_base_paise or 0) / 100, DAILYPASS_PRICE_FLOOR_RUPEES)
    return round(base_rupees * get_markup_multiplier())

def compute_dailypass_price_paise(owner_base_paise: int) -> int:
   
    return compute_dailypass_price_rupees(owner_base_paise) * 100

def compute_session_price_rupees(owner_final_price: int) -> int:

    return round((owner_final_price or 0) * get_markup_multiplier())

def get_daily_offer_discount() -> int:
    
    if not settings.membership_daily_offer_enabled:
        return 0

    today = datetime.now(_IST).day
    if today % 2 != 0:  
        return 0

    return settings.membership_daily_offer_discount

def get_walkaway_visited_key(client_id: int) -> str:
    today = datetime.now(_IST).strftime("%Y-%m-%d")
    return f"membership:visited:{client_id}:{today}"

def get_walkaway_redis_key(client_id: int) -> str:
    today = datetime.now(_IST).strftime("%Y-%m-%d")
    return f"membership:walkaway:{client_id}:{today}"

def get_seconds_until_midnight_ist() -> int:
    now = datetime.now(_IST)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(int((midnight - now).total_seconds()), 1)

def apply_walkaway_discount(amount: int) -> int:
    discount = int(amount * WALKAWAY_DISCOUNT_PERCENT / 100)
    return max(amount - discount, 0)


