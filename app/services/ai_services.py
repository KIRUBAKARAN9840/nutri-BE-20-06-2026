# app/services/ai_services.py
"""
Async AI service functions - direct FastAPI replacements for Celery AI tasks.
All functions use AsyncOpenAI and run in the FastAPI event loop (I/O-bound).

Replaces:
- app/tasks/workout_tasks.py (all tasks)
- app/tasks/meal_tasks.py (all tasks)
- app/tasks/analysis_tasks.py (generate_followup_response)
- app/tasks/voice_tasks.py (extract_food_from_text, transcribe_and_translate, notification tasks)
- app/tasks/image_scanner_tasks.py (analyze_food_text - text analysis only)
"""
import json
import re
import logging
import uuid
import httpx
import orjson
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, date

from app.utils.openai_pool import get_openai_client
from app.utils.async_openai import async_openai_call
from app.utils.redis_config import get_redis
from app.utils.ai_sanitizer import sanitize_user_input, sanitize_food_name

logger = logging.getLogger(__name__)


# ─── ASYNC PROGRESS PUBLISHING ───────────────────────────────────────────────
async def publish_progress_async(task_id: str, data: dict):
    """Publish progress to Redis pub/sub for SSE streaming (async version)"""
    try:
        redis_client = await get_redis()
        await redis_client.publish(f"task:{task_id}", json.dumps(data))
    except Exception as e:
        logger.error(f"Task {task_id}: Failed to publish progress - {e}")


# ═════════════════════════════════════════════════════════════════════════════
# VOICE TASKS (replaces app/tasks/voice_tasks.py)
# ═════════════════════════════════════════════════════════════════════════════

async def extract_food_from_text_async(user_id: int, text: str) -> dict:
    """
    Extract food info from text using async OpenAI.
    Replaces: voice_tasks.extract_food_from_text
    """
    from app.fittbot_api.v1.client.client_api.chatbot.codes.food_log import (
        get_enhanced_ai_prompt,
        parse_food_with_smart_units
    )

    text = sanitize_user_input(text, max_length=2000)
    reasoning_prompt = get_enhanced_ai_prompt(text)
    oai_client = get_openai_client()

    try:
        response = await async_openai_call(
            oai_client,
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": """You are a nutrition expert specializing in Indian cuisine.
                    CRITICAL RULES:
                    1. Compound food words are SINGLE dishes (curdrice = curd rice as one dish)
                    2. Use culturally appropriate units (plates for rice dishes, tablespoons for spoons)
                    3. Never split single dishes into multiple foods
                    4. Respect user's measurement context (3 spoon = 3 tablespoons, not grams)"""
                },
                {"role": "user", "content": reasoning_prompt}
            ],
            max_tokens=1000,
            temperature=0.1
        )

        result = response.choices[0].message.content.strip()
        result = re.sub(r"^```json\s*", "", result)
        result = re.sub(r"\s*```$", "", result)

        foods = json.loads(result)
        if not isinstance(foods, list):
            foods = [foods] if isinstance(foods, dict) else []

        return {"foods": foods}

    except Exception as e:
        logger.warning(f"OpenAI extraction error for user {user_id}: {e}, using fallback parsing")
        return parse_food_with_smart_units(text)


async def calculate_nutrition_using_ai_async(food_name: str, quantity: float, unit: str) -> dict:
    """
    Calculate nutrition for a food item using async OpenAI.
    Replaces: voice_tasks.calculate_nutrition_using_ai_sync
    """
    from app.fittbot_api.v1.client.client_api.chatbot.codes.food_log import (
        ensure_nutrition_fields,
        get_fallback_nutrition
    )

    try:
        prompt = f"""
        Calculate nutrition for: {quantity} {unit} of {food_name}

        Use these REALISTIC conversions:
        - 1 plate (rice dishes) = 300 grams
        - 1 tablespoon = 15 grams (solids) or 15 ml (liquids)
        - 1 teaspoon = 5 grams (solids) or 5 ml (liquids)
        - 1 cup = 200 grams (solids) or 200 ml (liquids)
        - 1 bowl = 200 grams
        - 1 glass = 200 ml
        - 1 piece varies by food type (estimate appropriately)

        Return ONLY valid JSON with realistic values:
        {{
            "calories": number,
            "protein": number,
            "carbs": number,
            "fat": number,
            "fiber": number,
            "sugar": number,
            "calcium": number (in mg),
            "magnesium": number (in mg),
            "sodium": number (in mg),
            "potassium": number (in mg),
            "iron": number (in mg),
            "iodine": number (in mcg)
        }}
        """

        oai_client = get_openai_client()
        response = await async_openai_call(
            oai_client,
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are a nutrition expert. Always provide realistic nutrition values based on the specified quantity and unit."
                },
                {"role": "user", "content": prompt}
            ],
            max_tokens=300,
            temperature=0
        )

        result = response.choices[0].message.content.strip()
        result = re.sub(r"^```json\s*", "", result)
        result = re.sub(r"\s*```$", "", result)

        nutrition = json.loads(result)
        return ensure_nutrition_fields(nutrition)

    except Exception as e:
        logger.warning(f"AI nutrition calculation failed: {e}, using fallback")
        return get_fallback_nutrition(food_name, quantity, unit)


