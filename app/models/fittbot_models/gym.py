from sqlalchemy import (
    Column, Integer, BigInteger, UniqueConstraint, String, Float, Enum, Text,
    DateTime, ForeignKey, Date, Time, Boolean, JSON, Numeric, Index, func,
)
from sqlalchemy.orm import relationship
from sqlalchemy.ext.mutable import MutableList, MutableDict
from app.models.database import Base
from datetime import datetime


class Gym(Base):
    __tablename__ = "gyms"

    gym_id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, nullable=True)
    name = Column(String(200), nullable=False)
    location = Column(String(255), nullable=True)
    type = Column(String(20), default=" ")
    max_clients = Column(Integer, nullable=True)
    logo = Column(String(255))
    cover_pic=Column(String(255))
    subscription_end_date = Column(Date)
    subscription_start_date = Column(Date)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    referal_id=Column(String(15))
    fittbot_verified=Column(Boolean, default=False)
    dailypass=Column(Boolean, default=False)
    gym_timings=Column(JSON)

    # New fields for registration
    contact_number = Column(String(15), nullable=True)
    services = Column(JSON, nullable=True)  # Array of services offered
    operating_hours = Column(JSON, nullable=True)  # Array of operating hour objects
    door_no = Column(String(50), nullable=True)
    building = Column(String(255), nullable=True)
    street = Column(String(255), nullable=True)
    area = Column(String(255), nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    pincode = Column(String(10), nullable=True)
    fitness_type=Column(JSON, nullable=False)  # Array of fitness types (e.g., gym, yoga, crossfit)

    trainer_profiles = relationship("TrainerProfile", back_populates="gym")

    __table_args__ = (
        Index("idx_gym_verified_location", "fittbot_verified", "city", "area", "pincode"),
    )


class GymOwner(Base):
    __tablename__ = "gym_owners"

    owner_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=True)
    email = Column(String(100), nullable=True)
    refresh_token=Column(String(255))
    contact_number = Column(String(15), unique=True, nullable=False)
    password = Column(String(255), nullable=False)
    profile = Column(String(255))
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    dob=Column(Date)
    age=Column(String(15))
    verification=Column(JSON,nullable=False)
    expo_token = Column(MutableList.as_mutable(JSON))
    incomplete=Column(Boolean, nullable=False, default=False)


class GymLocation(Base):
    __tablename__ = "gym_location"
    id = Column(Integer, primary_key=True, index=True)
    gym_id = Column(Integer, unique=True, index=True, nullable=False)
    latitude = Column(Numeric(10, 8), nullable=False)
    longitude = Column(Numeric(11, 8), nullable=False)
    gym_pic = Column(String(255), nullable=True)

    __table_args__ = (
        Index("idx_gym_location_coordinates", "gym_id", "latitude", "longitude"),
    )


