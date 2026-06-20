from pydantic import BaseModel

from .schemas import HomeDTO


class GetHomeResponse(BaseModel):
    status: int = 200
    data: HomeDTO
