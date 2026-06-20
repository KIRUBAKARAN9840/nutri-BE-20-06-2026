"""
V2 subscription payment flow for Google Play (RevenueCat) and Razorpay.

Platform-specific modules handle checkout, verify, and webhook processing
with a shared side-effects service for nutrition, rewards, and referrals.
"""

from .googleplay.routes import router as googleplay_subscription_router
from .razorpay.routes import router as razorpay_subscription_router

__all__ = [
    "googleplay_subscription_router",
    "razorpay_subscription_router",
]
