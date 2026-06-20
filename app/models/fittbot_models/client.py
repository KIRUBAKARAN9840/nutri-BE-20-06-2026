from sqlalchemy import (
    Column, Integer, String, Float, Enum, Text, DateTime, ForeignKey, Date,
    Time, Boolean, JSON, Index,
)
from sqlalchemy.ext.mutable import MutableList
from app.models.database import Base
from datetime import datetime
import uuid


class Client(Base):
    __tablename__ = "clients"

    client_id = Column(Integer, primary_key=True, index=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE", onupdate="CASCADE"), nullable=True)
    name = Column(String(100), nullable=True)
    profile=Column(String(255))
    location = Column(String(255),nullable=True)
    email = Column(String(100),nullable=False)
    contact = Column(String(15), nullable=False)
    password = Column(String(255), nullable=False)
    lifestyle = Column(Text)
    medical_issues = Column(Text)
    batch_id = Column(Integer, nullable=True)
    training_id = Column(Integer, nullable=True)
    age = Column(Integer, nullable=True)
    goals = Column(Text)
    gender = Column(String(20), nullable=True,default="Other")
    height = Column(Float, nullable=True)
    weight = Column(Float, nullable=True)
    bmi = Column(Float, nullable=True)
    access=Column(Boolean)
    joined_date = Column(Date, default=lambda: datetime.now().date())
    status = Column(Enum("active", "inactive"), default="active")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    dob=Column(Date, nullable=True)
    expiry=Column(Enum("joining_date", "start_of_the_month"))
    refresh_token=Column(String(255))
    verification=Column(JSON)
    uuid_client = Column(String(36), unique=True, default=lambda: str(uuid.uuid4()), nullable=False)
    incomplete = Column(Boolean, nullable=False, default=False)
    expo_token = Column(MutableList.as_mutable(JSON))
    device_token = Column(MutableList.as_mutable(JSON))
    data_sharing=Column(Boolean)
    pincode = Column(String(10))
    modal_shown = Column(Boolean, default=False)
    platform = Column(String(15))


class ClientGym(Base):
    __tablename__ = "gym_client_id"
    id=Column(Integer,primary_key=True,autoincrement=True)
    client_id=Column(Integer, primary_key=True, index=True)
    gym_id=Column(Integer)
    gym_client_id= Column(String(255))
    admission_number= Column(String(100))


class OldGymData(Base):
    __tablename__ = "gym_old_data"

    id  = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey('clients.client_id', ondelete='CASCADE', onupdate='CASCADE'), nullable=True)
    gym_client_id = Column(String(25))
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE", onupdate="CASCADE"), nullable=True)
    name = Column(String(100), nullable=False)
    profile=Column(String(255))
    location = Column(String(255),nullable=True)
    email = Column(String(100), unique=False, nullable=False)
    contact = Column(String(15), nullable=False)
    lifestyle = Column(Text)
    medical_issues = Column(Text)
    batch_id = Column(Integer, nullable=True)
    training_id = Column(Integer, nullable=True)
    age = Column(Integer, nullable=True)
    goals = Column(Text)
    gender = Column(String(20), nullable=True,default="Other")
    height = Column(Float, nullable=True)
    weight = Column(Float, nullable=True)
    bmi = Column(Float, nullable=True)
    joined_date = Column(Date, default=lambda: datetime.now().date())
    status = Column(Enum("active", "inactive"), default="active")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    dob=Column(Date, nullable=True)
    expires_at=Column(Date, nullable=True)
    starts_at=Column(Date, nullable=True)
    admission_number= Column(String(100))


