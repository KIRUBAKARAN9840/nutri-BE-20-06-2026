import base64
import time
import asyncio
import logging
from datetime import date, datetime
from typing import List, Optional, Dict, Any
import random
from fastapi import HTTPException, UploadFile
from redis.asyncio import Redis
from sqlalchemy.orm import Session

from app.utils.redis_config import get_redis
from app.utils.ai_sanitizer import (
    sanitize_food_name,
    validate_image_magic_bytes,
    MAX_IMAGES_PER_REQUEST
)

logger = logging.getLogger(__name__)

from .repository import (
    FoodScannerRepository,
    get_meal_by_current_time,
    calculate_totals,
)
from .schemas import (
    AnalyzeResponse,
    AnalyzeResponseData,
    SaveFoodResponse,
    HealthCheckResponse,
    ConcurrencyStatus,
    CircuitBreakerStatus,
    JobStatusResponse,
)
from .ai_service import analyze_image, analyze_text, _normalise, PRIMARY_MODEL


# Configuration
SUPPORTED_FORMATS = {"image/jpeg", "image/jpg", "image/png", "image/avif", "image/webp", "application/octet-stream"}
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".avif", ".webp"}
MAX_FILE_MB = 1

# Timezone
import pytz
IST = pytz.timezone("Asia/Kolkata")


