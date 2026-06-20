"""
Provider-agnostic credit endpoints: balance and history.

Both Google Play and Razorpay credits write to the same credit tables,
so these endpoints serve all providers.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.utils.idor_protection import get_verified_client_id

from .shared.credit_service import CreditService
from .shared.schemas import (
    CreditBalanceResponse,
    CreditHistoryResponse,
    CreditLedgerEntry,
)

router = APIRouter(prefix="/credits", tags=["Food Scanner Credits"])


def _get_db_session():
    from app.models.database import get_db

    session = next(get_db())
    try:
        yield session
    finally:
        session.close()


# ── Balance ─────────────────────────────────────────────────────────

@router.get("/balance", response_model=CreditBalanceResponse)
async def get_credit_balance(
    client_id: int = Depends(get_verified_client_id),
    db: Session = Depends(_get_db_session),
):
    credit_svc = CreditService(db)
    
    bal = credit_svc.get_balance(client_id)
    is_unlimited = credit_svc.is_unlimited_active(client_id)
    db.commit()
    return CreditBalanceResponse(
        customer_id=str(client_id),
        balance=bal.balance,
        total_purchased=bal.total_purchased,
        total_bonus=bal.total_bonus,
        total_used=bal.total_used,
        is_unlimited=is_unlimited,
        unlimited_until=(
            bal.unlimited_until.isoformat() if bal.unlimited_until else None
        ),
    )



# ── History ─────────────────────────────────────────────────────────

@router.get("/history", response_model=CreditHistoryResponse)
async def get_credit_history(
    client_id: int = Depends(get_verified_client_id),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(_get_db_session),
):
    credit_svc = CreditService(db)
    entries, total = credit_svc.get_history(
        client_id, limit=limit, offset=offset
    )
    return CreditHistoryResponse(
        customer_id=str(client_id),
        entries=[
            CreditLedgerEntry(
                id=e.id,
                txn_type=e.txn_type,
                credits=e.credits,
                balance_after=e.balance_after,
                source_order_id=e.source_order_id,
                source_subscription_id=e.source_subscription_id,
                description=e.description,
                expires_at=e.expires_at.isoformat() if e.expires_at else None,
                created_at=e.created_at.isoformat() if e.created_at else "",
            )
            for e in entries
        ],
        total=total,
    )