class ClientTarget(Base):
    __tablename__ = "client_targets"

    target_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    client_id = Column(Integer, nullable=False)
    calories = Column(Integer, nullable=True)
    protein = Column(Integer, nullable=True)
    carbs = Column(Integer, nullable=True)
    fat = Column(Integer, nullable=True)
    sugar = Column(Integer, nullable=True)
    fiber = Column(Integer, nullable=True)
    steps = Column(Integer, nullable=True)
    calories_to_burn = Column(Integer, nullable=True)
    water_intake = Column(Float, nullable=True)
    sleep_hours = Column(Float, nullable=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    weight = Column(Integer, nullable=True)
    start_weight = Column(Float, nullable=True)
    calcium=Column(Float, nullable=True)
    magnesium =Column(Float, nullable=True)
    potassium =Column(Float, nullable=True)
    Iodine=Column(Float, nullable=True)
    Iron=Column(Float, nullable=True)


class ClientActual(Base):
    __tablename__ = "client_actual"

    record_id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, nullable=False)
    date = Column(Date, nullable=False)
    weight = Column(Float)
    calories = Column(Integer)
    protein = Column(Integer)
    carbs = Column(Integer)
    fats = Column(Integer)
    sugar = Column(Integer)
    fiber = Column(Integer)
    steps = Column(Integer)
    burnt_calories = Column(Integer)
    water_intake = Column(Float)
    sleep_hours = Column(Float)
    target_calories = Column(Integer, nullable=True)
    target_protein = Column(Integer, nullable=True)
    target_fat = Column(Integer, nullable=True)
    target_carbs = Column(Integer, nullable=True)
    target_sleep_hrs = Column(Float, nullable=True)
    target_water_intake = Column(Float, nullable=True)
    target_steps = Column(Integer, nullable=True)
    calcium=Column(Float, nullable=True)
    magnesium =Column(Float, nullable=True)
    potassium =Column(Float, nullable=True)
    Iodine=Column(Float, nullable=True)
    Iron=Column(Float, nullable=True)
    target_sugar = Column(Integer, nullable=True)
    target_fiber = Column(Integer, nullable=True)
    target_calcium=Column(Float, nullable=True)
    target_magnesium =Column(Float, nullable=True)
    target_potassium =Column(Float, nullable=True)
    target_Iodine=Column(Float, nullable=True)
    target_Iron=Column(Float, nullable=True)
    last_water_time = Column(DateTime, nullable=True)


class ClientActualAggregatedWeekly(Base):
    __tablename__ = "client_actual_aggregated_weekly"

    record_id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, primary_key=True, nullable=False)
    week_start = Column(Date, nullable=False)
    avg_weight = Column(Float)
    avg_calories = Column(Float)
    avg_protein = Column(Float)
    avg_carbs = Column(Float)
    avg_fats = Column(Float)
    total_steps = Column(Integer)
    total_burnt_calories = Column(Integer)
    avg_water_intake = Column(Float)
    avg_sleep_hours = Column(Float)
    avg_sugar = Column(Float)
    avg_fiber = Column(Float)
    avg_calcium=Column(Float, nullable=True)
    avg_magnesium =Column(Float, nullable=True)
    avg_potassium =Column(Float, nullable=True)
    avg_Iodine=Column(Float, nullable=True)
    avg_Iron=Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ClientActualAggregated(Base):
    __tablename__ = "client_actual_aggregated"

    record_id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, primary_key=True, nullable=False)
    year = Column(Integer, nullable=False)
    avg_weight = Column(Float)
    avg_calories = Column(Float)
    avg_protein = Column(Float)
    avg_carbs = Column(Float)
    avg_fats = Column(Float)
    workout_time= Column(Integer)
    rest_time= Column(Integer)
    gym_time= Column(Integer)
    no_of_days_calories_met = Column(Integer)
    calories_surplus_days = Column(Integer)
    calories_deficit_days = Column(Integer)
    longest_streak = Column(Integer)
    current_streak = Column(Integer)
    average_protein_target = Column(Float)
    average_carbs_target = Column(Float)
    average_fat_target = Column(Float)
    avg_sugar = Column(Float)
    avg_fiber = Column(Float)
    avg_calcium=Column(Float, nullable=True)
    avg_magnesium =Column(Float, nullable=True)
    avg_potassium =Column(Float, nullable=True)
    avg_Iodine=Column(Float, nullable=True)
    avg_Iron=Column(Float, nullable=True)
    average_sugar_target = Column(Float)
    average_fiber_target = Column(Float)
    average_calcium_target=Column(Float, nullable=True)
    average_magnesium_target =Column(Float, nullable=True)
    average_potassium_target =Column(Float, nullable=True)
    average_Iodine_target=Column(Float, nullable=True)
    average_Iron_target=Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ClientGeneralAnalysis(Base):
    __tablename__ = "client_general_analysis"

    record_id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, nullable=False)
    date = Column(Date, nullable=False)
    weight = Column(Float, nullable=True)
    sleep_hrs = Column(Float, nullable=True)
    attendance = Column(Integer, nullable=True)
    water_taken = Column(Float, nullable=True)
    steps_count = Column(Integer, nullable=True)
    burnt_calories = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ClientScheduler(Base):
    __tablename__ = "client_scheduler"

    id = Column(Integer, primary_key=True, autoincrement=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False)
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False)
    assigned_trainer = Column(Integer, nullable=True)
    assigned_dietplan = Column(Integer, nullable=True)
    assigned_workoutplan = Column(Integer, nullable=True)


