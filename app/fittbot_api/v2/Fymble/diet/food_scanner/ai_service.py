import asyncio
import base64
import io
import json
import logging
import random
import re
import time
from typing import Dict, List, Optional, Union
from collections import defaultdict
from datetime import datetime as dt

import httpx
from fastapi import HTTPException
from openai import AsyncOpenAI

from app.config.settings import settings
from app.utils.openai_pool import get_openai_client
from app.utils.async_openai import async_openai_call

logger = logging.getLogger(__name__)

# ─── AI MODEL CONFIGURATION ────────────────────────────────────
PRIMARY_MODEL = "gpt-5.1"
SECONDARY_MODEL = "gpt-4o"

# ─── CONNECTION POOLING (persistent HTTP/2 client) ─────────────
http_client = httpx.AsyncClient(
    limits=httpx.Limits(
        max_keepalive_connections=100,
        max_connections=200,
        keepalive_expiry=30.0,
    ),
    timeout=httpx.Timeout(15.0, connect=5.0),
    http2=True,
)

openai_client = AsyncOpenAI(
    api_key=settings.openai_api_key,
    default_headers={"User-Agent": "food-scanner/2.0"},
    timeout=12.0,
    max_retries=2,
    http_client=http_client,
)

# ─── CONCURRENCY CONTROLS ─────────────────────────────────────
OPENAI_CONCURRENCY_LIMIT = 50
OPENAI_QUEUE_TIMEOUT = 5.0
OPENAI_REQUEST_TIMEOUT = 15.0

_openai_semaphore = None

def get_openai_semaphore():
    global _openai_semaphore
    if _openai_semaphore is None:
        _openai_semaphore = asyncio.Semaphore(OPENAI_CONCURRENCY_LIMIT)
    return _openai_semaphore


# ─── CIRCUIT BREAKER ──────────────────────────────────────────
class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, timeout_seconds: int = 60):
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds
        self.failures = defaultdict(int)
        self.last_failure_time = defaultdict(lambda: dt.min)
        self.is_open = defaultdict(bool)

    def record_success(self, service: str):
        self.failures[service] = 0
        self.is_open[service] = False

    def record_failure(self, service: str):
        self.failures[service] += 1
        self.last_failure_time[service] = dt.now()
        if self.failures[service] >= self.failure_threshold:
            self.is_open[service] = True
            logger.warning("Circuit breaker OPEN for %s (%d failures)", service, self.failures[service])

    def can_attempt(self, service: str) -> bool:
        if not self.is_open[service]:
            return True
        time_since_failure = dt.now() - self.last_failure_time[service]
        if time_since_failure.total_seconds() > self.timeout_seconds:
            logger.info("Circuit breaker HALF-OPEN for %s (trying again)", service)
            self.is_open[service] = False
            self.failures[service] = 0
            return True
        return False

circuit_breaker = CircuitBreaker(failure_threshold=5, timeout_seconds=60)


# ─── METRICS TRACKING ─────────────────────────────────────────
class Metrics:
    def __init__(self):
        self.calls = defaultdict(int)
        self.successes = defaultdict(int)
        self.failures = defaultdict(int)
        self.total_latency = defaultdict(float)

    def record_call(self, service: str, success: bool, latency_ms: float):
        self.calls[service] += 1
        if success:
            self.successes[service] += 1
        else:
            self.failures[service] += 1
        self.total_latency[service] += latency_ms

    def get_stats(self):
        stats = {}
        for service in self.calls:
            total = self.calls[service]
            stats[service] = {
                "total_calls": total,
                "success_rate": f"{(self.successes[service]/total*100):.1f}%",
                "avg_latency_ms": f"{(self.total_latency[service]/total):.0f}ms"
            }
        return stats

metrics = Metrics()


# ─── RETRY WITH BACKOFF ───────────────────────────────────────
async def _retry_with_backoff(coro_factory, max_attempts: int = 3):
    """Retry helper that respects Retry-After headers and applies jittered backoff."""
    for attempt in range(1, max_attempts + 1):
        try:
            return await coro_factory()
        except HTTPException:
            raise
        except Exception as exc:
            if attempt == max_attempts:
                raise
            retry_after_header = None
            response = getattr(exc, "response", None)
            if response is not None:
                retry_after_header = response.headers.get("Retry-After")
            if retry_after_header:
                try:
                    wait_seconds = float(retry_after_header)
                except ValueError:
                    wait_seconds = min(8.0, (2 ** (attempt - 1)) + random.random())
            else:
                wait_seconds = min(8.0, (2 ** (attempt - 1)) + random.random())
            logger.info("Backing off %.2fs before retry %d", wait_seconds, attempt + 1)
            await asyncio.sleep(wait_seconds)