async def transcribe_audio_async(audio_bytes: bytes, context: str = "general") -> str:
    """
    Transcribe audio using Groq API (async httpx).
    Replaces: voice_tasks.transcribe_audio_sync
    """
    from app.fittbot_api.v1.client.client_api.chatbot.chatbot_services.asr import (
        get_groq_api_key, GROQ_BASE_URL, GROQ_ASR_MODEL
    )

    api_key = get_groq_api_key()
    if not api_key:
        raise ValueError("GROQ_API_KEY not set")

    url = f"{GROQ_BASE_URL}/openai/v1/audio/transcriptions"

    async with httpx.AsyncClient(timeout=60.0) as client:
        files = {"file": ("audio.wav", audio_bytes, "audio/wav")}
        data = {"model": GROQ_ASR_MODEL}
        headers = {"Authorization": f"Bearer {api_key}"}

        response = await client.post(url, data=data, files=files, headers=headers)
        response.raise_for_status()

        result = response.json()
        return (result.get("text") or "").strip()


async def transcribe_and_translate_async(user_id: int, audio_bytes: bytes, context: str = "general") -> dict:
    """
    Transcribe audio with Groq and translate to English with OpenAI (async).
    Replaces: voice_tasks.transcribe_and_translate
    """
    # Step 1: Transcribe
    transcript = await transcribe_audio_async(audio_bytes, context)
    if not transcript:
        raise ValueError("Empty transcript from Groq")

    logger.info(f"Transcription for user {user_id}: '{transcript[:100]}...'")

    # Step 2: Translate with OpenAI
    system_prompt = (
        "You are a translator. Output ONLY JSON like "
        "{\"lang\":\"xx\",\"english\":\"...\"} "
        "Detect source language code (ISO-639-1 if possible). "
        "Translate to natural English. Do not add extra words. "
        "Keep food names recognizable; use common transliterations if needed."
    )

    oai_client = get_openai_client()
    response = await async_openai_call(
        oai_client,
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": transcript}
        ],
        response_format={"type": "json_object"},
        temperature=0
    )

    content = (response.choices[0].message.content or "").strip()

    try:
        data = json.loads(content)
        return {
            "transcript": transcript,
            "lang": (data.get("lang") or "unknown").strip(),
            "english": (data.get("english") or transcript).strip()
        }
    except json.JSONDecodeError:
        return {"transcript": transcript, "lang": "unknown", "english": transcript}


