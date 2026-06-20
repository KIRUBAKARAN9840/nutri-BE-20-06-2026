

import functools
from typing import Optional

from app.utils.logging_utils import FittbotHTTPException


def handle_db_errors(
    error_code: str = "UNEXPECTED_ERROR",
    detail: str = "An unexpected error occurred",
    status_code: int = 500,
):


    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except FittbotHTTPException:
                raise
            except Exception as e:
                # Attempt rollback on the db session if available
                db = kwargs.get("db")
                if db is not None:
                    try:
                        if hasattr(db, "rollback"):
                            # Async session
                            maybe_coro = db.rollback()
                            if maybe_coro is not None:
                                await maybe_coro
                    except Exception:
                        pass  # rollback itself failed; still raise original

                raise FittbotHTTPException(
                    status_code=status_code,
                    detail=detail,
                    error_code=error_code,
                    log_data={"error": repr(e), "fn": func.__name__},
                ) from e

        return wrapper
    return decorator
