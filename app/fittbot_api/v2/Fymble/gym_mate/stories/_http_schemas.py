from typing import Optional

from pydantic import BaseModel, Field

from .schemas import PresignedStoryUploadDTO, StoriesForClientDTO, StoryDTO


class PresignStoryMediaRequest(BaseModel):
    content_type: str = Field(..., description="image/jpeg, image/png, or image/webp")


class PresignStoryMediaResponse(BaseModel):
    status: int = 200
    message: str = "Upload URL issued"
    data: PresignedStoryUploadDTO


class CreateStoryRequest(BaseModel):
    s3_key: str = Field(..., max_length=500)
    media_type: str = Field("image", max_length=20)
    caption: Optional[str] = Field(None, max_length=300)
    audience: str = Field("public", max_length=20)
    thumbnail_key: Optional[str] = Field(None, max_length=500)


class CreateStoryResponse(BaseModel):
    status: int = 200
    message: str = "Story published"
    data: StoryDTO


class DeleteStoryResponse(BaseModel):
    status: int = 200
    message: str = "Story deleted"


class StoriesByClientResponse(BaseModel):
    status: int = 200
    data: StoriesForClientDTO


class ViewStoryResponse(BaseModel):
    status: int = 200
    message: str = "View recorded"