class FoodScannerService:
    """Service for food scanner operations."""

    def __init__(self, db: Session, redis: Redis):
        self.repo = FoodScannerRepository(db)
        self.redis = redis
        self.db = db

    async def health_check(self) -> HealthCheckResponse:
        """Return health status with metrics."""
        return HealthCheckResponse(
            status="healthy",
            timestamp=datetime.now(IST).isoformat(),
            metrics={
                "food_scanner": {
                    "total_calls": 0,
                    "success_rate": "100%",
                    "avg_latency_ms": "0ms"
                }
            },
            circuit_breakers={
                "openai_primary": CircuitBreakerStatus(is_open=False, failures=0),
                "openai_secondary": CircuitBreakerStatus(is_open=False, failures=0),
                "gemini": CircuitBreakerStatus(is_open=False, failures=0),
            },
            concurrency=ConcurrencyStatus(
                openai_semaphore_available=50,
                gemini_semaphore_available=30,
                cpu_semaphore_available=20,
                cpu_executor_threads=20,
            )
        )

    async def check_rate_limit(self, client_id: int, food_scan: Optional[bool]) -> bool:
        """Check if client has exceeded rate limits."""
        if food_scan is not None and client_id is not None:
            today_date = date.today().strftime("%Y-%m-%d")
            food_scan_key = f"{client_id}:food_scan:{today_date}"
            await self.redis.set(food_scan_key, "used", ex=86400)

        if client_id is not None:
            today_date = date.today().strftime("%Y-%m-%d")
            scans_key = f"{client_id}:food_scan_count:{today_date}"

            current_scans_raw = await self.redis.get(scans_key)
            current_scans = int(current_scans_raw) if current_scans_raw else 0

            if current_scans >= 30:
                return False

            new_scan_total = await self.redis.incr(scans_key)
            if new_scan_total == 1:
                await self.redis.expire(scans_key, 86400)
            if new_scan_total > 30:
                await self.redis.decr(scans_key)
                return False

        return True

    def _is_supported_image(self, content_type: str, filename: str = "") -> bool:
        """Check if image format is supported."""
        if content_type in {"image/jpeg", "image/jpg", "image/png", "image/avif", "image/webp"}:
            return True
        if content_type == "application/octet-stream" and filename:
            ext = f".{filename.lower().split('.')[-1]}" if '.' in filename else ""
            return ext in SUPPORTED_EXTENSIONS
        return False

    async def validate_and_prepare_images(
        self,
        files: List[UploadFile]
    ) -> List[Dict[str, Any]]:
        """Validate and prepare images for processing."""
        if not files:
            raise HTTPException(status_code=400, detail="No files supplied")

        if len(files) > MAX_IMAGES_PER_REQUEST:
            raise HTTPException(
                status_code=400,
                detail=f"Maximum {MAX_IMAGES_PER_REQUEST} images per request"
            )

        raw_image_data_list = []
        for uf in files:
            if not self._is_supported_image(uf.content_type, uf.filename or ""):
                supported_formats = "JPEG, JPG, PNG, AVIF, WebP"
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported file format. Please upload: {supported_formats}"
                )

            raw = await uf.read()

            if not validate_image_magic_bytes(raw):
                raise HTTPException(
                    status_code=400,
                    detail="Invalid image file. File content does not match a supported image format."
                )

            image_data = {
                "raw_data": base64.b64encode(raw).decode('utf-8'),
                "content_type": uf.content_type,
                "filename": uf.filename or ""
            }
            raw_image_data_list.append(image_data)

        return raw_image_data_list

    async def analyze_images(
        self,
        files: List[UploadFile],
        client_id: Optional[int] = None,
        food_scan: Optional[bool] = None
    ) -> AnalyzeResponse:
        """Analyze food images using AI."""
        start_time = time.time()

        # Check rate limit
        if not await self.check_rate_limit(client_id, food_scan):
            return AnalyzeResponse(
                status=200,
                data=AnalyzeResponseData(
                    items=[],
                    totals={},
                    micro_nutrients={},
                    insights=[],
                    message="Daily food scan limit reached. Please try again tomorrow."
                )
            )

        # Validate images
        raw_image_data_list = await self.validate_and_prepare_images(files)

        # Step 1: Offload compression to Celery (CPU-bound)
        from app.tasks.image_scanner_tasks import compress_food_images
        from celery.result import AsyncResult

        task = compress_food_images.delay(raw_image_data_list=raw_image_data_list)
        logger.info("Compression task queued: %s", task.id)

        # Wait for compression result (fast - typically <2s)
        max_wait = 30
        poll_interval = 0.3
        elapsed = 0

        compressed_images = None
        while elapsed < max_wait:
            celery_task = AsyncResult(task.id)
            if celery_task.ready():
                if celery_task.successful():
                    compressed_images = celery_task.result
                    logger.info("Compression done in %.2fs", time.time() - start_time)
                    break
                else:
                    logger.warning("Compression failed: %s", celery_task.info)
                    raise HTTPException(status_code=500, detail=f"Image compression failed: {str(celery_task.info)}")
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        if compressed_images is None:
            raise HTTPException(status_code=504, detail="Image compression timed out.")

        # Step 2: AI analysis using v2 AI service (I/O-bound - async)
        all_items = []
        all_insights = []
        ai_primary_name = None  # Store AI's primary_name if provided
        model_used = None  # Track which AI model succeeded

        for img in compressed_images:
            image_bytes = base64.b64decode(img["compressed_b64"])
            result = await analyze_image(image_bytes, img["content_type"])
            all_items.extend(result.get("items", []))
            all_insights.extend(result.get("insights", []))
            # Get primary_name from AI (only from first image if multiple)
            if not ai_primary_name and result.get("primary_name"):
                ai_primary_name = result.get("primary_name")
            # Track which model was used (from first successful result)
            if not model_used and result.get("model_used"):
                model_used = result.get("model_used")

        # Normalize and calculate totals
        enriched_items = _normalise(all_items)

        # Calculate totals - use proper None handling
        def safe_sum(items, key):
            return round(sum(
                (item.get(key) if item.get(key) is not None else 0)
                for item in items
            ), 2)

        totals = {
            "calories": int(safe_sum(enriched_items, "calories")),
            "protein_g": int(safe_sum(enriched_items, "protein_g")),
            "carbs_g": int(safe_sum(enriched_items, "carbs_g")),
            "fat_g": int(safe_sum(enriched_items, "fat_g")),
            "fibre_g": int(safe_sum(enriched_items, "fibre_g")),
            "sugar_g": int(safe_sum(enriched_items, "sugar_g")),
        }

        micro_nutrients = {
            "calcium_mg": safe_sum(enriched_items, "calcium_mg"),
            "magnesium_mg": safe_sum(enriched_items, "magnesium_mg"),
            "sodium_mg": safe_sum(enriched_items, "sodium_mg"),
            "potassium_mg": safe_sum(enriched_items, "potassium_mg"),
            "iron_mg": safe_sum(enriched_items, "iron_mg"),
            "iodine_mcg": safe_sum(enriched_items, "iodine_mcg"),
        }

        # Use AI-provided primary_name, or fallback to first item label
        primary_food = ai_primary_name or (enriched_items[0].get("label") if enriched_items else "Unknown")

        # Build items array with individual nutrition (always show, even for single item)
        items_with_nutrition = []
        for idx, item in enumerate(enriched_items, start=1):
            # Use AI-provided label directly (no code-based cleaning)
            label = item.get("label", "Unknown")

            items_with_nutrition.append({
                "id": idx,
                "name": label,
                "calories": int(round(item.get("calories", 0) or 0)),
                "protein_g": int(round(item.get("protein_g", 0) or 0)),
                "carbs_g": int(round(item.get("carbs_g", 0) or 0)),
                "fat_g": int(round(item.get("fat_g", 0) or 0)),
                "fibre_g": int(round(item.get("fibre_g", 0) or 0)),
                "sugar_g": int(round(item.get("sugar_g", 0) or 0)),
            })

        # Deduct 1 credit after successful AI scan — unless the client holds
        # an active unlimited-scan pass (credit_999), in which case scans are free.
        if client_id is not None:
            try:
                from app.fittbot_api.v2.Fymble_Payments.Subscriptions_AiCredits.credits.shared.credit_service import CreditService
                credit_svc = CreditService(self.db)
                if credit_svc.is_unlimited_active(client_id):
                    logger.info("Client %s has active unlimited scan pass — no credit deducted", client_id)
                else:
                    credit_svc.deduct_credit(client_id, amount=1, description="Food scan")
                    self.db.commit()
                    logger.info("Deducted 1 credit for client %s", client_id)
            except Exception as e:
                logger.warning("Credit deduction failed for client %s: %s", client_id, e)

        # Calculate latency
        end_time = time.time()
        latency_ms = (end_time - start_time) * 1000
        return AnalyzeResponse(
            status=200,
            data=AnalyzeResponseData(
                primary_food=primary_food,
                items=items_with_nutrition,
                totals=totals,
                micro_nutrients=micro_nutrients,
                insights=all_insights[:2],
            )
        )

    async def _check_text_rate_limit(self, client_id: Optional[int]) -> bool:
        """Rate limit for /analyse_text: 20 requests per 8 hours per user."""
        if client_id is None:
            return True

        key = f"rate:analyse_text:{client_id}"
        count = await self.redis.incr(key)
        if count == 1:
            # First request — set TTL to 8 hours (28800 seconds)
            await self.redis.expire(key, 28800)
        return count <= 20

    async def analyze_text(
        self,
        food_items: List[Dict[str, Any]],
        client_id: Optional[int] = None,
        model: Optional[str] = None
    ) -> AnalyzeResponse:
        """Analyze food items from text using AI."""
        # Rate limit: 20 per 8 hours
        if not await self._check_text_rate_limit(client_id):
            raise HTTPException(status_code=429, detail="Text analysis limit reached (20 per 8 hours). Please try again later.")

        if not food_items:
            return AnalyzeResponse(
                status=200,
                data=AnalyzeResponseData(
                    items=[],
                    totals={
                        "calories": 0, "protein_g": 0, "carbs_g": 0,
                        "fat_g": 0, "fibre_g": 0, "sugar_g": 0,
                    },
                    micro_nutrients={
                        "calcium_mg": 0, "magnesium_mg": 0, "sodium_mg": 0,
                        "potassium_mg": 0, "iron_mg": 0, "iodine_mcg": 0,
                    },
                    insights=[],
                    message="No food items provided"
                )
            )

        # Pre-validation: sanitize + filter obvious bad input before AI call
        from app.utils.food_validator import get_food_validator
        validator = get_food_validator(self.db)

        sanitized_items = []
        pre_rejected = []
        for item in food_items:
            clean_name = sanitize_food_name(item.get("name", ""))
            if not clean_name:
                pre_rejected.append(item.get("name", ""))
                continue

            # Check blocked words and gibberish (fast, no AI needed)
            validation = validator.validate_food(clean_name)
            if validation["reason"] in ("blocked_harmful", "gibberish"):
                pre_rejected.append(clean_name)
                logger.info("[FoodScanner] Pre-rejected: %s (%s)", clean_name, validation["reason"])
                continue

            # Pass through to AI (let AI decide if it's real food or not)
            item["name"] = clean_name
            sanitized_items.append(item)

        # If all items were pre-rejected
        if not sanitized_items:
            return AnalyzeResponse(
                status=201,
                data=AnalyzeResponseData(
                    items=[],
                    totals={"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0, "fibre_g": 0, "sugar_g": 0},
                    micro_nutrients={},
                    insights=[],
                    message="No valid food items found.",
                )
            )

        selected_model = model or PRIMARY_MODEL
        result = await analyze_text(sanitized_items, selected_model)

        valid_items = result.get("items", [])
        has_rejections = bool(pre_rejected) or bool(result.get("rejected_items"))

        # If AI also rejected everything
        if not valid_items:
            return AnalyzeResponse(
                status=201,
                data=AnalyzeResponseData(
                    items=[],
                    totals={"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0, "fibre_g": 0, "sugar_g": 0},
                    micro_nutrients={},
                    insights=[],
                    message="No valid food items found.",
                )
            )

        # Some valid + some rejected = 201, all valid = 200
        status = 201 if has_rejections else 200

        return AnalyzeResponse(
            status=status,
            data=AnalyzeResponseData(
                items=valid_items,
                totals=result.get("totals", {}),
                micro_nutrients=result.get("micro_nutrients", {}),
                insights=result.get("insights", []),
                message=result.get("message"),
            )
        )

    async def modify_items(
        self,
        action: str,
        item_id: Optional[int],
        items: List[Dict[str, Any]],
        edited_food: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
    ) -> AnalyzeResponse:
        """Add, delete, or edit a food item and recalculate totals."""

        if action == "delete":
            # Remove item by id
            items = [i for i in items if i.get("id") != item_id]

        elif action in ("edit", "add"):
            if not edited_food:
                raise HTTPException(status_code=400, detail="edited_food is required for edit/add action")

            # Pre-validate the food name before calling AI
            from app.utils.food_validator import get_food_validator
            clean_name = sanitize_food_name(edited_food.get("name", ""))
            if not clean_name:
                return AnalyzeResponse(
                    status=201,
                    data=AnalyzeResponseData(
                        items=items, totals={}, micro_nutrients={}, insights=[],
                        message="Invalid food name",
                    )
                )

            validator = get_food_validator(self.db)
            validation = validator.validate_food(clean_name)
            if validation["reason"] in ("blocked_harmful", "gibberish"):
                return AnalyzeResponse(
                    status=201,
                    data=AnalyzeResponseData(
                        items=items, totals={}, micro_nutrients={}, insights=[],
                        message=f"'{clean_name}' is not a valid food item",
                    )
                )

            edited_food["name"] = clean_name

            # Call AI for the single item (AI does final food validation)
            selected_model = model or PRIMARY_MODEL
            result = await analyze_text([edited_food], selected_model)
            rejected = result.get("rejected_items", [])
            # Filter out ghost items: 0-cal "Unknown" entries that _normalise creates as fallback
            new_items = [i for i in result.get("items", []) if i.get("calories", 0) > 0 or i.get("protein_g", 0) > 0]

            if not new_items or rejected:
                return AnalyzeResponse(
                    status=201,
                    data=AnalyzeResponseData(
                        items=items, totals={}, micro_nutrients={}, insights=[],
                        message=f"'{clean_name}' is not a valid food item",
                    )
                )

            if action == "edit":
                new_item = new_items[0]
                new_item["id"] = item_id
                items = [new_item if i.get("id") == item_id else i for i in items]
            else:  # add
                items.append(new_items[0])

        else:
            raise HTTPException(status_code=400, detail="action must be 'add', 'delete', or 'edit'")

        # Reassign sequential ids (1, 2, 3...) after modification
        for idx, item in enumerate(items, start=1):
            item["id"] = idx

        # Recalculate totals from all items
        def safe_sum(key):
            return int(round(sum(i.get(key, 0) or 0 for i in items)))

        totals = {
            "calories": safe_sum("calories"),
            "protein_g": safe_sum("protein_g"),
            "carbs_g": safe_sum("carbs_g"),
            "fat_g": safe_sum("fat_g"),
            "fibre_g": safe_sum("fibre_g"),
            "sugar_g": safe_sum("sugar_g"),
        }

        return AnalyzeResponse(
            status=200,
            data=AnalyzeResponseData(
                items=items,
                totals=totals,
                micro_nutrients={},
                insights=[],
            )
        )

    async def save_food(
        self,
        client_id: int,
        items: List[str],
        totals: Dict[str, float],
        micro_nutrients: Dict[str, float],
        meal: Optional[str] = None
    ) -> SaveFoodResponse:
        """Save scanned food to database."""
        try:
            # Auto-detect meal if not provided
            meal = meal or get_meal_by_current_time()
            today = datetime.now(IST).date()


            # Create food item with unique ID
            unique_id = str(int(time.time() * 1000000)) + str(random.randint(10000, 99999))

            # Combine all scanned items into one food entry
            food_name = ", ".join(sanitize_food_name(item) for item in items)

            food_item = {
                "id": unique_id,
                "name": food_name,
                "quantity": "1 serving",
                "calories": int(totals.get("calories", 0)),
                "protein": round(totals.get("protein_g", 0), 1),
                "carbs": round(totals.get("carbs_g", 0), 1),
                "fat": round(totals.get("fat_g", 0), 1),
                "fiber": round(totals.get("fibre_g", 0), 1),
                "sugar": round(totals.get("sugar_g", 0), 1),
                "calcium": round(micro_nutrients.get("calcium_mg", 0), 1),
                "magnesium": round(micro_nutrients.get("magnesium_mg", 0), 1),
                "sodium": round(micro_nutrients.get("sodium_mg", 0), 1),
                "potassium": round(micro_nutrients.get("potassium_mg", 0), 1),
                "iron": round(micro_nutrients.get("iron_mg", 0), 1),
                "iodine": round(micro_nutrients.get("iodine_mcg", 0), 1),
                "image_url": ""
            }

            # Save to diet
            await self.repo.save_food_to_diet(client_id, meal, food_item, today)

            # Calculate and award XP
            calorie_points = await self.repo.calculate_and_award_xp(client_id, today)


            return SaveFoodResponse(
                status=200,
                message="Food saved successfully",
                meal=meal,
                reward_point=calorie_points
            )

        except Exception as e:
            self.db.rollback()
            raise HTTPException(status_code=500, detail=f"Error saving food: {str(e)}")

    async def create_ai_diet(
        self,
        client_id: int,
        date_str: str,
        scanner_data: dict,
        gym_id: Optional[int],
        meal_category: str
    ) -> dict:
        """Create AI diet entry from food scanner data."""
        from datetime import datetime

        try:
            # Parse date string to date object
            today = datetime.strptime(date_str.split("T")[0], "%Y-%m-%d").date()

            # Create food item from scanner data
            # Use primary_food for the name, with fallback to items list for old format
            primary_food = scanner_data.get("primary_food", "")
            items_list = scanner_data.get("items", [])

            if primary_food:
                food_name = primary_food
            elif items_list and isinstance(items_list[0], dict):
                # New format with nutrition
                food_name = items_list[0].get("name", "Scanned Food")
            elif items_list and isinstance(items_list[0], str):
                # Old format with strings
                food_name = "+".join(items_list)
            else:
                food_name = "Scanned Food"

            food_item = {
                "id": f"{int(time.time() * 1000000)}",
                "name": food_name,
                "calories": scanner_data.get("totals", {}).get("calories", 0),
                "protein": scanner_data.get("totals", {}).get("protein_g", 0),
                "carbs": scanner_data.get("totals", {}).get("carbs_g", 0),
                "fat": scanner_data.get("totals", {}).get("fat_g", 0),
                "fiber": scanner_data.get("totals", {}).get("fibre_g", 0),
                "sugar": scanner_data.get("totals", {}).get("sugar_g", 0),
                "sodium": scanner_data.get("micro_nutrients", {}).get("sodium_mg", 0),
                "calcium": scanner_data.get("micro_nutrients", {}).get("calcium_mg", 0),
                "magnesium": scanner_data.get("micro_nutrients", {}).get("magnesium_mg", 0),
                "potassium": scanner_data.get("micro_nutrients", {}).get("potassium_mg", 0),
                "iron": scanner_data.get("micro_nutrients", {}).get("iron_mg", 0),
                "quantity": "1 serving",
                "image_url": ""
            }

            # Save to diet
            await self.repo.save_food_to_diet(client_id, meal_category, food_item, today)

            # Calculate and award XP
            calorie_points = await self.repo.calculate_and_award_xp(client_id, today)

            # Check feedback status
            from app.fittbot_api.v1.client.client_api.side_bar.ratings import check_feedback_status
            show_feedback = check_feedback_status(self.db, client_id)

            # Check if target exceeded
            from app.models.fittbot_models import ClientTarget, ActualDiet

            target_exceeded = False
            client_target_record = self.db.query(ClientTarget).filter(
                ClientTarget.client_id == client_id
            ).first()

            if client_target_record and client_target_record.calories:
                actual_diet_record = self.db.query(ActualDiet).filter(
                    ActualDiet.client_id == client_id,
                    ActualDiet.date == today.strftime("%Y-%m-%d")
                ).first()

                if actual_diet_record and actual_diet_record.diet_data:
                    total_calories_from_diet = calculate_totals(actual_diet_record.diet_data)
                    actual_calories = total_calories_from_diet.get("calories", 0)

                    if actual_calories > client_target_record.calories:
                        # Check if already shown today via Redis
                        redis_key = f"diet_target_achieved:{client_id}:{client_target_record.calories}:{today}"
                        if await self.redis.exists(redis_key):
                            target_exceeded = False
                        else:
                            await self.redis.set(redis_key, "1", ex=86400)
                            target_exceeded = True


            return {
                "status": 200,
                "reward_point": calorie_points,
                "feedback": show_feedback,
                "target": target_exceeded
            }

        except Exception as e:
            self.db.rollback()
            raise HTTPException(status_code=500, detail=f"Error creating AI diet: {str(e)}")

    async def analyze_async(
        self,
        files: List[UploadFile],
        client_id: Optional[int] = None,
        food_scan: Optional[bool] = None
    ) -> str:
        """Queue async food analysis job."""
        # For now, return a mock job ID
        # In production, this would queue a Celery task
        import uuid
        job_id = str(uuid.uuid4())
        return job_id

    async def get_job_status(self, job_id: str) -> JobStatusResponse:
        """Get status of async analysis job."""
        # For now, return mock status
        # In production, this would check actual Celery job status
        return JobStatusResponse(
            status=200,
            job_id=job_id,
            state="completed",
            result=None,
            error=None
        )
