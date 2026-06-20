from sqlalchemy import (
    Column, Integer, String, Float, Text, DateTime, ForeignKey, Date,
    Boolean, JSON, Index, func,
)
from app.models.database import Base
from datetime import datetime


class Food(Base):
    __tablename__ = "fittbot_food"

    id = Column(Integer, primary_key=True, index=True)
    categories = Column(String(100), nullable=False)
    item = Column(String(100), nullable=False)
    quantity=Column(String(45), nullable=False)
    pic = Column(Text)
    calories = Column(Integer, nullable=False)
    protein = Column(Float, nullable=False)
    carbs = Column(Float, nullable=False)
    fat = Column(Float, nullable=False)
    fiber = Column(Float, nullable=False)
    sugar = Column(Float, nullable=False)
    added_sugar=Column(Float, nullable=True)
    calcium=Column(Float, nullable=True)
    magnesium =Column(Float, nullable=True)
    potassium =Column(Float, nullable=True)
    sodium=Column(Float, nullable=True)
    iron=Column(Float, nullable=True)
    is_added=Column(Boolean, default=False)
    is_natural=Column(Boolean, default=False)
    is_manual=Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class CustomFood(Base):
    __tablename__ = "custom_food"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(Integer, ForeignKey("gym_owners.owner_id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    quantity = Column(String(45), nullable=False)
    calories = Column(Integer, nullable=False)
    protein = Column(Float, nullable=False)
    carbs = Column(Float, nullable=False)
    fat = Column(Float, nullable=False)
    fiber = Column(Float, nullable=True)
    sugar = Column(Float, nullable=True)
    pic = Column(Text, nullable=True)
    calcium=Column(Float, nullable=True)
    magnesium =Column(Float, nullable=True)
    potassium =Column(Float, nullable=True)
    Iodine=Column(Float, nullable=True)
    Iron=Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class DietTemplate(Base):
    __tablename__ = "diet_template"

    template_id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, nullable=False)
    diet_variant = Column(String(45), nullable=False)
    time_slot = Column(String(20), nullable=False)
    meal_type = Column(String(50), nullable=False)
    diet_type = Column(String(50), nullable=False)
    calories = Column(Integer, nullable=False)
    protein = Column(Integer, nullable=False)
    fat = Column(Integer, nullable=False)
    carbs = Column(Integer, nullable=False)
    notes = Column(String(255), nullable=True)
    fiber = Column(Integer, nullable=True)
    sugar = Column(Integer, nullable=True)
    calcium=Column(Float, nullable=True)
    magnesium =Column(Float, nullable=True)
    potassium =Column(Float, nullable=True)
    Iodine=Column(Float, nullable=True)
    Iron=Column(Float, nullable=True)


class ActualDiet(Base):
    __tablename__ = "actual_diet"

    record_id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, nullable=False)
    date = Column(Date, nullable=False)
    diet_data = Column(JSON, nullable=True)


class FittbotDietTemplate(Base):
    __tablename__ ='fittbot_diet_template'

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    template_name = Column(String(45))
    template_json = Column(JSON)
    gender = Column(String(50))
    goals  = Column(String(45))
    cousine = Column(String(50))
    expertise_level = Column(String(50))
    tip = Column(String(255))


class ClientDietTemplate(Base):
    __tablename__ = "client_diet_template"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, nullable=False)
    template_name = Column(String(255), nullable=False)
    diet_data = Column(JSON,nullable=False)


