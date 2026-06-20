from sqlalchemy import (
    Column, Integer, BigInteger, String, Float, Enum, Text, DateTime,
    ForeignKey, Date, Boolean, JSON, Numeric, Index,
)
from app.models.database import Base
from datetime import datetime


class RazorpayOrder(Base):
    __tablename__ = "razorpay_orders"

    id         = Column(BigInteger, primary_key=True)
    client_id = Column(Integer)
    plan = Column(Integer)
    order_id   = Column(String(40), unique=True, index=True)
    amount     = Column(Integer)
    currency   = Column(String(4), default="INR")
    status     = Column(Enum("created", "authorized", "paid", "failed", "refunded", name="order_status"))
    payment_id = Column(String(40), unique=True, nullable=True)
    receipt    = Column(String(64))
    payment_method   = Column(String(32))
    acquirer_ref     = Column(String(64))
    failure_code     = Column(String(32))
    failure_desc     = Column(String(255))
    signature_verified = Column(Boolean, default=False)
    captured_at      = Column(DateTime)
    verified_at      = Column(DateTime)


class RazorpayPayment(Base):
    __tablename__ = "razorpay_payments"

    id = Column(BigInteger, primary_key=True)
    razorpay_event_key = Column(String(120), unique=True, index=True)
    event_type         = Column(String(64))
    payload            = Column(JSON)
    signature          = Column(String(128))


class FeeHistory(Base):
    __tablename__ = "fee_history"

    record_id = Column(Integer, primary_key=True, autoincrement=True)
    gym_id = Column(Integer, nullable=False)
    client_id = Column(Integer, nullable=False)
    fees_paid = Column(Float, nullable=False)
    payment_date = Column(Date, nullable=False)
    type= Column(String(45), nullable=False)


class FeesReceipt(Base):
    __tablename__ = "fees_receipt"

    receipt_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=True)  # For regular clients
    manual_client_id = Column(Integer, ForeignKey("manual_clients.id", ondelete="CASCADE"), nullable=True)  # For manual CRM clients
    gym_id = Column(Integer, ForeignKey("gyms.gym_id"), nullable=False)
    client_name = Column(String(255))
    gym_name = Column(String(255))
    gym_logo = Column(String(255))
    gym_contact = Column(String(255))
    gym_location = Column(String(255))
    plan_id = Column(Integer, ForeignKey("gym_plans.id"), nullable=False)
    plan_description = Column(String(255))
    fees = Column(Float)
    fees_type = Column(String(50))
    discount = Column(Float)
    discounted_fees = Column(Float)
    due_date = Column(DateTime)
    invoice_number = Column(String(255))
    client_contact = Column(String(45))
    bank_details = Column(String(255))
    ifsc_code = Column(String(255))
    bank_name = Column(String(255))
    upi_id = Column(String(255))
    account_holder_name = Column(String(255))
    invoice_date = Column(String(255))
    payment_method = Column(String(255))
    gst_number = Column(String(55))
    client_email = Column(String(255))
    mail_status = Column(Boolean)
    created_at = Column(DateTime)
    update_at = Column(DateTime)
    payment_date = Column(DateTime)
    payment_reference_number = Column(String(255), nullable=True)
    gst_percentage = Column(Float, nullable=True, default=18)
    gst_type = Column(String(255), nullable=True)
    branch = Column(String(100),nullable=True)
    total_amount = Column(Float, nullable=True)


class EnquiryEstimates(Base):
    __tablename__ = "enquiry_estimates"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    enquiry_id = Column(Integer, ForeignKey("gym_enquiry.enquiry_id", ondelete="CASCADE"), nullable=False)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id"), nullable=False)
    client_name = Column(String(255))
    gym_name = Column(String(255))
    gym_logo = Column(String(255))
    gym_contact = Column(String(255))
    gym_location = Column(String(255))
    plan_id = Column(Integer, ForeignKey("gym_plans.id"), nullable=False)
    plan_description = Column(String(255))
    fees = Column(Float)
    admission_fees = Column(Float, nullable=True, default=0)
    fees_type = Column(String(50))
    discount = Column(Float)
    discounted_fees = Column(Float)
    estimate_number = Column(String(255))
    client_contact = Column(String(45))
    bank_details = Column(String(255))
    ifsc_code = Column(String(255))
    bank_name = Column(String(255))
    upi_id = Column(String(255))
    account_holder_name = Column(String(255))
    estimate_date = Column(String(255))
    gst_number = Column(String(55))
    client_email = Column(String(255))
    mail_status = Column(Boolean)
    created_at = Column(DateTime)
    update_at = Column(DateTime)
    gst_percentage = Column(Float, nullable=True, default=18)
    gst_type = Column(String(255), nullable=True)
    branch = Column(String(100),nullable=True)
    total_amount = Column(Float, nullable=True)


class EstimateDiscount(Base):
    __tablename__ = "estimate_discount"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    membership_id = Column(Integer, nullable=False, index=True)
    discount_amount = Column(Numeric(10, 2), nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class AboutToExpire(Base):
    __tablename__ = "about_to_expire"

    expiry_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="CASCADE"))
    gym_id = Column(Integer, ForeignKey("gyms.gym_id"), nullable=False)
    gym_client_id=Column(String(45))
    admission_number=Column(String(100))
    expires_in=Column(Integer)
    client_name = Column(String(255))
    gym_name = Column(String(255))
    gym_logo = Column(String(255), nullable=True)
    gym_contact = Column(String(255), nullable=True)
    gym_location = Column(String(255), nullable=True)
    plan_id = Column(Integer, ForeignKey("gym_plans.id", ondelete="CASCADE"))
    plan_description = Column(String(255), nullable=True)
    fees = Column(Float, nullable=True)
    discount = Column(Float, nullable=True)
    discounted_fees = Column(Float, nullable =True)
    due_date = Column(DateTime)
    invoice_number = Column(String(255), nullable=True)
    client_contact = Column(String(20), nullable=True)
    bank_details = Column(String(255), nullable=True)
    ifsc_code = Column(String(255), nullable=True)
    bank_name = Column(String(255))
    upi_id = Column(String(255))
    branch = Column(String(255))
    account_holder_name = Column(String(255), nullable=True)
    gst_number=Column(String(55), default=None)
    paid = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    mail_status = Column(Boolean, default=False)
    expired = Column(Boolean, default=False)
    email = Column(String(55),nullable=False)


class GymBusinessPayment(Base):
    __tablename__ = "gym_business_payment"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    client_id = Column(String(100), nullable=False, index=True)
    gym_id = Column(String(100), nullable=False, index=True)
    date = Column(Date, nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    status = Column(String(50), nullable=False)
    mode = Column(String(50), nullable=False)
    entitlement_id = Column(String(100), nullable=True, index=True)
    payment_id = Column(String(100), nullable=True, index=True)
    order_id = Column(String(100), nullable=True, index=True)
    membership_id=Column(Integer, nullable=True)
    created_at= Column(DateTime, nullable=True)
    updated_at= Column(DateTime, nullable=True)