async def process_voice_message_async(user_id: int, audio_bytes: bytes, meal: str = None) -> dict:
    """
    Full voice food logging pipeline (async).
    Replaces: voice_tasks.process_voice_message

    Steps: Transcribe → Extract food → Calculate nutrition → Save to DB
    Returns result dict for SSE streaming.
    """
    task_id = str(uuid.uuid4())
    redis_client = await get_redis()

    try:
        # Publish progress via Redis for SSE
        await publish_progress_async(task_id, {
            "status": "progress", "progress": 10, "message": "Processing audio..."
        })

        # Step 1: Transcribe
        from app.fittbot_api.v1.client.client_api.chatbot.codes.food_log import FOOD_LOG_TRANSCRIPTION_PROMPT
        transcript = await transcribe_audio_async(audio_bytes)
        if not transcript:
            raise ValueError("Empty transcript from Groq")

        await publish_progress_async(task_id, {
            "status": "progress", "progress": 30, "message": "Analyzing food items..."
        })

        # Step 2: Extract food
        food_info = await extract_food_from_text_async(user_id, transcript)
        foods = food_info.get("foods", [])
        if not foods:
            raise ValueError("No food items identified")

        await publish_progress_async(task_id, {
            "status": "progress", "progress": 60, "message": "Calculating nutrition..."
        })

        # Step 3: Calculate nutrition (concurrently for multiple foods)
        import asyncio
        for food in foods:
            if food.get("quantity") is not None and food.get("calories") is None:
                nutrition = await calculate_nutrition_using_ai_async(
                    food["name"], food["quantity"], food["unit"]
                )
                food.update(nutrition)

        await publish_progress_async(task_id, {
            "status": "progress", "progress": 80, "message": "Saving to database..."
        })

        # Step 4: Save to DB (using sync DB in thread pool since food_log uses sync ORM)
        from app.models.database import get_db_sync
        from app.utils.redis_config import get_redis_sync
        import pytz

        db = next(get_db_sync())
        try:
            IST = pytz.timezone("Asia/Kolkata")
            today_date = datetime.now(IST).strftime("%Y-%m-%d")
            sync_redis = get_redis_sync()

            if meal:
                from app.tasks.voice_tasks import store_diet_data_to_db_sync
                store_diet_data_to_db_sync(db, sync_redis, user_id, today_date, foods, meal)
        finally:
            db.close()

        result = {
            "type": "food_log",
            "status": "logged",
            "is_log": True,
            "message": f"Logged {len(foods)} food items successfully!",
            "foods": foods,
            "transcript": transcript
        }

        await publish_progress_async(task_id, {
            "status": "completed", "progress": 100, "result": result
        })

        return result, task_id

    except Exception as e:
        logger.error(f"Voice processing failed for user {user_id}: {e}")
        error_result = {"type": "error", "message": f"Failed to process voice: {str(e)}"}
        await publish_progress_async(task_id, {
            "status": "error", "progress": 0, "result": error_result
        })
        raise


async def send_voice_notification_async(user_id: int, notification_type: str, extra_data: dict = None):
    """
    Send voice notification via Redis pub/sub (async).
    Replaces all fire-and-forget voice notification tasks:
    - process_food_log_success_voice
    - process_food_scanner_voice
    - process_workout_log_success_voice
    - process_food_template_voice
    - process_workout_template_voice
    """
    from app.utils.redis_config import get_redis

    redis_client = await get_redis()
    extra_data = extra_data or {}

    # Voice messages by type
    voice_messages = {
        "food_log_success": "Food logged successfully! Tap View Food Logs to see your logged items.",
        "meal_selector": "Select a meal category to log your food.",
        "food_scanner": None,  # Dynamic based on items
        "workout_log_success": "Great job! Workout logged successfully",
        "food_template_creation": "Here is your diet template.",
        "food_template_meal_plan_saved": "Your diet plan saved",
        "workout_template_creation": "Here is your workout plan",
        "workout_template_workout_saved": "Your workout plan has been saved",
    }

    # Handle food scanner with dynamic message
    if notification_type == "food_scanner":
        from app.tasks.voice_tasks import generate_natural_food_message
        items = extra_data.get("items", [])
        voice_message = generate_natural_food_message(items)
    else:
        voice_message = voice_messages.get(notification_type, "Notification received.")

    try:
        message_id = str(uuid.uuid4())
        task_id = str(uuid.uuid4())

        voice_data = {
            "type": f"{notification_type}_voice",
            "user_id": user_id,
            "message": voice_message,
            "timestamp": datetime.utcnow().isoformat(),
            **extra_data,
        }

        # Store in Redis with 5-min TTL
        await redis_client.setex(f"voice_message:{message_id}", 300, json.dumps(voice_data))

        # Publish to WebSocket channel
        websocket_message = {
            "type": "voice_message",
            "task_id": task_id,
            "message_id": message_id,
            "data": voice_data
        }
        await redis_client.publish(f"user_channel:{user_id}", json.dumps(websocket_message))

        logger.info(f"Voice notification '{notification_type}' sent for user {user_id}")
        return {"status": "success", "message_id": message_id}

    except Exception as e:
        logger.error(f"Voice notification failed for user {user_id}: {e}")
        return {"status": "error", "error": str(e)}


# ═════════════════════════════════════════════════════════════════════════════
# WORKOUT TASKS (replaces app/tasks/workout_tasks.py)
# ═════════════════════════════════════════════════════════════════════════════