# ─── JSON PARSER ───────────────────────────────────────────────
_fence = re.compile(r"^```(?:json)?\s*|\s*```$", re.S | re.I)
_number = r"[-+]?\d*\.?\d+"


def _strip_fence(t: str) -> str:
    return _fence.sub("", t.strip()).strip()


def robust_json_parse(txt: str) -> Union[Dict, List, List[str]]:
    """Parse AI JSON response with fallback for malformed output."""
    txt = _strip_fence(txt)
    try:
        obj = json.loads(txt)
        if isinstance(obj, dict):
            return obj
        if isinstance(obj, list):
            return obj
    except json.JSONDecodeError:
        pass

    out = []
    for blk in re.findall(r"\{[^}]*\}", txt, re.S):
        blk = re.sub(r",\s*}", "}", blk)
        try:
            out.append(json.loads(blk))
            continue
        except json.JSONDecodeError:
            pass
        lab = re.search(r'"label"\s*:\s*"([^"]+)"', blk)
        if lab:
            cal = re.search(r'"calories"\s*:\s*(' + _number + ")", blk)
            out.append({
                "label": lab.group(1),
                "calories": float(cal.group(1)) if cal else None,
            })
    return out or re.findall(r'"label"\s*:\s*"([^"]+)"', txt)


def _normalise(items: list) -> list:
    """Normalize food items returned by AI."""
    # Default micro nutrient values per 200 calories (common food database averages)
    DEFAULT_MICROS = {
        "calcium_mg": 50.0, "magnesium_mg": 40.0, "sodium_mg": 600.0,
        "potassium_mg": 400.0, "iron_mg": 3.0, "iodine_mcg": 0.0
    }

    # Default macro values for items missing them
    DEFAULT_MACROS = {
        "protein_g": 10.0, "carbs_g": 30.0, "fat_g": 8.0,
        "fibre_g": 3.0, "sugar_g": 10.0
    }

    # Zero values for unknown foods
    ZERO_NUTRITION = {
        "calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0,
        "fibre_g": 0, "sugar_g": 0, "calcium_mg": 0, "magnesium_mg": 0,
        "sodium_mg": 0, "potassium_mg": 0, "iron_mg": 0, "iodine_mcg": 0
    }

    norm = []
    for itm in items:
        if isinstance(itm, dict):
            d = {k: itm.get(k) for k in
                 ["label", "calories", "protein_g", "carbs_g",
                  "fat_g", "fibre_g", "sugar_g", "calcium_mg",
                  "magnesium_mg", "sodium_mg", "potassium_mg",
                  "iron_mg", "iodine_mcg"]}
            label = d.get("label") or itm.get("name") or "Unknown"
            d["label"] = label

            # If food is Unknown, use zero values only
            if "unknown" in label.lower():
                d.update(ZERO_NUTRITION)
                norm.append(d)
                continue

            # Ensure calories has a value
            calories = d.get("calories") or 150

            # Fill in missing macro nutrients ONLY if AI didn't provide them (None, not 0)
            for macro, default_val in DEFAULT_MACROS.items():
                if d.get(macro) is None:
                    # Scale macro by calories ratio
                    ratio = calories / 150.0  # 150 is our baseline
                    d[macro] = round(default_val * ratio, 2)

            # Fill in missing micro nutrients ONLY if AI didn't provide them (None, not 0)
            ratio = calories / 200.0  # normalize to 200 calorie serving
            for micro, default_val in DEFAULT_MICROS.items():
                if d.get(micro) is None:
                    d[micro] = round(default_val * ratio, 2)

            norm.append(d)
        else:
            # For string items (not AI responses), check if unknown
            label = str(itm)
            if "unknown" in label.lower():
                norm.append({"label": label, **ZERO_NUTRITION})
            else:
                norm.append({
                    "label": label, "calories": 150,
                    **DEFAULT_MACROS,
                    **DEFAULT_MICROS
                })

    # Empty list fallback - use zero values for unknown
    if not norm:
        return [{"label": "Unknown", **ZERO_NUTRITION}]

    return norm


