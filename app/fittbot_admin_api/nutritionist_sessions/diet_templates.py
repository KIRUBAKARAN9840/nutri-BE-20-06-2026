from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, or_
from typing import List, Optional
from datetime import datetime
import re

def parse_base_quantity(qty_str):
    if not qty_str:
        return 100.0, 'g'
    match = re.search(r"(\d+(\.\d+)?)", qty_str)
    if match:
        num = float(match.group(1))
        unit = re.sub(r"[0-9. ]", "", qty_str) or 'g'
        return num, unit
    return 100.0, 'g'

from app.models.async_database import get_async_db
from app.models.adminmodels import Admins
from app.models.nutrition_models import Nutritionist, DietTemplate, ClientDietTemplate
from app.models.fittbot_models import Food
from app.fittbot_admin_api.auth.authentication import get_current_admin_from_cookie
from app.fittbot_admin_api.nutritionist_sessions._helpers import resolve_nutritionist_id
from app.utils.totp_utils import decrypt_totp_secret, verify_totp_code

router = APIRouter(prefix="/api/admin/nutritionist_diet_templates", tags=["NutritionistDietTemplates"])


@router.post("/create")
async def create_diet_template(
    template_data: dict,
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    Create a new diet template.

    Request body:
    {
        "template_name": "Weight Lose",
        "number_of_days": 7,
        "description": "Optional description",
        "diet_data": [
            {
                "day_number": 1,
                "meals": [
                    {
                        "title": "Pre workout",
                        "time": "6:30-7:00 AM",
                        "foods": [
                            {
                                "name": "Spicy Grilled Chicken Strip",
                                "quantity": 2,
                                "nutrition": {
                                    "calories": 115,
                                    "protein": 24,
                                    "fat": 2,
                                    "carbs": 0,
                                    "fiber": 0,
                                    "sugar": 0,
                                    "sodium": 58,
                                    "calcium": 30,
                                    "iron": 1.1,
                                    "magnesium": 17,
                                    "potassium": 150
                                }
                            }
                        ]
                    }
                ]
            }
        ]
    }
    """
    try:
        nutritionist_id = await resolve_nutritionist_id(admin, db)

        # Validate required fields
        if "template_name" not in template_data or not template_data["template_name"]:
            raise HTTPException(
                status_code=400,
                detail="template_name is required"
            )

        if "session_no" not in template_data or template_data["session_no"] is None:
            raise HTTPException(
                status_code=400,
                detail="session_no is required"
            )

        if "number_of_days" not in template_data or not template_data["number_of_days"]:
            raise HTTPException(
                status_code=400,
                detail="number_of_days is required"
            )

        if "diet_data" not in template_data or not isinstance(template_data["diet_data"], list):
            raise HTTPException(
                status_code=400,
                detail="diet_data is required and must be an array"
            )

        # Create the diet template
        new_template = DietTemplate(
            nutritionist_id=nutritionist_id,
            template_name=template_data["template_name"],
            number_of_days=template_data["number_of_days"],
            diet_data=template_data["diet_data"],
            description=template_data.get("description"),
            session_no=template_data.get("session_no")
        )

        db.add(new_template)
        await db.commit()
        await db.refresh(new_template)

        return {
            "success": True,
            "data": {
                "id": new_template.id,
                "template_name": new_template.template_name,
                "number_of_days": new_template.number_of_days,
                "diet_data": new_template.diet_data,
                "description": new_template.description,
                "session_no": new_template.session_no,
                "created_at": new_template.created_at.isoformat() if new_template.created_at else None
            },
            "message": "Diet template created successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while creating diet template: {str(e)}"
        )


@router.get("/list")
async def get_diet_templates(
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    Get all diet templates for the nutritionist.
    """
    try:
        nutritionist_id = await resolve_nutritionist_id(admin, db)

        # Get all distinct assigned template IDs
        assigned_query = select(ClientDietTemplate.template_id).distinct()
        assigned_result = await db.execute(assigned_query)
        assigned_ids = set(assigned_result.scalars().all())

        query = select(DietTemplate).where(
            DietTemplate.nutritionist_id == nutritionist_id
        ).order_by(
            DietTemplate.created_at.desc()
        )

        result = await db.execute(query)
        templates = result.scalars().all()

        template_list = []
        for template in templates:
            template_list.append({
                "id": template.id,
                "template_name": template.template_name,
                "number_of_days": template.number_of_days,
                "description": template.description,
                "session_no": template.session_no,
                "is_assigned": template.id in assigned_ids,
                "created_at": template.created_at.isoformat() if template.created_at else None,
                "updated_at": template.updated_at.isoformat() if template.updated_at else None
            })

        return {
            "success": True,
            "data": {
                "templates": template_list,
                "count": len(template_list)
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while fetching diet templates: {str(e)}"
        )


@router.get("/template/{template_id}")
async def get_diet_template(
    template_id: int,
    verified: bool = Query(False, description="Whether the fetch request has been verified via 2FA/OTP"),
    for_edit: bool = Query(False, description="Whether the template is fetched for editing purposes"),
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    Get a specific diet template by ID with full diet data.
    If the template is assigned, it requires verified=True to proceed with editing.
    """
    try:
        nutritionist_id = await resolve_nutritionist_id(admin, db)

        query = select(DietTemplate).where(
            and_(
                DietTemplate.id == template_id,
                DietTemplate.nutritionist_id == nutritionist_id
            )
        )

        result = await db.execute(query)
        template = result.scalar_one_or_none()

        if not template:
            raise HTTPException(
                status_code=404,
                detail="Diet template not found"
            )

        # Check if the template is assigned
        assignment_query = select(ClientDietTemplate).where(
            ClientDietTemplate.template_id == template_id
        )
        assignment_result = await db.execute(assignment_query)
        assignments = assignment_result.scalars().all()

        is_assigned = len(assignments) > 0

        if is_assigned and for_edit and not verified:
            return {
                "success": True,
                "requires_verification": True,
                "message": "This template is currently assigned to a client. Security verification is required to edit it."
            }

        return {
            "success": True,
            "data": {
                "id": template.id,
                "template_name": template.template_name,
                "number_of_days": template.number_of_days,
                "diet_data": template.diet_data,
                "description": template.description,
                "session_no": template.session_no,
                "created_at": template.created_at.isoformat() if template.created_at else None,
                "updated_at": template.updated_at.isoformat() if template.updated_at else None
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while fetching diet template: {str(e)}"
        )


@router.put("/template/{template_id}")
async def update_diet_template(
    template_id: int,
    template_data: dict,
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    Update an existing diet template.
    """
    try:
        nutritionist_id = await resolve_nutritionist_id(admin, db)

        query = select(DietTemplate).where(
            and_(
                DietTemplate.id == template_id,
                DietTemplate.nutritionist_id == nutritionist_id
            )
        )

        result = await db.execute(query)
        template = result.scalar_one_or_none()

        if not template:
            raise HTTPException(
                status_code=404,
                detail="Diet template not found"
            )

        # Update fields
        if "template_name" in template_data:
            template.template_name = template_data["template_name"]
        if "number_of_days" in template_data:
            template.number_of_days = template_data["number_of_days"]
        if "diet_data" in template_data:
            template.diet_data = template_data["diet_data"]
        if "description" in template_data:
            template.description = template_data["description"]
        if "session_no" in template_data:
            if template_data["session_no"] is None:
                raise HTTPException(
                    status_code=400,
                    detail="session_no is required"
                )
            template.session_no = template_data["session_no"]

        template.updated_at = datetime.now()

        await db.commit()
        await db.refresh(template)

        return {
            "success": True,
            "data": {
                "id": template.id,
                "template_name": template.template_name,
                "number_of_days": template.number_of_days,
                "diet_data": template.diet_data,
                "description": template.description,
                "session_no": template.session_no,
                "updated_at": template.updated_at.isoformat() if template.updated_at else None
            },
            "message": "Diet template updated successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while updating diet template: {str(e)}"
        )


@router.delete("/template/{template_id}")
async def delete_diet_template(
    template_id: int,
    verified: bool = Query(False, description="Whether the deletion has been authorized via 2FA/OTP"),
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    Delete a diet template.
    Checks if the template is assigned in client_diet_templates table.
    If not assigned, deletes directly.
    If assigned, requires verified=True to proceed with cascade deletion.
    """
    try:
        nutritionist_id = await resolve_nutritionist_id(admin, db)

        # 1. Fetch the diet template
        query = select(DietTemplate).where(
            and_(
                DietTemplate.id == template_id,
                DietTemplate.nutritionist_id == nutritionist_id
            )
        )

        result = await db.execute(query)
        template = result.scalar_one_or_none()

        if not template:
            raise HTTPException(
                status_code=404,
                detail="Diet template not found"
            )

        # 2. Check if assigned in client_diet_templates
        assignment_query = select(ClientDietTemplate).where(
            ClientDietTemplate.template_id == template_id
        )
        assignment_result = await db.execute(assignment_query)
        assignments = assignment_result.scalars().all()

        is_assigned = len(assignments) > 0

        if is_assigned:
            raise HTTPException(
                status_code=400,
                detail="This template is currently assigned to a client and cannot be deleted."
            )

        # Delete the template itself
        await db.delete(template)
        await db.commit()

        return {
            "success": True,
            "message": "Diet template and all associated assignments deleted successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while deleting diet template: {str(e)}"
        )


@router.get("/food-search")
async def search_foods(
    query: str,
    page: int = 1,
    limit: int = 20,
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    Search foods from fittbot_local.fittbot_food table.
    Returns food suggestions with nutrition info based on search query.
    """
    try:
        if not query or len(query) < 1:
            return {
                "success": True,
                "data": {
                    "foods": []
                }
            }

        search_pattern = f"%{query}%"
        offset = (page - 1) * limit

        # Query using SQLAlchemy ORM
        food_query = select(
            Food.id,
            Food.item,
            Food.quantity,
            Food.calories,
            Food.protein,
            Food.carbs,
            Food.fat,
            Food.fiber,
            Food.sugar,
            Food.calcium,
            Food.magnesium,
            Food.potassium,
            Food.sodium,
            Food.iron
        ).where(
            Food.item.ilike(search_pattern)
        ).order_by(
            Food.item.asc()
        ).limit(limit).offset(offset)

        result = await db.execute(food_query)
        rows = result.all()

        foods = []
        for row in rows:
            qty_str = row[2]
            base_qty, base_unit = parse_base_quantity(qty_str)
            foods.append({
                "id": row[0],
                "name": row[1],
                "quantity": qty_str,
                "base_quantity": base_qty,
                "base_unit": base_unit,
                "nutrition": {
                    "calories": row[3] or 0,
                    "protein": row[4] or 0,
                    "carbs": row[5] or 0,
                    "fat": row[6] or 0,
                    "fiber": row[7] or 0,
                    "sugar": row[8] or 0,
                    "calcium": row[9] or 0,
                    "magnesium": row[10] or 0,
                    "potassium": row[11] or 0,
                    "sodium": row[12] or 0,
                    "iron": row[13] or 0
                }
            })

        return {
            "success": True,
            "data": {
                "foods": foods,
                "count": len(foods)
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while searching foods: {str(e)}"
        )