async def generate_day_names_async(user_id: int, user_request: str, days_count: int) -> list:
    """
    Generate creative day names for workout template.
    Replaces: workout_tasks.generate_day_names
    """
    try:
        system_prompt = f"""You are a creative fitness coach. Generate exactly {days_count} unique, motivating names for workout days based on the user's request.

Rules:
1. Generate exactly {days_count} names
2. Names should be single words only (no spaces)
3. Names should be appropriate for workout days
4. Make them fun and motivating
5. Follow the user's theme/request as closely as possible

Return ONLY a JSON array of strings, nothing else.

Examples:
- User: "animal names" → ["Lion", "Tiger", "Bear", "Wolf", "Eagle"]
- User: "king names" → ["Arthur", "Alexander", "Napoleon", "Caesar", "Viking"]
- User: "superhero names" → ["Thor", "Hulk", "Superman", "Captain", "Storm"]
- User: "first 2 days Lion and Tiger" → ["Lion", "Tiger", "Day3", "Day4", "Day5"]"""

        oai_client = get_openai_client()
        response = await async_openai_call(
            oai_client,
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"User request: '{user_request}' for {days_count} workout days"}
            ],
            temperature=0.7
        )

        content = (response.choices[0].message.content or "").strip()

        try:
            day_names = json.loads(content)
            if isinstance(day_names, list) and len(day_names) == days_count:
                return [str(name).title() for name in day_names]
        except json.JSONDecodeError:
            pass

        return [f"Day{i+1}" for i in range(days_count)]

    except Exception as e:
        logger.error(f"Day name generation failed for user {user_id}: {e}")
        return [f"Day{i+1}" for i in range(days_count)]


async def detect_edit_intent_type_async(user_id: int, user_input: str) -> str:
    """
    Detect the type of edit intent from user input.
    Replaces: workout_tasks.detect_edit_intent_type
    """
    try:
        prompt = f"""Analyze this workout template edit request and respond with EXACTLY one of these:
- BULK_RENAME (if user wants to rename ALL days with a theme like "animal names", "superhero names")
- INDIVIDUAL_RENAME (if user wants to rename a SPECIFIC day like "rename day 1 to X")
- EXERCISE_CHANGE (if user wants to add, remove, or modify exercises)

User request: "{user_input}"

Examples:
- "Change day 1 name as spiderman" → INDIVIDUAL_RENAME
- "Give all days animal names" → BULK_RENAME
- "Add more chest exercises" → EXERCISE_CHANGE
- "Rename day 2 to batman" → INDIVIDUAL_RENAME
- "Remove squats" → EXERCISE_CHANGE

Response:"""

        oai_client = get_openai_client()
        response = await async_openai_call(
            oai_client,
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )

        content = (response.choices[0].message.content or "").strip().upper()
        if content not in ["BULK_RENAME", "INDIVIDUAL_RENAME", "EXERCISE_CHANGE"]:
            content = "EXERCISE_CHANGE"

        return content

    except Exception as e:
        logger.error(f"Edit intent detection failed for user {user_id}: {e}")
        return "EXERCISE_CHANGE"


async def extract_exercises_async(user_id: int, text: str) -> list:
    """
    Extract exercise names from user input text.
    Replaces: workout_tasks.extract_exercises
    """
    try:
        prompt = f"""
        Extract exercise names from this text: "{text}"

        RULES:
        1. Extract ANY exercise or physical activity mentioned
        2. Handle common misspellings and variations
        3. Normalize exercise names to standard form
        4. Be very permissive - if it sounds like exercise, include it

        EXAMPLES:
        - "pushup" → "Push Up"
        - "dumbell bench pres" → "Dumbbell Bench Press"
        - "bicep curls" → "Bicep Curl"
        - "squats and lunges" → ["Squat", "Lunge"]

        Return JSON:
        {{
            "exercises": ["Exercise Name 1", "Exercise Name 2"]
        }}
        """

        oai_client = get_openai_client()
        response = await async_openai_call(
            oai_client,
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are an exercise recognition expert. Extract and normalize exercise names."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1
        )

        content = (response.choices[0].message.content or "").strip()
        content = re.sub(r"^```json\s*", "", content)
        content = re.sub(r"\s*```$", "", content)

        parsed = json.loads(content)
        exercises = parsed.get("exercises", [])
        return [ex.strip() for ex in exercises if ex.strip()]

    except Exception as e:
        logger.error(f"Exercise extraction failed for user {user_id}: {e}")
        return []


