from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer

from app.models.database import Base


class AdRegistration(Base):
    """One row per signup that came in through an ad. Row presence == true;
    no row == not from an ad. Written by /register when the request body
    flag `is_from_ad` is true.
    """

    __tablename__ = "ad_registerations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    __table_args__ = (
        Index("ix_ad_registerations_client_created", "client_id", "created_at"),
    )