class GymDetails(Base):
    __tablename__ = "gym_details"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False, unique=True, index=True)
    total_machineries = Column(Integer, nullable=True)
    floor_space = Column(Integer, nullable=True)
    total_trainers = Column(Integer, nullable=True)
    yearly_membership_cost = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class GymBatches(Base):
    __tablename__ = "gym_batches"

    batch_id = Column(Integer, primary_key=True, autoincrement=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id"), nullable=False)
    batch_name = Column(String(50), nullable=False)
    timing=Column(String(50), nullable=False)
    description = Column(String(255), nullable=True)


class GymPlans(Base):
    __tablename__ = "gym_plans"

    id = Column(Integer, primary_key=True, autoincrement=True)

    gym_id = Column(Integer, ForeignKey("gyms.gym_id"), nullable=False)
    plans = Column(String(50),  nullable=False)
    amount = Column(Integer, nullable=False)
    duration = Column(Integer, nullable=False)
    description = Column(String(255), nullable=True)
    services=Column(JSON, nullable=True)
    personal_training=Column(Boolean, default=False)
    bonus=Column(Integer,nullable=True)
    pause=Column(Integer,nullable=True)
    bonus_type=Column(String(45),nullable=True)
    pause_type=Column(String(45),nullable=True)
    original_amount=Column(Integer,nullable=True)
    plan_for= Column(String(45))
    buddy_count=Column(Integer,nullable=True)
    sessions_count=Column(Integer,nullable=True)


class GymMembershipOffer(Base):

    __tablename__ = "gym_membership_offers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE"), nullable=False, index=True)
    plan_id = Column(Integer, ForeignKey("gym_plans.id", ondelete="CASCADE"), nullable=True, index=True)
    offer_price = Column(Integer, nullable=False)
    valid_from = Column(DateTime, nullable=False)
    valid_until = Column(DateTime, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    __table_args__ = (
        Index("idx_gym_membership_offer_lookup", "gym_id", "is_active", "valid_from", "valid_until"),
    )


class GymFees(Base):
    __tablename__ = "gym_fees"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    client_id  = Column(Integer, nullable=False, index=True)
    start_date = Column(Date, nullable=False)
    end_date   = Column(Date, nullable=False)


class LiveCount(Base):
    __tablename__ = "live_count"
    id = Column(Integer, primary_key=True,nullable=True,autoincrement=True )
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE", onupdate="CASCADE"))
    count = Column(Integer, nullable=False, default=0)


class NewOffer(Base):
    """
    Gym-level offer flags to control special dailypass/session pricing.
    """
    __tablename__ = "new_offer"

    id = Column(Integer, primary_key=True, autoincrement=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False, unique=True, index=True)
    dailypass = Column(Boolean, default=False, nullable=False)
    session = Column(Boolean, default=False, nullable=False)


class NoCostEmi(Base):
    __tablename__ = "no_cost_emi"

    id = Column(Integer, primary_key=True, autoincrement=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False, index=True)
    no_cost_emi = Column(Boolean, default=False, nullable=False)
    bnpl = Column(Boolean, default=False, nullable=False)


class AccountDetails(Base):
    __tablename__ = "account_details"

    account_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE") ,nullable=False)
    account_number = Column(String(255))
    bank_name = Column(String(255))
    account_ifsccode = Column(String(45))
    account_branch = Column(String(255))
    account_holdername = Column(String(255))
    upi_id = Column(String(255), nullable=True)
    gst_number = Column(String(55),default=None)
    pan_number=Column(String(45),default=None)

    # Additional fields for registration
    gst_type = Column(String(20), nullable=True)  # inclusive, exclusive, nogst
    gst_percentage = Column(String(5), default="18", nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class AccountDetailsEditRequest(Base):
    __tablename__ = "account_details_edit_requests"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE"), nullable=False, index=True)
    owner_id = Column(Integer, ForeignKey("gym_owners.owner_id", ondelete="CASCADE"), nullable=False, index=True)
    old_json = Column(JSON, nullable=False)  # Original payment details before edit
    new_json = Column(JSON, nullable=False)  # Requested new payment details
    query_solved = Column(Boolean, default=False, nullable=False)
    requested_time = Column(DateTime, default=datetime.now, nullable=False)
    resolved_time = Column(DateTime, nullable=True)
    admin_remarks = Column(String(500), nullable=True)  # Optional remarks from admin


class GymPhoto(Base):
    __tablename__ = "gym_photos"

    photo_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE"), nullable=False, index=True)
    area_type = Column(String(50), nullable=False, index=True)  # entrance, cardio, weight, locker, reception, other
    image_url = Column(String(500), nullable=False)
    file_name = Column(String(255), nullable=True)
    file_size = Column(Integer, nullable=True)  # in bytes
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    gym = relationship("Gym", backref="gym_photos")


class GymStudiosPic(Base):
    __tablename__ = "gym_studios_pic"

    photo_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE"), nullable=False, index=True)
    type = Column(String(55), nullable=False, index=True)  # cover_pic, logo, etc.
    image_url = Column(String(500), nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        Index("idx_gym_studios_pic_gym_type", "gym_id", "type"),
    )


class GymImportData(Base):
    __tablename__ = "gym_import_data"

    import_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete = "CASCADE"), nullable=False)
    client_name = Column(String(45), nullable=False)
    client_contact = Column(String(45), nullable=False)
    client_email = Column(String(255), nullable=True, default=None)
    client_location = Column(String(255), nullable=True, default=None)
    status = Column(String(45))
    gender = Column(String(45), nullable=False)
    sms_status = Column(Boolean, nullable=False, default=False)
    admission_number = Column(String(100), default= None)
    expires_at=Column(Date, nullable=True)
    joined_at=Column(Date, nullable=True)
    import_type=Column(String(45))


