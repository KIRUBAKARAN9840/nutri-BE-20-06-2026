from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, or_
from typing import List, Optional
from datetime import datetime

from app.models.async_database import get_async_db
from app.models.adminmodels import Admins
from app.models.fittbot_models import Food
from app.fittbot_admin_api.auth.authentication import get_current_admin_from_cookie
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

router = APIRouter(prefix="/api/admin/nutritionist_food_collections", tags=["NutritionistFoodCollections"])

@router.get("/list")
async def list_foods(
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=500),
    search: Optional[str] = None,
    category: Optional[str] = None,
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    List foods with pagination, search, and category filtering.
    """
    try:
        offset = (page - 1) * limit
        filters = []
        
        if search:
            filters.append(Food.item.ilike(f"%{search}%"))
        
        if category:
            filters.append(Food.categories == category)
        
        # Count total records for pagination
        count_query = select(func.count()).select_from(Food)
        if filters:
            count_query = count_query.where(and_(*filters))
        
        total_result = await db.execute(count_query)
        total_records = total_result.scalar()
        
        # Fetch paginated data
        food_query = select(Food)
        if filters:
            food_query = food_query.where(and_(*filters))
        
        food_query = food_query.order_by(Food.item.asc()).offset(offset).limit(limit)
        
        result = await db.execute(food_query)
        foods = result.scalars().all()
        
        # Format the output
        food_list = []
        for food in foods:
            base_qty, base_unit = parse_base_quantity(food.quantity)
            food_list.append({
                "id": food.id,
                "item": food.item,
                "categories": food.categories,
                "quantity": food.quantity,
                "base_quantity": base_qty,
                "base_unit": base_unit,
                "calories": food.calories,
                "protein": food.protein,
                "carbs": food.carbs,
                "fat": food.fat,
                "fiber": food.fiber,
                "sugar": food.sugar,
                "calcium": food.calcium,
                "magnesium": food.magnesium,
                "potassium": food.potassium,
                "sodium": food.sodium,
                "iron": food.iron,
                "pic": food.pic
            })
            
        return {
            "success": True,
            "data": {
                "foods": food_list,
                "total": total_records,
                "pages": (total_records + limit - 1) // limit,
                "current_page": page
            }
        }
    except Exception as e:
        print(f"Error listing foods: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/categories")
async def get_categories(
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    Get distinct food categories for filtering.
    """
    try:
        query = select(Food.categories).distinct().order_by(Food.categories.asc())
        result = await db.execute(query)
        categories = [row[0] for row in result.all() if row[0]]
        return {"success": True, "data": categories}
    except Exception as e:
        print(f"Error fetching categories: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/add")
async def add_food(
    food_data: dict,
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    Add a new food item to the collection.
    """
    try:
        new_food = Food(
            item=food_data.get("item"),
            categories=food_data.get("categories"),
            quantity=food_data.get("quantity", "100g"),
            calories=food_data.get("calories", 0),
            protein=food_data.get("protein", 0),
            carbs=food_data.get("carbs", 0),
            fat=food_data.get("fat", 0),
            fiber=food_data.get("fiber", 0),
            sugar=food_data.get("sugar", 0),
            calcium=food_data.get("calcium", 0),
            magnesium=food_data.get("magnesium", 0),
            potassium=food_data.get("potassium", 0),
            sodium=food_data.get("sodium", 0),
            iron=food_data.get("iron", 0),
            pic=food_data.get("pic"),
            is_manual=True
        )
        db.add(new_food)
        await db.commit()
        await db.refresh(new_food)
        return {"success": True, "data": {"id": new_food.id}}
    except Exception as e:
        await db.rollback()
        print(f"Error adding food: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/edit/{food_id}")
async def edit_food(
    food_id: int,
    food_data: dict,
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    Edit an existing food item.
    """
    try:
        query = select(Food).where(Food.id == food_id)
        result = await db.execute(query)
        food = result.scalar_one_or_none()
        
        if not food:
            raise HTTPException(status_code=404, detail="Food not found")
            
        # Update fields if provided
        for key, value in food_data.items():
            if hasattr(food, key):
                setattr(food, key, value)
        
        await db.commit()
        return {"success": True, "message": "Food updated successfully"}
    except Exception as e:
        await db.rollback()
        print(f"Error editing food: {e}")
        raise HTTPException(status_code=500, detail=str(e))