async def extract_exercises_with_details_async(user_id: int, text: str) -> list:
    """
    Extract exercises with sets/reps/duration details.
    Replaces: workout_tasks.extract_exercises_with_details
    """
    try:
        prompt = f"""
        Parse workout information from this text: "{text}"

        CRITICAL PARSING RULES:
        - Intensity words (low, light, moderate, medium, high, hard, intense, heavy) belong to the EXERCISE they modify
        - Duration belongs to the exercise it comes after
        - NEVER create separate exercises from intensity or duration words
        - Each exercise object should represent ONE actual physical exercise
        - Format variations: "3x10", "3*10", "3 sets of 10" all mean 3 sets, 10 reps

        Return JSON array:
        [
            {{
                "exercise": "Exercise Name",
                "has_sets_reps": true/false,
                "sets": number or null,
                "reps": number or null,
                "has_duration": true/false,
                "duration_minutes": number or null,
                "intensity": "low/moderate/high" or null
            }}
        ]

        RULES:
        - MOST IMPORTANT: Intensity and duration MODIFY exercises, they are NOT separate exercises
        - Handle both "3x10" and "3*10" formats for sets and reps
        - NEVER return "Low Intensity" or "High Intensity" as an exercise name
        """

        oai_client = get_openai_client()
        response = await async_openai_call(
            oai_client,
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are an exercise recognition expert. Extract exercises and their sets/reps/duration details."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1
        )

        content = (response.choices[0].message.content or "").strip()
        content = re.sub(r"^```json\s*", "", content)
        content = re.sub(r"\s*```$", "", content)

        parsed = json.loads(content)
        if not isinstance(parsed, list):
            parsed = [parsed]
        return parsed

    except Exception as e:
        logger.error(f"Exercise details extraction failed for user {user_id}: {e}")
        return []


async def parse_sets_reps_async(user_id: int, text: str, exercise_name: str = "") -> dict:
    """
    Parse sets and reps from user input text.
    Replaces: workout_tasks.parse_sets_reps
    """
    try:
        prompt = f"""
        Parse sets and reps from this text: "{text}"
        Exercise: {exercise_name}

        Handle these formats:
        - "3 sets 10 reps" → {{"sets": 3, "reps": 10, "format": "uniform"}}
        - "30 in first, 40 in 2nd set" → {{"sets": 2, "reps": [30, 40], "format": "variable"}}
        - "3x12" → {{"sets": 3, "reps": 12, "format": "uniform"}}
        - "15, 12, 10" → {{"sets": 3, "reps": [15, 12, 10], "format": "variable"}}

        Return JSON: {{"sets": number, "reps": number_or_array, "format": "uniform"|"variable"}}
        """

        oai_client = get_openai_client()
        response = await async_openai_call(
            oai_client,
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Extract workout sets/reps data. Return valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1
        )

        content = (response.choices[0].message.content or "").strip()
        content = re.sub(r"^```json\s*", "", content)
        content = re.sub(r"\s*```$", "", content)

        return json.loads(content)

    except Exception as e:
        logger.error(f"Sets/reps parsing failed for user {user_id}: {e}")
        return {"sets": None, "reps": None, "format": "unknown"}


# ═════════════════════════════════════════════════════════════════════════════
# MEAL TASKS (replaces app/tasks/meal_tasks.py)
# ═════════════════════════════════════════════════════════════════════════════

async def translate_text_async(user_id: int, text: str) -> dict:
    """
    Translate text to English using async OpenAI.
    Replaces: meal_tasks.translate_text
    """
    try:
        system_prompt = (
            "You are a translator. Output ONLY JSON like "
            "{\"lang\":\"xx\",\"english\":\"...\"} "
            "Detect source language code (ISO-639-1 if possible). "
            "Translate to natural English. Do not add extra words. "
            "Keep food names recognizable; use common transliterations if needed."
        )

        oai_client = get_openai_client()
        response = await async_openai_call(
            oai_client,
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text}
            ],
            response_format={"type": "json_object"},
            temperature=0
        )

        content = (response.choices[0].message.content or "").strip()
        data = json.loads(content)
        return {
            "lang": (data.get("lang") or "unknown").strip(),
            "english": (data.get("english") or text).strip()
        }

    except Exception as e:
        logger.error(f"Translation failed for user {user_id}: {e}")
        return {"lang": "unknown", "english": text}


