from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, Date, Boolean,
    JSON, Index,
)
from app.models.database import Base
from datetime import datetime
import uuid


class GymVerificationDocument(Base):
    __tablename__ = "gym_verification_documents"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE"), nullable=False, index=True)
    aadhaar_url = Column(String(500), nullable=True)
    aadhaar_back= Column(String(500), nullable=True)
    pan_url = Column(String(500), nullable=True)
    bankbook_url = Column(String(500), nullable=True)
    updated_by = Column(String(255), nullable=True)
    agreement=Column(Boolean, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class GymPrefilledAgreement(Base):
    """Stores prefilled agreement PDF links for gyms"""
    __tablename__ = "gym_prefilled_agreement"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE"), nullable=False, index=True, unique=True)
    s3_link = Column(String(500), nullable=False)
    is_clicked = Column(Boolean, default=False, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class GymAgreementSteps(Base):
    """Tracks multi-step agreement verification: Terms -> Selfie -> Signature -> OTP"""
    __tablename__ = "gym_agreement_steps"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE"), nullable=False, index=True)
    owner_id = Column(Integer, ForeignKey("gym_owners.owner_id", ondelete="SET NULL"), nullable=True, index=True)

    # Step 1: Terms acceptance
    terms_accepted = Column(Boolean, default=False)
    terms_accepted_at = Column(DateTime, nullable=True)

    # Step 2: Selfie with timestamp
    selfie_url = Column(String(500), nullable=True)
    selfie_captured_at = Column(DateTime, nullable=True)

    # Step 3: Digital signature
    signature_url = Column(String(500), nullable=True)
    signature_captured_at = Column(DateTime, nullable=True)

    # Step 4: OTP verification
    otp_verified = Column(Boolean, default=False)
    otp_verified_at = Column(DateTime, nullable=True)
    otp_mobile = Column(String(15), nullable=True)

    # Audit fields
    accepted_by_name = Column(String(200), nullable=True)
    accepted_ip = Column(String(50), nullable=True)
    accepted_user_agent = Column(String(500), nullable=True)
    agreement_version = Column(String(50), default="1.0")

    # Completion status
    all_steps_completed = Column(Boolean, default=False)
    completed_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class GymOnboardingEsign(Base):
    """
    Tracks gym onboarding e-sign documents via Leegality.
    Stores document status, URLs, and signed document S3 paths.
    """
    __tablename__ = "gym_onboarding_esign"

    id = Column(Integer, primary_key=True, autoincrement=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE"), nullable=False, index=True)
    owner_id = Column(Integer, ForeignKey("gym_owners.owner_id", ondelete="CASCADE"), nullable=False, index=True)

    # Leegality document identifiers
    document_id = Column(String(100), nullable=True, index=True)
    irn = Column(String(100), nullable=True, unique=True, index=True)  # Internal Reference Number

    # Document details
    gym_name = Column(String(200), nullable=False)
    location = Column(String(255), nullable=True)
    gst_no = Column(String(20), nullable=True)
    pan = Column(String(15), nullable=True)
    address = Column(Text, nullable=True)
    authorised_name = Column(String(200), nullable=False)
    mobile = Column(String(15), nullable=False)
    email = Column(String(100), nullable=False)

    # Status tracking
    status = Column(String(50), default="pending", nullable=False, index=True)  # pending, sent, signed, failed, expired
    signing_url = Column(Text, nullable=True)

    # Signed document storage
    signed_pdf_url = Column(String(500), nullable=True)  # S3 URL after document is signed
    audit_trail_url = Column(String(500), nullable=True)  # S3 URL for audit trail PDF
    signed_at = Column(DateTime, nullable=True)

    # Webhook tracking
    webhook_received_at = Column(DateTime, nullable=True)
    webhook_event_type = Column(String(50), nullable=True)
    webhook_payload = Column(JSON, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # Indexes for common queries
    __table_args__ = (
        Index("ix_esign_gym_status", "gym_id", "status"),
        Index("ix_esign_created", "created_at"),
    )


class GymAgreement(Base):
    """
    Tracks prefilled gym agreement PDFs generated asynchronously via Celery.
    Stores generation status, S3 paths, and acceptance consent.
    """
    __tablename__ = "gym_agreements"

    agreement_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE"), nullable=False, index=True)
    owner_id = Column(Integer, ForeignKey("gym_owners.owner_id", ondelete="CASCADE"), nullable=True, index=True)

    # Template version for tracking which coords/template was used
    template_version = Column(String(20), default="v1", nullable=False)

    # Status tracking: PENDING -> GENERATING -> READY -> ACCEPTED (or FAILED)
    status = Column(String(20), default="PENDING", nullable=False, index=True)

    # Prefill data stored as JSON for record keeping
    prefill_json = Column(JSON, nullable=True)

    # S3 storage
    s3_key_final = Column(Text, nullable=True)  # Final PDF S3 key
    pdf_sha256 = Column(String(64), nullable=True)  # SHA256 hash for integrity

    # Error tracking
    error_message = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    ready_at = Column(DateTime, nullable=True)  # When PDF generation completed

    # Acceptance/consent fields
    accepted_at = Column(DateTime, nullable=True)
    accepted_by_name = Column(String(200), nullable=True)  # Typed name for consent
    accepted_ip = Column(String(64), nullable=True)  # IP address for audit
    accepted_user_agent = Column(Text, nullable=True)  # User agent for audit
    selfie_s3_key = Column(Text, nullable=True)  # Optional selfie for verification

    # Indexes for common queries
    __table_args__ = (
        Index("ix_gym_agreement_gym_status", "gym_id", "status"),
        Index("ix_gym_agreement_created", "created_at"),
    )
