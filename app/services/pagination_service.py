"""
Centralized pagination helpers.

Eliminates duplicated offset calculation and pagination metadata construction
across admin, marketing, and API endpoints.
"""

from typing import Any, List


def paginate_query(page: int, per_page: int) -> tuple:
    """
    Return (offset, limit) for SQL queries.

    Usage::

        offset, limit = paginate_query(page, per_page)
        results = db.query(Model).offset(offset).limit(limit).all()
    """
    page = max(page, 1)
    per_page = max(per_page, 1)
    offset = (page - 1) * per_page
    return offset, per_page


def build_pagination_meta(
    page: int,
    per_page: int,
    total_records: int,
) -> dict:
    """
    Build a standard pagination metadata dict.

    Returns::

        {
            "page": 2,
            "per_page": 20,
            "total_records": 153,
            "total_pages": 8,
            "has_next": True,
            "has_prev": True,
        }
    """
    total_pages = (total_records + per_page - 1) // per_page if per_page else 0
    return {
        "page": page,
        "per_page": per_page,
        "total_records": total_records,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
    }


def paginated_response(
    data: Any,
    page: int,
    per_page: int,
    total_records: int,
    message: str = "Data fetched successfully",
) -> dict:
    """Return a complete paginated API response envelope."""
    return {
        "status": 200,
        "message": message,
        "data": data,
        "pagination": build_pagination_meta(page, per_page, total_records),
    }
