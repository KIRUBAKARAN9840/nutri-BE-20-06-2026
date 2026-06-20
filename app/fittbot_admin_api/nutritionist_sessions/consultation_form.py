from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, text
from typing import Dict, Any, Optional
from pydantic import BaseModel

from app.models.async_database import get_async_db
from app.models.nutrition_models import NutritionConsultationForm
from app.fittbot_admin_api.auth.authentication import get_current_admin_from_cookie
from app.fittbot_admin_api.nutritionist_sessions._helpers import resolve_nutritionist_id
from app.models.adminmodels import Admins

router = APIRouter(prefix="/api/admin/nutritionist_consultation", tags=["NutritionistConsultation"])

class ConsultationFormRequest(BaseModel):
    client_id: int
    full_name: Optional[str] = None
    age: Optional[str] = None
    gender: Optional[str] = None
    occupation: Optional[str] = None
    main_health_goal: Optional[str] = None
    native: Optional[str] = None
    current_place: Optional[str] = None
    
    anthropometric_table: Optional[Dict] = None
    recent_changes: Optional[Dict] = None
    fat_distribution: Optional[Dict] = None
    nutritionist_notes: Optional[str] = None
    
    vitamin_deficiencies: Optional[str] = None
    biochemical_issues: Optional[str] = None
    ongoing_medications: Optional[str] = None
    
    clinical_concerns: Optional[Dict] = None
    edema_swelling: Optional[str] = None
    joint_pain: Optional[str] = None
    weakness_dizziness: Optional[str] = None
    other_symptoms: Optional[str] = None
    
    meals_daily: Optional[str] = None
    skip_breakfast: Optional[str] = None
    dinner_timing: Optional[str] = None
    late_night_eating: Optional[str] = None
    diet_preference: Optional[str] = None
    water_intake: Optional[str] = None
    eat_outside_frequency: Optional[str] = None
    food_allergies: Optional[str] = None
    cooking_time: Optional[str] = None
    stay_arrangement: Optional[str] = None
    eating_pattern_desc: Optional[str] = None
    
    daily_routine: Optional[Dict] = None
    lifestyle_habits: Optional[Dict] = None
    exercise_routine: Optional[str] = None
    step_count: Optional[str] = None
    activity_level: Optional[str] = None
    work_mode: Optional[str] = None
    
    main_goals: Optional[str] = None
    consistency_challenges: Optional[str] = None
    expected_support: Optional[str] = None

@router.get("/{client_id}")
async def get_consultation_form(
    client_id: int,
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    try:
        # Fetch the latest consultation form for this client (regardless of nutritionist)
        query = select(NutritionConsultationForm).where(
            NutritionConsultationForm.client_id == client_id
        ).order_by(NutritionConsultationForm.updated_at.desc())
        
        result = await db.execute(query)
        form = result.scalars().first()
        
        if not form:
            return {"success": True, "data": None}
            
        return {"success": True, "data": form}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/save")
async def save_consultation_form(
    payload: ConsultationFormRequest,
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    try:
        nutritionist_id = await resolve_nutritionist_id(admin, db)

        # Check if form already exists for this client
        query = select(NutritionConsultationForm).where(
            NutritionConsultationForm.client_id == payload.client_id
        )
        result = await db.execute(query)
        existing_form = result.scalars().first()

        data = payload.dict(exclude_unset=True)
        data["nutritionist_id"] = nutritionist_id
        
        if existing_form:
            # Update existing form
            for key, value in data.items():
                setattr(existing_form, key, value)
            message = "Consultation form updated successfully"
        else:
            # Create new form
            new_form = NutritionConsultationForm(**data)
            db.add(new_form)
            message = "Consultation form saved successfully"
            
        await db.commit()
        return {"success": True, "message": message}
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