async def classify_meal_intent_async(user_id: int, user_input: str, current_state: str) -> dict:
    """
    AI-driven intent classifier for meal planning chatbot.
    Replaces: meal_tasks.classify_meal_intent
    """
    try:
        system_prompt = f"""You are an intent classifier for a meal planning chatbot.
Current conversation state: {current_state}

Classify the user's intent into ONE of these categories:

1. **diet_preference**: User is specifying their diet type
   - Extract: diet_type (vegetarian, non-vegetarian, vegan, eggetarian, jain, ketogenic, paleo)

2. **cuisine_preference**: User is specifying cuisine preference
   - Extract: cuisine_type (north_indian, south_indian, commonly_available)
   - Note: "simple", "basic", "common", "everyday" → commonly_available

3. **food_allergy**: User mentions food allergies or items to avoid
   - Extract: allergens (list of foods/ingredients to avoid)

4. **food_removal**: User wants to remove specific foods
   - Extract: foods_to_remove (list of food items)

5. **food_alternate**: User wants alternatives for specific foods
   - Extract: foods_to_alternate (list of food items to find alternatives for)

6. **health_condition_change**: User mentions a health condition or dietary need
   - Extract: health_conditions (list: diabetic, pregnancy, pcos, hypertension, thyroid, etc.)

7. **save_template**: User wants to save or finalize the template (save, done, finish, etc.)

8. **unclear**: User input is unclear or doesn't match any intent

IMPORTANT: Be flexible with typos, informal language, and variations.

Return ONLY valid JSON in this format:
{{
    "intent": "intent_name",
    "confidence": 0.95,
    "extracted_data": {{
        "key": "value"
    }},
    "normalized_input": "corrected version of user input"
}}"""

        oai_client = get_openai_client()
        response = await async_openai_call(
            oai_client,
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input}
            ],
            response_format={"type": "json_object"},
            temperature=0.3
        )

        content = (response.choices[0].message.content or "").strip()
        return json.loads(content)

    except Exception as e:
        logger.error(f"Intent classification failed for user {user_id}: {e}")
        return {
            "intent": "unclear",
            "confidence": 0.0,
            "extracted_data": {},
            "normalized_input": user_input
        }


# ═════════════════════════════════════════════════════════════════════════════
# ANALYSIS TASKS (replaces app/tasks/analysis_tasks.py - only followup)
# ═════════════════════════════════════════════════════════════════════════════

async def generate_followup_response_async(
    user_id: int,
    user_text: str,
    summary: str,
    dataset: dict = None,
    is_followup: bool = False
) -> str:
    """
    Generate follow-up response to user's question about analysis.
    Replaces: analysis_tasks.generate_followup_response
    """
    try:
        user_text = sanitize_user_input(user_text, max_length=2000)

        from app.fittbot_api.v1.client.client_api.chatbot.chatbot_services.llm_helpers import (
            GENERAL_SYSTEM, STYLE_CHAT_FORMAT, OPENAI_MODEL
        )
        from app.fittbot_api.v1.client.client_api.chatbot.chatbot_services.report_analysis import (
            STYLE_INSIGHT_REPORT
        )

        msgs = [
            {"role": "system", "content": GENERAL_SYSTEM},
            {"role": "system", "content": STYLE_CHAT_FORMAT},
            {"role": "system", "content": STYLE_INSIGHT_REPORT},
            {"role": "assistant", "content": f"Here's your analysis:\n\n{summary}"},
            {"role": "user", "content": user_text},
        ]

        if is_followup and dataset:
            msgs.insert(3, {
                "role": "system",
                "content": f"User's fitness data (for reference):\n{orjson.dumps(dataset).decode()}"
            })

        oai_client = get_openai_client()
        response = await async_openai_call(
            oai_client,
            model=OPENAI_MODEL,
            messages=msgs,
            temperature=0.3
        )

        return (response.choices[0].message.content or "").strip()

    except Exception as e:
        logger.error(f"Followup response failed for user {user_id}: {e}")
        return "I'm having trouble answering that right now. Could you try rephrasing your question?"


# ═════════════════════════════════════════════════════════════════════════════
# FOOD SCANNING - INLINE ANALYSIS (replaces image_scanner_tasks AI portions)
# ═════════════════════════════════════════════════════════════════════════════

