
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from .shared.credit_service import CreditService, InsufficientCreditsError

class CreditsModule:
    """Entry point for other modules to interact with credits."""

    def __init__(self, db: Session):
        self._svc = CreditService(db)

    def get_balance(self, client_id: int) -> int:
        return self._svc.get_balance(client_id).balance

    def deduct_credit(
        self, client_id: int, amount: int = 1, *, description: str = "Food scan"
    ) -> int:
        """
        Deduct credits. Returns new balance.
        Raises InsufficientCreditsError if not enough credits.
        """
        return self._svc.deduct_credit(
            client_id, amount=amount, description=description
        )

    def grant_bonus_credits(
        self,
        client_id: int,
        credits: int,
        *,
        source_subscription_id: str = "",
        description: str = "Subscription bonus",
        expires_at: Optional[datetime] = None,
    ) -> int:
        """Grant free credits with optional expiry. Returns new balance."""
        return self._svc.grant_credits(
            client_id=client_id,
            credits=credits,
            txn_type="subscription_bonus",
            source_subscription_id=source_subscription_id,
            description=description,
            expires_at=expires_at,
        )
