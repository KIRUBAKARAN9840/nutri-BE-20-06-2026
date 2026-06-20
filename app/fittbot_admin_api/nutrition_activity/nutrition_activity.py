from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional
from datetime import datetime
import logging

from app.models.async_database import get_async_db
from app.models.adminmodels import Admins, UserLoginLogs

router = APIRouter(prefix="/api/admin/auth", tags=["NutritionActivity"])
logger = logging.getLogger("nutrition_activity")

ALLOWED_NUTRITIONIST_IDS = [7]

@router.get("/nutrition-activity")
async def get_nutrition_activity(
    page: int = Query(1, ge=1, description="Page number (starting from 1)"),
    limit: int = Query(50, ge=1, le=100, description="Items per page"),
    search: Optional[str] = Query(None, description="Optional search filter by admin name"),
    name: Optional[str] = Query(None, description="Optional filter by exact nutritionist name"),
    start_date: Optional[str] = Query(None, description="Optional start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="Optional end date (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_async_db)
):

    try:
        # Check and create user_login_logs table if it does not exist
        try:
            # Table creation is usually handled during app startup, but kept as non-blocking check
            conn = await db.connection()
            await conn.run_sync(UserLoginLogs.__table__.create, checkfirst=True)
        except Exception as table_err:
            logger.error(f"Error checking/creating user_login_logs table: {str(table_err)}")

        # Parse dates
        start_date_obj = None
        end_date_obj = None
        if start_date:
            try:
                start_date_obj = datetime.strptime(start_date, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid start_date format. Use YYYY-MM-DD")
        if end_date:
            try:
                end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid end_date format. Use YYYY-MM-DD")

        # Fetch unique nutritionist names for the filter dropdown from the allowed IDs
        names_query = select(Admins.name).where(Admins.admin_id.in_(ALLOWED_NUTRITIONIST_IDS)).distinct()
        names_result = await db.execute(names_query)
        nutritionist_names = [row[0] for row in names_result.all() if row[0]]

        # 1. Build the count query to calculate total matched records
        count_query = select(func.count()).select_from(UserLoginLogs).join(
            Admins, UserLoginLogs.user_id == Admins.admin_id
        ).where(UserLoginLogs.user_id.in_(ALLOWED_NUTRITIONIST_IDS))

        # Apply search filter efficiently in count query if provided
        if search:
            search_pattern = f"%{search}%"
            count_query = count_query.where(Admins.name.ilike(search_pattern))

        # Apply exact name filter if provided
        if name:
            count_query = count_query.where(Admins.name == name)

        # Apply date filters to count query
        if start_date_obj:
            count_query = count_query.where(UserLoginLogs.login_time >= start_date_obj)
        if end_date_obj:
            count_query = count_query.where(UserLoginLogs.login_time <= end_date_obj)

        # Execute count query asynchronously
        count_result = await db.execute(count_query)
        total_count = count_result.scalar() or 0

        # 2. Build the paginated data query using offset and limit to fetch only current page records
        offset = (page - 1) * limit
        data_query = select(
            Admins.name,
            UserLoginLogs.login_time
        ).join(
            UserLoginLogs, UserLoginLogs.user_id == Admins.admin_id
        ).where(UserLoginLogs.user_id.in_(ALLOWED_NUTRITIONIST_IDS))

        # Apply search filter efficiently in data query if provided
        if search:
            search_pattern = f"%{search}%"
            data_query = data_query.where(Admins.name.ilike(search_pattern))

        # Apply exact name filter if provided
        if name:
            data_query = data_query.where(Admins.name == name)

        # Apply date filters to data query
        if start_date_obj:
            data_query = data_query.where(UserLoginLogs.login_time >= start_date_obj)
        if end_date_obj:
            data_query = data_query.where(UserLoginLogs.login_time <= end_date_obj)

        # Apply ordering, limit, and offset in database layer (prevents N+1 and loading entire dataset)
        data_query = data_query.order_by(UserLoginLogs.login_time.desc()).offset(offset).limit(limit)

        # Execute data query asynchronously
        data_result = await db.execute(data_query)
        rows = data_result.all()

        # Format paginated results
        activity_list = []
        for row_name, login_time in rows:
            activity_list.append({
                "name": row_name,
                "login_time": login_time.strftime("%Y-%m-%d %H:%M:%S") if login_time else None
            })

        total_pages = (total_count + limit - 1) // limit if limit > 0 else 0

        return {
            "status": 200,
            "data": activity_list,
            "nutritionists": nutritionist_names,
            "pagination": {
                "total": total_count,
                "page": page,
                "limit": limit,
                "totalPages": total_pages,
                "hasNext": page < total_pages,
                "hasPrev": page > 1
            }
        }
    except Exception as e:
        logger.error(f"Error fetching nutritionist activity logs: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while fetching activity logs: {str(e)}"
        )
