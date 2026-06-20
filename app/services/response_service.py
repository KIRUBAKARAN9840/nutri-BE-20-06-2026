"""
Standardized API response helpers.

Provides a consistent response envelope so every endpoint returns the same
shape: {"status": <int>, "message": <str>, "data": <any>}.
"""

from typing import Any, Optional


def success_response(
    data: Any = None,
    message: str = "Success",
    status: int = 200,
) -> dict:
    """Return a standard success envelope."""
    return {"status": status, "message": message, "data": data}


def created_response(
    data: Any = None,
    message: str = "Created successfully",
) -> dict:
    """Return a standard 201 envelope."""
    return {"status": 201, "message": message, "data": data}


def paginated_response(
    data: Any,
    page: int,
    per_page: int,
    total_records: int,
    message: str = "Data fetched successfully",
) -> dict:
    """Return a standard paginated envelope."""
    total_pages = (total_records + per_page - 1) // per_page if per_page else 0
    return {
        "status": 200,
        "message": message,
        "data": data,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total_records": total_records,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
        },
    }
