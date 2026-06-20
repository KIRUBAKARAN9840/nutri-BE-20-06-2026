from sqlalchemy import (
    Column, Integer, String, Float, Enum, Text, DateTime, ForeignKey, Date,
    Boolean, JSON, Index, func,
)
from app.models.database import Base
from datetime import datetime


class WorkoutTemplate(Base):
    __tablename__ = "workout_template"

    template_id = Column(Integer, primary_key=True, autoincrement=True)
    gym_id=Column(Integer, nullable=False)
    template_name=Column(String(60), nullable=False)
    client_id = Column(Integer, nullable=False)
    day = Column(String(20), nullable=False)
    workout_name = Column(String(100), nullable=False)
    sets = Column(Integer, nullable=False)
    reps = Column(Integer, nullable=False)
    weight_1 = Column(Integer)
    weight_2 = Column(Integer)
    weight_3 = Column(Integer)
    weight_4 = Column(Integer)
    muscle_group = Column(String(50))
    duration = Column(Integer)
    rest_time = Column(Integer)
    notes = Column(Text)


class ActualWorkout(Base):
    __tablename__ = "actual_workout"

    record_id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, nullable=False)
    date = Column(Date, nullable=False)
    workout_details = Column(JSON, nullable=True)
    workout_time = Column(Float, nullable=True)


class FittbotWorkout(Base):
    __tablename__ = "fittbot_workout"

    id = Column(Integer, primary_key=True, autoincrement=True)
    exercise_data = Column(JSON, nullable=False)


class ClientWorkoutTemplate(Base):
    __tablename__ = "client_workout_template"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, nullable=False)
    template_name = Column(String(255), nullable=False)
    exercise_data = Column(JSON, nullable=False)


class TemplateWorkout(Base):
    __tablename__ = "template_workout"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE"), nullable=False)
    name = Column(String(45), nullable=False)
    workoutPlan = Column(JSON, nullable=True)
    notes = Column(Text, nullable=True)


class DefaultWorkoutTemplates(Base):
    __tablename__ ='default_workout_templates'

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    gender = Column(String(20))
    goals = Column(String(20))
    expertise_level=Column(String(100))
    workout_json = Column(JSON)


class HomeWorkout(Base):
    __tablename__ = "home_workout"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    home_workout = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class EquipmentWorkout(Base):
    __tablename__ = "equipment_workout"

    id = Column(Integer, primary_key=True, autoincrement=True)
    equipment = Column(JSON, nullable=False)


class QRCode(Base):
    __tablename__ = "qr_code"
    id = Column(Integer, primary_key=True, index=True)
    exercises = Column(String(255), nullable=False)
    muscle_group = Column(String(255),nullable=False)
    isMuscleGroup=Column(Boolean, nullable=False)
    isCardio=Column(Boolean, nullable=False)
    isBodyWeight=Column(Boolean, nullable=False)
    gif_path_m=Column(String(255), nullable=True)
    gif_path_f=Column(String(255), nullable=True)
    img_path_m=Column(String(255), nullable=True)
    img_path_f=Column(String(255), nullable=True)


class FittbotMuscleGroup(Base):
    __tablename__ = "fittbot_muscle_group"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    gender       = Column(Enum("male", "female", "other", name="gender_enum"), nullable=False)
    muscle_group = Column(String(100), nullable=False)
    url          = Column(String(255), nullable=False)


class MuscleAggregatedInsights(Base):
    __tablename__ = "muscle_aggregated_insights"

    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False, unique=True)
    client_id = Column(Integer, nullable=False)
    muscle_group = Column(String(100), nullable=True)
    total_volume = Column(Float, nullable=True)
    avg_weight = Column(Float, nullable=True)
    avg_reps = Column(Float, nullable=True)
    max_weight = Column(Float, nullable=True)
    max_reps = Column(Integer, nullable=True)
    rest_days = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class AggregatedInsights(Base):
    __tablename__ = 'aggregated_insights'

    id = Column(Integer, primary_key=True, autoincrement=True,nullable=False, unique=True)
    client_id = Column(Integer, nullable=False)
    week_start= Column(Date, nullable=False)
    total_volume = Column(Float, nullable=False)
    avg_weight = Column(Float, nullable=False)
    avg_reps = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ClientWeeklyPerformance(Base):
    __tablename__ = "client_weekly_performance"

    id = Column(Integer, primary_key=True, autoincrement=True,nullable=False, unique=True)
    client_id = Column(Integer, nullable=False)
    week_start = Column(Date, nullable=False)
    muscle_group = Column(String(50), nullable=True)
    total_volume = Column(Float, nullable=True)
    avg_weight = Column(Float, nullable=True)
    avg_reps = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
