import re
import time
import os
from typing import Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, File, UploadFile, Form, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc

from app.config.settings import settings
from app.models.async_database import get_async_db
from app.models.adminmodels import Admins
from app.models.nutrition_models import Nutritionist, ClientDoc
from app.models.fittbot_models import Client
from app.fittbot_admin_api.auth.authentication import get_current_admin_from_cookie
from app.fittbot_admin_api.nutritionist_sessions._helpers import resolve_nutritionist_id
from app.services.s3_service import generate_presigned_post, build_cdn_url

router = APIRouter(prefix="/api/admin/nutritionist_sessions", tags=["NutritionistClientDocs"])

ALLOWED_EXTENSIONS = {
    "pdf", "doc", "docx", "png", "jpg", "jpeg", "webp", "txt", "csv", "xlsx", "xls"
}

class ConfirmDocBody(BaseModel):
    cdn_url: str
    file_name: str

@router.get("/client/{client_id}/docs")
async def get_client_docs(
    client_id: int,
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    Lists all documents uploaded for the specific client.
    """
    try:
        # Check if client exists
        client_query = select(Client.client_id).where(Client.client_id == client_id)
        client_result = await db.execute(client_query)
        if not client_result.scalar_one_or_none():
            raise HTTPException(
                status_code=404,
                detail="Client not found"
            )

        # Fetch docs ordered by created_at DESC
        query = select(ClientDoc).where(ClientDoc.client_id == client_id).order_by(desc(ClientDoc.created_at))
        result = await db.execute(query)
        docs = result.scalars().all()

        return {
            "success": True,
            "data": [
                {
                    "id": doc.id,
                    "client_id": doc.client_id,
                    "nutritionist_id": doc.nutritionist_id,
                    "url": doc.url,
                    "file_name": doc.file_name,
                    "created_at": doc.created_at.isoformat() if doc.created_at else None,
                    "updated_at": doc.updated_at.isoformat() if doc.updated_at else None
                } for doc in docs
            ]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while fetching client documents: {str(e)}"
        )

@router.get("/client/{client_id}/docs/upload-url")
async def get_upload_url(
    client_id: int,
    request: Request,
    file_name: str = Query(..., description="The name of the file to be uploaded"),
    content_type: str = Query(..., description="Content type of the file (e.g. application/pdf)"),
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):

    try:
        # Check if client exists
        client_query = select(Client.client_id).where(Client.client_id == client_id)
        client_result = await db.execute(client_query)
        if not client_result.scalar_one_or_none():
            raise HTTPException(
                status_code=404,
                detail="Client not found"
            )

        # Validate file format / extension
        ext = file_name.split(".")[-1].lower() if "." in file_name else ""
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file format. Supported formats: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            )

        # Generate unique key
        timestamp = int(time.time())
        sanitized_name = re.sub(r'[^a-zA-Z0-9._-]', '_', file_name)
        key = f"nutritionist/client_docs/client-{client_id}_{timestamp}_{sanitized_name}"

        # 20 MB size limit
        max_size = 20 * 1024 * 1024

        if settings.environment == "local":
            
            base_url = str(request.base_url).rstrip("/")
            local_url = f"{base_url}/api/admin/nutritionist_sessions/local-upload"
            presigned = {
                "url": local_url,
                "fields": {
                    "key": key,
                    "Content-Type": content_type
                }
            }
            cdn_url = f"{base_url}/uploads/{key}"
        else:
            presigned = generate_presigned_post(
                key=key,
                content_type=content_type,
                max_size=max_size
            )
            cdn_url = build_cdn_url(presigned["url"], key)


        return {
            "success": True,
            "data": {
                "upload": presigned,
                "cdn_url": cdn_url,
                "key": key
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while creating the upload URL: {str(e)}"
        )

@router.post("/local-upload")
async def local_upload(
    key: str = Form(...),
    file: UploadFile = File(...)
):
    """
    Handles local file upload for development environment.
    """
    try:
        target_path = os.path.join("uploads", key)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, "wb") as buffer:
            buffer.write(await file.read())
        return {"success": True}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Local upload failed: {str(e)}"
        )

@router.post("/client/{client_id}/docs/confirm")
async def confirm_upload(
    client_id: int,
    body: ConfirmDocBody,
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    Commits the successfully uploaded file's URL and name to the clients_docs table.
    """
    try:
        # Check if client exists
        client_query = select(Client.client_id).where(Client.client_id == client_id)
        client_result = await db.execute(client_query)
        if not client_result.scalar_one_or_none():
            raise HTTPException(
                status_code=404,
                detail="Client not found"
            )

        nutritionist_id = await resolve_nutritionist_id(admin, db)

        new_doc = ClientDoc(
            client_id=client_id,
            nutritionist_id=nutritionist_id,
            url=body.cdn_url,
            file_name=body.file_name
        )

        db.add(new_doc)
        await db.commit()
        await db.refresh(new_doc)

        return {
            "success": True,
            "message": "Document registered successfully",
            "data": {
                "id": new_doc.id,
                "client_id": new_doc.client_id,
                "nutritionist_id": new_doc.nutritionist_id,
                "url": new_doc.url,
                "file_name": new_doc.file_name,
                "created_at": new_doc.created_at.isoformat() if new_doc.created_at else None
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while confirming the document upload: {str(e)}"
        )

@router.delete("/client/{client_id}/docs/{doc_id}")
async def delete_client_doc(
    client_id: int,
    doc_id: int,
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    Deletes a specific document for a client.
    """
    try:
        query = select(ClientDoc).where(and_(ClientDoc.id == doc_id, ClientDoc.client_id == client_id))
        result = await db.execute(query)
        doc = result.scalar_one_or_none()
        
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
            
        await db.delete(doc)
        await db.commit()
        
        return {"success": True, "message": "Document deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while deleting the document: {str(e)}"
        )


