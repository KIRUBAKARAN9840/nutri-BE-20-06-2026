# Service layer exports
from app.services.cache_service import (
    delete_keys_by_pattern,
    delete_multiple_patterns,
    cache_get_or_set,
    cache_set,
    cache_invalidate,
)
from app.services.s3_service import (
    AWS_REGION,
    BUCKET_NAME,
    generate_presigned_post,
    build_cdn_url,
    generate_presigned_get,
    get_upload_s3,
    get_pdf_s3,
)
from app.services.nutrition_calculator import (
    calculate_age,
    calculate_bmi,
    calculate_bmr,
    calculate_macros_simple,
    activity_multipliers,
    get_water_intake,
)
from app.services.response_service import (
    success_response,
    created_response,
    paginated_response,
)
from app.services.client_service import (
    get_client_or_404,
    get_gym_or_404,
    async_get_client_or_404,
    async_get_gym_or_404,
)
from app.services.db_utils import handle_db_errors
from app.services.websocket_hub import WebSocketHub
