
from typing import Dict, Any, Optional

# ── Constants ───────────────────────────────────────────────

REWARD_PERCENT = 0.10           # 10%
REWARD_CAP_RUPEES = 100         # max reward in rupees
REWARD_CAP_MINOR = REWARD_CAP_RUPEES * 100  # 10 000 paisa


def calculate_reward(
    amount_minor: int,
    available_cash_rupees: float,
    max_cap_minor: Optional[int] = None,
) -> Dict[str, Any]:

    ten_percent_minor = int(amount_minor * REWARD_PERCENT)
    capped_minor = min(ten_percent_minor, max_cap_minor) if max_cap_minor is not None else ten_percent_minor

    available_cash_minor = int(available_cash_rupees * 100)

    # Apply the lesser of capped reward or available cash
    raw_reward = min(available_cash_minor, capped_minor)
    # Round to nearest rupee (in minor units)
    reward_minor = int(round(raw_reward / 100) * 100)

    return {
        "reward_applied": reward_minor > 0,
        "reward_amount_minor": reward_minor,
        "reward_amount_rupees": reward_minor / 100,
        "ten_percent_cap_minor": ten_percent_minor,
        "available_fittbot_cash_minor": available_cash_minor,
        "available_fittbot_cash_rupees": available_cash_rupees,
        "calculation_base": "service_amount",
        "max_reward_cap": (max_cap_minor / 100) if max_cap_minor is not None else None,
    }