# ─── OPENAI IMAGE ANALYSIS ─────────────────────────────────────
async def _ask_openai(model: str, image_bytes: bytes, content_type: str) -> dict:
    """Query OpenAI Vision API for food analysis with semaphore, circuit breaker, retry, and metrics."""
    service_name = f"openai-{model}"

    # Circuit breaker check
    if not circuit_breaker.can_attempt(service_name):
        logger.warning("%s circuit breaker is OPEN, skipping", service_name)
        return {"items": [], "insights": []}

    # Semaphore - fail fast if queue is full
    semaphore_acquired = False
    try:
        await asyncio.wait_for(get_openai_semaphore().acquire(), timeout=OPENAI_QUEUE_TIMEOUT)
        semaphore_acquired = True
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="OpenAI queue full, please retry")

    start_time = time.time()
    try:
        uri = f"data:{content_type};base64,{base64.b64encode(image_bytes).decode()}"

        prompt = """Analyze food in this image. Return JSON:

{
  "primary_name": "Display name",
  "items": [{"label": "Food", "calories": 200, "protein_g": 10, "carbs_g": 30, "fat_g": 5}],
  "insights": ["tip"]
}

PRIMARY_NAME: 1=item, 2-3=combine (e.g. "Pizza with coffee"), 4+=same type or "Thali"
LABELS: Food name only, no quantities/sizes
NUTRITION: protein, carbs, fat per item. 0 if none.
NO DUPLICATES: Each food item must be unique. If plate has 3 idlys, return ONE entry "Idly" with combined/single nutrition. Never list the same food twice in items array.

JSON only"""

        client = get_openai_client()

        # GPT-5.1+ requires max_completion_tokens instead of max_tokens
        tokens_param = "max_completion_tokens" if "gpt-5" in model.lower() else "max_tokens"

        # Reduced token limits for faster response
        max_tokens = 2000 if "gpt-5" in model.lower() else 1500

        async def invoke_openai():
            return await asyncio.wait_for(
                async_openai_call(
                    client,
                    model=model,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": uri, "detail": "auto"}},
                            {"type": "text", "text": prompt},
                        ],
                    }],
                    temperature=1 if "gpt-5" in model.lower() else 0,
                    **{tokens_param: max_tokens},
                ),
                timeout=OPENAI_REQUEST_TIMEOUT,
            )

        response = await _retry_with_backoff(invoke_openai)

        if hasattr(response, 'choices') and response.choices and response.choices[0].message:
            raw_content = response.choices[0].message.content
        else:
            raw_content = ""

        parsed = robust_json_parse(raw_content)

        # If empty result, try once more with simpler prompt
        if not parsed or (isinstance(parsed, list) and len(parsed) == 0) or (isinstance(parsed, dict) and not parsed.get("items")):

            # Simpler prompt that still gets basic nutrition and primary_name
            simple_prompt = """List all foods in this image as JSON:
{
  "primary_name": "Name for this meal combination",
  "items": [
    {"name": "food1", "calories": 100, "protein_g": 5, "carbs_g": 10, "fat_g": 3},
    {"name": "food2", "calories": 200, "protein_g": 8, "carbs_g": 25, "fat_g": 7}
  ]
}
For primary_name: if single food, use its name. If 2-3 foods, combine with "with". If 4+ foods form a known meal type, use that (e.g., "Thali", "Full Meals").
NO DUPLICATES: Each food must be unique. If 3 idlys on plate, return ONE entry "Idly". Never list same food twice.
JSON only:"""

            max_tokens = 1500 if "gpt-5" in model.lower() else 1000

            async def invoke_openai_simple():
                return await asyncio.wait_for(
                    async_openai_call(
                        client,
                        model=model,
                        messages=[{
                            "role": "user",
                            "content": [
                                {"type": "image_url", "image_url": {"url": uri, "detail": "auto"}},
                                {"type": "text", "text": simple_prompt},
                            ],
                        }],
                        temperature=1 if "gpt-5" in model.lower() else 0,
                        **{tokens_param: max_tokens},
                    ),
                    timeout=OPENAI_REQUEST_TIMEOUT,
                )

            response2 = await _retry_with_backoff(invoke_openai_simple)

            raw_content2 = response2.choices[0].message.content

            # Parse the simpler format and convert to our format
            parsed2 = robust_json_parse(raw_content2)
            if parsed2:
                # Handle both list and dict formats
                if isinstance(parsed2, dict) and "items" in parsed2:
                    items_list = parsed2["items"]
                    primary = parsed2.get("primary_name", "")
                elif isinstance(parsed2, list):
                    items_list = parsed2
                    primary = ""
                else:
                    items_list = []
                    primary = ""

                formatted_items = []
                for item in items_list if isinstance(items_list, list) else []:
                    if isinstance(item, dict):
                        formatted_items.append({
                            "label": item.get("name", "Unknown"),
                            "calories": item.get("calories", 0),
                            "protein_g": item.get("protein_g", 0),
                            "carbs_g": item.get("carbs_g", 0),
                            "fat_g": item.get("fat_g", 0),
                            "fibre_g": item.get("fibre_g", 0),
                            "sugar_g": item.get("sugar_g", 0),
                            "calcium_mg": item.get("calcium_mg", 0),
                            "magnesium_mg": item.get("magnesium_mg", 0),
                            "sodium_mg": item.get("sodium_mg", 0),
                            "potassium_mg": item.get("potassium_mg", 0),
                            "iron_mg": item.get("iron_mg", 0),
                            "iodine_mcg": item.get("iodine_mcg", 0),
                        })

                latency_ms = (time.time() - start_time) * 1000
                circuit_breaker.record_success(service_name)
                metrics.record_call(service_name, success=True, latency_ms=latency_ms)

                result = {"items": formatted_items, "insights": []}
                if primary:
                    result["primary_name"] = primary
                return result

        latency_ms = (time.time() - start_time) * 1000
        circuit_breaker.record_success(service_name)
        metrics.record_call(service_name, success=True, latency_ms=latency_ms)

        if isinstance(parsed, dict) and "items" in parsed:
            return parsed
        return {"items": parsed if isinstance(parsed, list) else [], "insights": []}

    except asyncio.TimeoutError:
        latency_ms = (time.time() - start_time) * 1000
        circuit_breaker.record_failure(service_name)
        metrics.record_call(service_name, success=False, latency_ms=latency_ms)
        logger.warning("%s timeout", service_name)
        raise
    except HTTPException:
        raise
    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000
        circuit_breaker.record_failure(service_name)
        metrics.record_call(service_name, success=False, latency_ms=latency_ms)
        logger.warning("OpenAI %s error: %s", model, e)
        raise
    finally:
        if semaphore_acquired:
            get_openai_semaphore().release()


