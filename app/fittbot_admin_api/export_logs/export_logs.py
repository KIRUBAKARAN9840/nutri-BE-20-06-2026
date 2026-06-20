from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.requests import Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import Optional
import logging

from app.models.database import get_db
from app.models.adminmodels import ExportInfo
from app.fittbot_admin_api.auth.authentication import (
    get_current_user_from_cookie,
    get_current_admin_from_cookie
)

router = APIRouter(prefix="/api/admin/auth", tags=["ExportLogs"])
logger = logging.getLogger("export_logs")


class LogExportRequest(BaseModel):
    export_page_url: str


@router.post("/log_export")
async def log_export(
    request_data: LogExportRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Audit log a successful data export.
    Resolves the current authenticated user's name and role.
    Creates fittbot_admins.export_info table on the fly if it doesn't exist.
    """
    try:
        # Authenticate and get user details
        user, user_type = await get_current_user_from_cookie(request, db)

        # Check and create export_info table if it does not exist
        try:
            ExportInfo.__table__.create(bind=db.get_bind(), checkfirst=True)
        except Exception as table_err:
            logger.error(f"Error checking/creating export_info table: {str(table_err)}")

        # Extract name and role
        name = getattr(user, "name", "Unknown User")
        role = getattr(user, "role", "Unknown Role") or user_type

        # Create audit log record
        new_log = ExportInfo(
            name=name,
            role=role,
            export_page_url=request_data.export_page_url
        )
        db.add(new_log)
        db.commit()

        return {
            "status": 200,
            "message": "Export successfully logged",
            "data": {
                "name": name,
                "role": role,
                "export_page_url": request_data.export_page_url
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to log export: {str(e)}")


@router.get("/export_logs")
async def get_export_logs(
    request: Request,
    page: int = Query(1, ge=1, description="Page number starting from 1"),
    limit: int = Query(50, ge=1, le=100, description="Number of items per page (default: 50)"),
    search: Optional[str] = Query(None, description="Optional filter search term"),
    db: Session = Depends(get_db)
):
    """
    Get paginated audit log records of all successful data exports.
    Only available to admins.
    Handles filters, search queries, and pagination entirely in the database backend.
    """
    try:
        # Verify authenticated user is an admin
        user = await get_current_admin_from_cookie(request, db)

        # Automatically check and create export_info table if it does not exist
        try:
            ExportInfo.__table__.create(bind=db.get_bind(), checkfirst=True)
        except Exception as table_err:
            logger.error(f"Error checking/creating export_info table: {str(table_err)}")

        # Base query mapping the ExportInfo model
        query = db.query(ExportInfo)

        # Apply search filter efficiently in the database if provided (avoids loop operations / N+1)
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    ExportInfo.name.ilike(search_pattern),
                    ExportInfo.role.ilike(search_pattern),
                    ExportInfo.export_page_url.ilike(search_pattern)
                )
            )

        # Compute total matched records count
        total_count = query.count()

        # Compute total pages
        total_pages = (total_count + limit - 1) // limit

        # Retrieve offset-limited records from database
        offset = (page - 1) * limit
        logs = query.order_by(ExportInfo.created_at.desc()).offset(offset).limit(limit).all()

        return {
            "status": 200,
            "message": "Export logs retrieved successfully",
            "data": [
                {
                    "id": log.id,
                    "name": log.name,
                    "role": log.role,
                    "export_page_url": log.export_page_url,
                    "created_at": log.created_at.isoformat() if log.created_at else None,
                    "updated_at": log.updated_at.isoformat() if log.updated_at else None
                }
                for log in logs
            ],
            "pagination": {
                "total": total_count,
                "page": page,
                "limit": limit,
                "totalPages": total_pages,
                "hasNext": page < total_pages,
                "hasPrev": page > 1
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch export logs: {str(e)}")
