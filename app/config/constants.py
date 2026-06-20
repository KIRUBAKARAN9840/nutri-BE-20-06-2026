"""
Application-wide constants.

Centralizes magic numbers that were hardcoded across 30+ endpoint files.
Import from here instead of using raw literals.
"""

# ── Pricing ─────────────────────────────────────────────────

OFFER_PRICE_RUPEES = 49
OFFER_PRICE_PAISE = 4900
GYM_OFFER_USER_CAP = 50  # max users per gym at offer price

# ── Rewards ─────────────────────────────────────────────────

REWARD_PERCENT = 0.10           # 10%
REWARD_CAP_RUPEES = 100
REWARD_CAP_PAISE = 10_000

# ── Commission / Royalty ────────────────────────────────────

ROYALTY_PERCENT = 0.20  # 20% royalty

# ── OTP ─────────────────────────────────────────────────────

OTP_EXPIRY_SECONDS = 300        # 5 minutes
OTP_RESEND_COOLDOWN_SECONDS = 60

# ── Cache TTLs (seconds) ───────────────────────────────────

CACHE_TTL_SHORT = 300           # 5 minutes
CACHE_TTL_MEDIUM = 3_600        # 1 hour
CACHE_TTL_LONG = 86_400         # 24 hours
CACHE_TTL_WEEK = 604_800        # 7 days

# ── S3 Presigned URLs ──────────────────────────────────────

PRESIGN_EXPIRY_SECONDS = 600    # 10 minutes

# ── File Size Limits ────────────────────────────────────────

MAX_AVATAR_SIZE = 1 * 1024 * 1024        # 1 MB
MAX_PHOTO_SIZE = 10 * 1024 * 1024        # 10 MB
MAX_DOCUMENT_SIZE = 25 * 1024 * 1024     # 25 MB

# ── Subscription Plans ──────────────────────────────────────

PLAN_NAMES = {
    "one_month_plan": "Gold",
    "six_month_plan": "Platinum",
    "twelve_month_plan": "Diamond",
}

# ── Nutrition ───────────────────────────────────────────────

ACTIVITY_MULTIPLIERS = {
    "sedentary": 1.2,
    "lightly_active": 1.375,
    "moderately_active": 1.55,
    "very_active": 1.725,
    "super_active": 1.9,
}

WATER_INTAKE_LITRES = {
    "male": 3.7,
    "female": 2.7,
    "default": 3.0,
}
