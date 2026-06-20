from typing import Optional
from pydantic import BaseModel


class AppConfigRequest(BaseModel):
    app: str                          # "fittbot" or "business"
    current_version: str              # e.g. "2.1.0"
    platform: Optional[str] = None    # "android" or "ios"