class TemplateDiet(Base):
    __tablename__ = "template_diet"

    template_id = Column(Integer, primary_key=True, autoincrement=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE"), nullable=False)
    template_name = Column(String(45), nullable=False)
    template_details = Column(JSON, nullable=True)
    notes = Column(Text, nullable=True)


class IndianFoodMaster(Base):
    """
    Comprehensive Indian food database with diet types, regional cuisines, and health tags.
    Supports personalized meal planning for Indian diet preferences.
    """
    __tablename__ = "indian_food_master"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)

    # Basic food information
    food_name = Column(String(200), nullable=False, index=True)
    food_name_hindi = Column(String(200), nullable=True)
    food_name_regional = Column(String(200), nullable=True)
    category = Column(String(100), nullable=False, index=True)  # e.g., "Breakfast", "Main Course", "Snacks"
    description = Column(Text, nullable=True)

    # Nutritional information (per standard serving)
    quantity = Column(String(100), nullable=False)  # e.g., "1 medium bowl (150g)"
    calories = Column(Float, nullable=False)
    protein = Column(Float, nullable=False)
    carbs = Column(Float, nullable=False)
    fat = Column(Float, nullable=False)
    fiber = Column(Float, nullable=False, default=0)
    sugar = Column(Float, nullable=False, default=0)

    # Micronutrients
    calcium = Column(Float, nullable=True, default=0)
    magnesium = Column(Float, nullable=True, default=0)
    potassium = Column(Float, nullable=True, default=0)
    iodine = Column(Float, nullable=True, default=0)
    iron = Column(Float, nullable=True, default=0)
    vitamin_a = Column(Float, nullable=True, default=0)
    vitamin_c = Column(Float, nullable=True, default=0)
    vitamin_d = Column(Float, nullable=True, default=0)

    # Diet type categorization (multiple can be true)
    is_vegetarian = Column(Boolean, default=False, index=True)
    is_non_vegetarian = Column(Boolean, default=False, index=True)
    is_vegan = Column(Boolean, default=False, index=True)
    is_eggetarian = Column(Boolean, default=False, index=True)
    is_jain = Column(Boolean, default=False, index=True)
    is_paleo = Column(Boolean, default=False, index=True)
    is_ketogenic = Column(Boolean, default=False, index=True)

    # Regional cuisine
    cuisine_type = Column(String(100), nullable=True, index=True)  # "North Indian", "South Indian", "Common"
    state_origin = Column(String(100), nullable=True)  # e.g., "Punjab", "Tamil Nadu", "All India"

    # Meal slot suitability
    suitable_for_early_morning = Column(Boolean, default=False)
    suitable_for_pre_breakfast = Column(Boolean, default=False)
    suitable_for_breakfast = Column(Boolean, default=False)
    suitable_for_mid_morning = Column(Boolean, default=False)
    suitable_for_lunch = Column(Boolean, default=False)
    suitable_for_evening_snack = Column(Boolean, default=False)
    suitable_for_pre_workout = Column(Boolean, default=False)
    suitable_for_post_workout = Column(Boolean, default=False)
    suitable_for_dinner = Column(Boolean, default=False)
    suitable_for_bedtime = Column(Boolean, default=False)

    # Health condition tags
    is_diabetic_friendly = Column(Boolean, default=False, index=True)
    is_high_protein = Column(Boolean, default=False, index=True)
    is_low_calorie = Column(Boolean, default=False, index=True)
    is_weight_loss_friendly = Column(Boolean, default=False, index=True)
    is_muscle_gain_friendly = Column(Boolean, default=False, index=True)
    is_heart_healthy = Column(Boolean, default=False, index=True)
    is_gluten_free = Column(Boolean, default=False, index=True)
    is_lactose_free = Column(Boolean, default=False, index=True)

    # Food type tags
    is_liquid = Column(Boolean, default=False, index=True)  # Beverages/drinks/liquid items

    # Glycemic Index and Load
    glycemic_index = Column(Integer, nullable=True)  # 0-100
    glycemic_load = Column(Float, nullable=True)

    # Additional metadata
    preparation_time_mins = Column(Integer, nullable=True)
    difficulty_level = Column(String(50), nullable=True)  # "Easy", "Medium", "Hard"
    is_seasonal = Column(Boolean, default=False)
    season_availability = Column(String(100), nullable=True)  # e.g., "Summer", "Winter", "All Year"

    # Image and tags
    image_url = Column(Text, nullable=True)
    tags = Column(JSON, nullable=True)  # Additional searchable tags

    # Status flags
    is_active = Column(Boolean, default=True, index=True)
    is_verified = Column(Boolean, default=False)
    popularity_score = Column(Integer, default=0)  # For ranking common foods

    # Timestamps
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # Indexes for performance
    __table_args__ = (
        Index('idx_diet_cuisine', 'is_vegetarian', 'is_non_vegetarian', 'cuisine_type'),
        Index('idx_health_tags', 'is_diabetic_friendly', 'is_high_protein', 'is_low_calorie'),
        Index('idx_meal_slots', 'suitable_for_breakfast', 'suitable_for_lunch', 'suitable_for_dinner'),
    )


class AiDietCoachFood(Base):
    __tablename__ = "ai_dietcoach_food"

    id = Column(Integer, primary_key=True, autoincrement=True)
    img_type = Column(String(30), nullable=True)
    img_name = Column(String(50), nullable=True)
    img_url = Column(String(600), nullable=True)