# ─── MAIN AI QUERY WITH FALLBACK ───────────────────────────────────
async def analyze_image(image_bytes: bytes, content_type: str, brief: bool = False) -> dict:
    """
    Analyze food image using AI with fallback logic.

    Tries: OpenAI Primary → OpenAI Secondary

    Returns:
        dict with items, insights, primary_name, and model_used
    """
    # Try primary OpenAI model
    try:
        logger.info("Trying %s", PRIMARY_MODEL)
        result = await _ask_openai(PRIMARY_MODEL, image_bytes, content_type)
        result["model_used"] = PRIMARY_MODEL
        logger.info("%s returned result", PRIMARY_MODEL)
        return result
    except asyncio.TimeoutError:
        logger.warning("%s timeout, trying fallback", PRIMARY_MODEL)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("%s error: %s, trying fallback", PRIMARY_MODEL, e)

    # Try secondary OpenAI model
    try:
        logger.info("Trying fallback: %s", SECONDARY_MODEL)
        result = await _ask_openai(SECONDARY_MODEL, image_bytes, content_type)
        result["model_used"] = SECONDARY_MODEL
        logger.info("%s returned result", SECONDARY_MODEL)
        return result
    except asyncio.TimeoutError:
        logger.warning("%s timeout", SECONDARY_MODEL)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("%s failed: %s", SECONDARY_MODEL, e)

    logger.warning("All AI models exhausted")
    return {"items": [], "insights": [], "model_used": "Failed"}