class ClientFittbotAccess(Base):

    __tablename__ = "client_fittbot_access"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    client_id     = Column(Integer,
                           ForeignKey("clients.client_id", ondelete="CASCADE"),
                           nullable=False)
    paid_date     = Column(DateTime, nullable=False)
    plan          = Column(String(100), nullable=False)
    access_status = Column(
        Enum("active", "inactive", name="access_status"),
        nullable=False,
        default="active"
    )
    fittbot_plan= Column(Integer, nullable=False)
    free_trial=Column(String(20))
    start_date= Column(Date)
    days_left=Column(Integer)


class ClientBirthday(Base):
    __tablename__ = "client_birthdays"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    client_id   = Column(Integer, ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False, unique=True)
    client_name = Column(String(100), nullable=False)
    expo_token  = Column(JSON, nullable=False)


class ClientWeightData(Base):
    __tablename__ = "client_weight_data"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(
        Integer,
        ForeignKey("clients.client_id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False
    )
    weight    = Column(Float, nullable=False)
    status    = Column(Boolean, default=False)
    date=Column(Date)


class WeightJourney(Base):
    __tablename__ ='client_weight_journey'

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, nullable=False)
    start_date=Column(Date)
    end_date = Column(Date)
    start_weight=Column(Float)
    actual_weight=Column(Float)
    target_weight=Column(Float)


class ClientWeightSelection(Base):
    __tablename__ = "client_weight_selection"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    client_id = Column(String(45), nullable=False)
    current_image_id = Column(String(45), nullable=False)
    target_image_id = Column(String(45), nullable=False)
    combination_id = Column(String(45), nullable=True)


class WeightManagementPlan(Base):
    __tablename__ = "weight_management_plan"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    category = Column(String(50), nullable=False)  # 'weight_loss' or 'weight_gain'
    gender = Column(String(20), nullable=False)  # 'male' or 'female'
    weight_min = Column(Integer, nullable=False)
    weight_max = Column(Integer, nullable=False)
    activity_level = Column(String(50), nullable=False)  # sedentary, lightly_active, etc.
    duration_months = Column(Integer, nullable=False)


class ClientNextXp(Base):
    __tablename__ = "client_next_xp"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer,
                       ForeignKey("clients.client_id", ondelete="CASCADE"),
                       nullable=False,
                       index=True,
                       unique=True)
    next_xp   = Column(Integer, nullable=False, default=0)
    gift = Column(String(155))


class VoicePreference(Base):
    __tablename__ = "voice_preference"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False, unique=True)
    preference = Column(String(1), nullable=False, default='1')  # '1' = voice ON, '0' = voice OFF
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class Preference(Base):
    __tablename__ ="preference"

    preference_id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False)
    notifications = Column(Boolean, nullable=False, default=False)
    remainders = Column(Boolean, nullable=False, default=False)
    data_sharing =Column(Boolean, nullable=False, default=False)
    newsletters = Column(Boolean, nullable=False, default=False)
    promos_and_offers = Column(Boolean, nullable=False, default=False)


class SmartWatch(Base):
    __tablename__='smart_watch'
    id  = Column(Integer, primary_key=True, index=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey('clients.client_id', ondelete='CASCADE', onupdate='CASCADE'),unique=True, nullable=True)
    interested = Column(Boolean)


class ClientModalTracker(Base):
    __tablename__ = "client_modal_tracker"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    last_modal_index = Column(Integer, default=0, nullable=False)  # 0=no_cost_emi, 1=bnpl, 2=session, 3=dailypass
    last_shown_date = Column(Date, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class CalorieEvent(Base):
    __tablename__ = "calorie_event"
    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, nullable=False)
    gym_id = Column(Integer, nullable=True)\

    event_date = Column(Date, nullable=True)
    calories_added = Column(Integer, nullable=True)
    workout_added=Column(Integer, nullable=True)
    water_added=Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class FittbotPlans(Base):
    __tablename__ = "fittbot_plans_legacy"

    id                 = Column(Integer, primary_key=True, autoincrement=True)
    plan_name          = Column(String(255), nullable=False)
    duration           = Column(Integer, nullable=False)
    image_url          = Column(String(512), nullable=True)
    price              = Column(Integer, nullable=False)


class ClientCharacter(Base):
    __tablename__ = "client_characters"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    client_id = Column(Integer, nullable=False, index=True)
    character_id = Column(Integer, nullable=False, index=True)