async def analyze_food_images_inline(compressed_images: list, food_scan: bool = True) -> dict:
    """
    Run AI food analysis inline in FastAPI (async).
    Takes already-compressed images and runs AI analysis + normalization.
    Replaces the AI portion of: image_scanner_tasks.analyze_food_image_v2

    Args:
        compressed_images: list of (compressed_b64, content_type) tuples
        food_scan: whether this is a food scan

    Returns:
        dict matching the legacy result format
    """
    import base64
    from app.fittbot_api.v1.client.client_api.food_scanner_AI.ai_food_scanner import (
        _ask, _normalise, _get_smart_insights
    )

    all_items = []
    all_insights = []

    for idx, (compressed_b64, content_type) in enumerate(compressed_images):
        try:
            image_bytes = base64.b64decode(compressed_b64)
            result = await _ask(image_bytes, content_type, brief=food_scan)
            all_items.extend(result.get("items", []))
            all_insights.extend(result.get("insights", []))
        except Exception as e:
            logger.error(f"Failed to analyze image {idx + 1}: {e}")
            continue

    # Normalize
    enriched_items = _normalise(all_items)

    # Calculate totals
    food_labels = [item.get("label", "Unknown") for item in enriched_items]

    totals = {
        "calories": int(round(sum(item.get("calories", 0) or 0 for item in enriched_items))),
        "protein_g": int(round(sum(item.get("protein_g", 0) or 0 for item in enriched_items))),
        "carbs_g": int(round(sum(item.get("carbs_g", 0) or 0 for item in enriched_items))),
        "fat_g": int(round(sum(item.get("fat_g", 0) or 0 for item in enriched_items))),
        "fibre_g": int(round(sum(item.get("fibre_g", 0) or 0 for item in enriched_items))),
        "sugar_g": int(round(sum(item.get("sugar_g", 0) or 0 for item in enriched_items))),
    }

    micro_nutrients = {
        "calcium_mg": round(sum(item.get("calcium_mg", 0) or 0 for item in enriched_items), 2),
        "magnesium_mg": round(sum(item.get("magnesium_mg", 0) or 0 for item in enriched_items), 2),
        "sodium_mg": round(sum(item.get("sodium_mg", 0) or 0 for item in enriched_items), 2),
        "potassium_mg": round(sum(item.get("potassium_mg", 0) or 0 for item in enriched_items), 2),
        "iron_mg": round(sum(item.get("iron_mg", 0) or 0 for item in enriched_items), 2),
        "iodine_mcg": round(sum(item.get("iodine_mcg", 0) or 0 for item in enriched_items), 2),
    }

    # Use AI insights if available, otherwise generate smart insights
    final_insights = all_insights[:2] if all_insights else _get_smart_insights(food_labels, totals, micro_nutrients)

    return {
        "success": True,
        "items": sorted(food_labels),
        "totals": totals,
        "micro_nutrients": micro_nutrients,
        "insights": final_insights[:2],
        "message": f"Identified {len(enriched_items)} food items"
    }