# ─── TEXT ANALYSIS ───────────────────────────────────────────────
async def analyze_text(food_items: List[Dict], model: str = PRIMARY_MODEL) -> dict:
    try:
        food_descriptions = [
            f"- {item.get('quantity', 1)} {item.get('unit', 'serving')} of {item.get('name', '')}"
            for item in food_items
        ]
        food_list_text = "\n".join(food_descriptions)

        prompt = f"""You are a strict food nutrition validator and calculator.

Given these items:

{food_list_text}

VALIDATION (do this FIRST for every item):
- ONLY accept items that are REAL, EDIBLE food or drink items.
- You must know Indian foods thoroughly: all regional cuisines (Tamil, Kerala, Andhra, Karnataka, North Indian, etc.), street food, traditional dishes, sweets, snacks, fruits, vegetables, grains, spices used as food.
- REJECT with is_food: false if the item is:
  * Profanity or slang or abuse in ANY language (especially Tamil, Hindi, Telugu, Kannada, Malayalam, English)
  * Harmful/toxic substances (poison, chemicals, fuel, cleaning products, drugs)
  * Random gibberish or nonsense words
  * Any non-food item (objects, people, places, brands that aren't food)
  * Anything you are not confident is a real food
- If you are UNSURE whether something is food, reject it. Do NOT guess nutrition for unknown items.

For VALID food items only, calculate accurate nutrition.

Return ONLY valid JSON:

{{
  "items": [
    {{
      "label": "Food Name (quantity unit)",
      "is_food": true,
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
    }},
    {{
      "label": "rejected item",
      "is_food": false,
      "reason": "profanity | harmful | not_food | gibberish"
    }}
  ],
  "insights": ["insight 1", "insight 2"]
}}

RULES:
1. Use field name "label" (NOT "name") - include quantity and unit in the label for valid foods
2. ALL numeric values must be plain numbers (NO units like "g" or "mg")
3. Calculate nutrition for the EXACT quantity and unit provided
4. Use exact field names: protein_g, carbs_g, fat_g, fibre_g, sugar_g, calcium_mg, magnesium_mg, sodium_mg, potassium_mg, iron_mg, iodine_mcg
5. NEVER return nutrition for non-food items. NEVER return 0-calorie unknown items. Either it is real food with real nutrition, or it is rejected with is_food: false.
6. When in doubt, REJECT. It is better to reject a valid food than to accept a bad word or non-food item.

Return ONLY valid JSON, no other text."""

        client = get_openai_client()

        # GPT-5.1+ requires max_completion_tokens instead of max_tokens
        tokens_param = "max_completion_tokens" if "gpt-5" in model.lower() else "max_tokens"

        response = await async_openai_call(
            client,
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=1 if "gpt-5" in model.lower() else 0,
            **{tokens_param: 1500},
        )

        raw_content = response.choices[0].message.content
        parsed = robust_json_parse(raw_content)

        if isinstance(parsed, dict) and "items" in parsed:
            all_items = parsed.get("items", [])
            all_insights = parsed.get("insights", [])
        else:
            all_items = parsed if isinstance(parsed, list) else []
            all_insights = []

        # Separate valid food items from rejected non-food items
        valid_items = []
        rejected_items = []
        for item in all_items:
            is_food = item.get("is_food")
            # Catch all falsy variations: False, "false", "False", 0, etc.
            is_rejected = (is_food is False or str(is_food).lower() == "false")
            # Also reject items with 0 or missing calories (AI returned unknown/empty nutrition)
            has_no_nutrition = (not item.get("calories") and not item.get("protein_g"))

            if is_rejected or has_no_nutrition:
                rejected_items.append({
                    "name": item.get("label", "Unknown"),
                    "reason": item.get("reason", "not_food"),
                })
                logger.info("[FoodScanner] Rejected non-food item: %s (%s)", item.get("label"), item.get("reason"))
            else:
                valid_items.append(item)

        enriched_items = _normalise(valid_items)

        # Calculate totals from valid food items only
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

        # Return per-item nutrition dicts (valid food items only)
        items_with_nutrition = []
        for idx, item in enumerate(enriched_items, start=1):
            items_with_nutrition.append({
                "id": idx,
                "name": item.get("label", "Unknown"),
                "calories": int(round(item.get("calories", 0) or 0)),
                "protein_g": int(round(item.get("protein_g", 0) or 0)),
                "carbs_g": int(round(item.get("carbs_g", 0) or 0)),
                "fat_g": int(round(item.get("fat_g", 0) or 0)),
                "fibre_g": int(round(item.get("fibre_g", 0) or 0)),
                "sugar_g": int(round(item.get("sugar_g", 0) or 0)),
            })

        message = None
        if rejected_items:
            rejected_names = [r["name"] for r in rejected_items]
            message = f"Rejected non-food items: {', '.join(rejected_names)}"

        return {
            "success": True,
            "items": items_with_nutrition,
            "totals": totals,
            "micro_nutrients": micro_nutrients,
            "rejected_items": rejected_items,
            "message": message,
            "insights": all_insights[:2],
        }

    except Exception:
        return {
            "success": False,
            "items": [],
            "totals": {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0, "fibre_g": 0, "sugar_g": 0},
            "micro_nutrients": {"calcium_mg": 0, "magnesium_mg": 0, "sodium_mg": 0, "potassium_mg": 0, "iron_mg": 0, "iodine_mcg": 0},
            "insights": [],
        }