class GymManualData(Base):
    __tablename__ = "gym_manual_data"

    id=Column(Integer, primary_key=True, autoincrement=True, index=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE"), nullable=False, index=True)
    total_clients= Column(Integer)
    active_clients=Column(Integer)
    inactive_clients=Column(Integer)
    total_enquiries=Column(Integer)
    total_followups=Column(Integer)


class GymEnquiry(Base):
    __tablename__ = "gym_enquiry"

    enquiry_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete = "CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    contact = Column(String(20), nullable=False)
    email = Column(String(255), nullable=False)
    convenientTime = Column(String(255))
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    status = Column(String(255), default="pending")
    statusReason = Column(String(255))
    message = Column(Text, nullable=True)


class GymAnnouncement(Base):
    __tablename__ = "gym_announcements"

    id          = Column(Integer, primary_key=True, index=True, autoincrement=True)
    gym_id      = Column(Integer, nullable=False)
    title       = Column(Text, nullable=False)
    description = Column(Text, nullable=False)
    datetime    = Column(DateTime, nullable=False)
    priority    = Column(String(45),nullable=True)


class GymOffer(Base):
    __tablename__ = "gym_offers"

    id             = Column(Integer, primary_key=True, index=True, autoincrement=True)
    gym_id         = Column(Integer, nullable=False)
    title          = Column(Text, nullable=False)
    subdescription = Column(Text, nullable=True)
    description    = Column(Text, nullable=False)
    validity       = Column(DateTime, nullable=False)
    discount       = Column(Integer, nullable=False)
    category       = Column(String(255), nullable=False)
    tag            = Column(String(255), nullable=True)
    code           = Column(String(100), nullable=False)
    image_url=Column(String(255), nullable=True)


class GymStudiosRequest(Base):
    __tablename__ = "gym_studios_request"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False, index=True)
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)
    area = Column(String(255), nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    pincode = Column(String(10), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class Brochures(Base):
    __tablename__ = "gym_brouchre"

    brouchre_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    gym_id   = Column(Integer,ForeignKey("gyms.gym_id", ondelete="CASCADE"), nullable=False, index=True)
    pic_url  = Column(String(255), nullable=False)


class GymOnboardingPics(Base):
    __tablename__ = "gym_onboarding_pics"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE"), nullable=False, index=True)
    machinery_1 = Column(String(255), nullable=True)
    machinery_2 = Column(String(255), nullable=True)
    treadmill_area = Column(String(255), nullable=True)
    cardio_area = Column(String(255), nullable=True)
    dumbell_area = Column(String(255), nullable=True)
    reception_area = Column(String(255), nullable=True)
    uploaded = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class BiometricModal(Base):
    __tablename__ = "biometric_modal"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE"), nullable=False, index=True)
    interest = Column(Boolean, default=False, nullable=False)
    pic_1 = Column(String(255), nullable=True)
    pic_2 = Column(String(255), nullable=True)
    pic_3 = Column(String(255), nullable=True)
    pic_4 = Column(String(255), nullable=True)
    pic_5 = Column(String(255), nullable=True)
    pic_6 = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class FittbotAssociates(Base):
    __tablename__ = "fittbot_associates"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    mobile_number = Column(String(15), nullable=False, unique=True, index=True)
    gym_ids = Column(JSON, nullable=True)


class Expenditure(Base):
    __tablename__ = "expenditures"

    expenditure_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    gym_id = Column(Integer, nullable=False)
    expenditure_type = Column(String(100), nullable=False)
    amount = Column(Float, nullable=False)
    date = Column(Date, nullable=False)


class GymAnalysis(Base):
    __tablename__ = "gym_analysis"

    id = Column(Integer, primary_key=True, autoincrement=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False)
    analysis_type = Column(String(100), nullable=False)
    analysis_name = Column(String(100), nullable=False)
    value = Column(Float, nullable=False)
    analysis = Column(MutableDict.as_mutable(JSON), default=dict, nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class GymMonthlyData(Base):
    __tablename__ = "gym_monthly_data"

    id = Column(Integer, primary_key=True, autoincrement=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False)
    month_year = Column(Date, nullable=False)
    income = Column(Integer, nullable=False, default=0)
    expenditure = Column(Integer, nullable=False, default=0)
    new_entrants = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class FittbotGymMembership(Base):
    __tablename__ = "fittbot_gym_membership"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    gym_id = Column(String(100), nullable=False, index=True)
    client_id = Column(String(100), nullable=False, index=True)
    plan_id = Column(Integer, nullable=True, index=True)
    type = Column(String(50), nullable=False, index=True)
    entitlement_id = Column(String(100), nullable=True, index=True)
    amount=Column(Float, nullable=False)
    purchased_at = Column(DateTime(timezone=True), nullable=False, index=True)
    status = Column(String(50), nullable=False, default="upcoming", index=True)
    joined_at = Column(Date, nullable=True, index=True)
    expires_at = Column(Date, nullable=True, index=True)
    pause= Column(String(50), default=False)
    pause_at=Column(Date)
    resume_at=Column(Date)
    old_client=Column(Boolean)