async def analyze_food_text_inline(
    user_id: int,
    food_items: list,
    model: str = "gpt-4o-mini"
) -> dict:
    """
    Analyze text-based food items inline in FastAPI (async).
    Replaces: image_scanner_tasks.analyze_food_text

    Args:
        user_id: Client ID
        food_items: List of {"name": str, "quantity": float, "unit": str}
        model: OpenAI model to use

    Returns:
        dict matching the legacy result format
    """
    from app.utils.food_validator import filter_valid_foods
    from app.fittbot_api.v1.client.client_api.food_scanner_AI.ai_food_scanner import (
        _robust_json_parse, _normalise
    )

    def create_unknown_item(name: str) -> dict:
        return {
            "label": f"Unknown ({name})",
            "calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0,
            "fibre_g": 0, "sugar_g": 0, "calcium_mg": 0, "magnesium_mg": 0,
            "sodium_mg": 0, "potassium_mg": 0, "iron_mg": 0, "iodine_mcg": 0,
        }

    # Sanitize
    for item in food_items:
        item["name"] = sanitize_food_name(item.get("name", ""))

    # Validate
    valid_items, invalid_items = filter_valid_foods(food_items)
    unknown_items = [create_unknown_item(inv.get("name", "Unknown")) for inv in invalid_items]

    if not valid_items:
        unknown_labels = [item.get("label", "Unknown") for item in unknown_items]
        return {
            "success": True,
            "items": sorted(unknown_labels),
            "totals": {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0, "fibre_g": 0, "sugar_g": 0},
            "micro_nutrients": {"calcium_mg": 0, "magnesium_mg": 0, "sodium_mg": 0, "potassium_mg": 0, "iron_mg": 0, "iodine_mcg": 0},
            "insights": [],
            "message": f"No valid food items identified. {len(unknown_items)} unknown items."
        }

    # Build prompt
    food_descriptions = [f"- {item.get('quantity', 1)} {item.get('unit', 'serving')} of {item.get('name', '')}" for item in valid_items]
    food_list_text = "\n".join(food_descriptions)

    prompt = f"""Calculate nutritional information for these food items:

{food_list_text}

Return ONLY a valid JSON object with this EXACT structure:

{{
  "items": [
    {{
      "label": "Food Name (quantity unit)",
      "calories": 200,
      "protein_g": 10.5,
      "carbs_g": 30.0,
      "fat_g": 5.0,
      "fibre_g": 3.0,
      "sugar_g": 2.0,
      "calcium_mg": 50.0,
      "magnesium_mg": 25.0,
      "sodium_mg": 300.0,
      "potassium_mg": 200.0,
      "iron_mg": 1.5,
      "iodine_mcg": 0.0
    }}
  ],
  "insights": ["insight 1", "insight 2"]
}}

RULES:
1. Use field name "label" (NOT "name") - include quantity and unit in the label
2. ALL numeric values must be plain numbers (NO units like "g" or "mg")
3. Calculate nutrition for the EXACT quantity and unit provided
4. Use exact field names: protein_g, carbs_g, fat_g, fibre_g, sugar_g, calcium_mg, magnesium_mg, sodium_mg, potassium_mg, iron_mg, iodine_mcg
5. Include ALL items provided - these are pre-validated food items

For insights: Give 1-2 brief health tips about the foods.

Return ONLY valid JSON, no other text."""

    try:
        oai_client = get_openai_client()
        response = await async_openai_call(
            oai_client,
            model=model,
            temperature=0,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )

        raw_content = response.choices[0].message.content
        parsed = _robust_json_parse(raw_content)

        if isinstance(parsed, dict) and "items" in parsed:
            all_items = parsed.get("items", [])
            all_insights = parsed.get("insights", [])
        else:
            all_items = parsed if isinstance(parsed, list) else []
            all_insights = []

        enriched_items = _normalise(all_items)
        final_items = enriched_items + unknown_items

        totals = {
            "calories": int(round(sum(item.get("calories", 0) or 0 for item in enriched_items))),
            "protein_g": int(round(sum(item.get("protein_g", 0) or 0 for item in enriched_items))),
            "carbs_g": int(round(sum(item.get("carbs_g", 0) or 0 for item in enriched_items))),
            "fat_g": int(round(sum(item.get("fat_g", 0) or 0 for item in enriched_items))),
            "fibre_g": int(round(sum(item.get("fibre_g", 0) or 0 for item in enriched_items))),
            "sugar_g": int(round(sum(item.get("sugar_g", 0) or 0 for item in enriched_items))),
        }

        micro_nutrients = {
            "calcium_mg": round(sum(item.get("calcium_mg", 0) or 0 for item in enriched_items), 2),
            "magnesium_mg": round(sum(item.get("magnesium_mg", 0) or 0 for item in enriched_items), 2),
            "sodium_mg": round(sum(item.get("sodium_mg", 0) or 0 for item in enriched_items), 2),
            "potassium_mg": round(sum(item.get("potassium_mg", 0) or 0 for item in enriched_items), 2),
            "iron_mg": round(sum(item.get("iron_mg", 0) or 0 for item in enriched_items), 2),
            "iodine_mcg": round(sum(item.get("iodine_mcg", 0) or 0 for item in enriched_items), 2),
        }

        food_labels = [item.get("label", "Unknown") for item in final_items]

        return {
            "success": True,
            "items": sorted(food_labels),
            "totals": totals,
            "micro_nutrients": micro_nutrients,
            "insights": all_insights[:2] if all_insights else [],
            "message": f"Identified {len(enriched_items)} food items" + (f" ({len(unknown_items)} unknown)" if unknown_items else "")
        }

    except Exception as e:
        logger.error(f"Text food analysis failed for user {user_id}: {e}")
        return {
            "success": False,
            "items": [],
            "totals": {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0, "fibre_g": 0, "sugar_g": 0},
            "micro_nutrients": {"calcium_mg": 0, "magnesium_mg": 0, "sodium_mg": 0, "potassium_mg": 0, "iron_mg": 0, "iodine_mcg": 0},
            "insights": [],
            "message": "Unable to analyze food items. Please try again."
        }
