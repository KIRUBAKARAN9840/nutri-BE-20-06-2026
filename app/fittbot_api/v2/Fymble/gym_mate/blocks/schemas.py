from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class BlockedUserDTO(BaseModel):
    block_id: int
    client_id: int
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    blocked_at: datetime
