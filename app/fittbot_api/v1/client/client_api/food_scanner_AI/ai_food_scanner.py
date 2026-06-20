#food_scan
import base64
import io
import json
import logging
import re
import asyncio
from typing import Optional, List, Union, Tuple, Dict, Any
from datetime import datetime, date
from fastapi import APIRouter, File, UploadFile, HTTPException, Depends, Form
from fastapi.responses import JSONResponse
from PIL import Image
from app.utils.ai_sanitizer import sanitize_food_name, validate_image_magic_bytes, FOOD_DETECTION_PROMPT_ADDITION, MAX_IMAGES_PER_REQUEST

# Celery task import for image processing
from app.tasks.image_scanner_tasks import analyze_food_image
Image.MAX_IMAGE_PIXELS = None  # Safe: uploads already capped and aggressively resized
from pydantic import BaseModel, Field
from openai import AsyncOpenAI
import google.generativeai as genai
from sqlalchemy.orm import Session
import pytz
import random
import time

from app.config.settings import settings
from app.models.database import get_db
from app.utils.redis_config import get_redis
from redis.asyncio import Redis
from app.models.fittbot_models import (
    ActualDiet,
    ClientTarget,
    CalorieEvent,
    LeaderboardDaily,
    LeaderboardMonthly,
    LeaderboardOverall,
)


logger = logging.getLogger(__name__)

# ─── CONFIG ────────────────────────────────────────────────
# OpenAI Models (in fallback order)
PRIMARY_MODEL   = "gpt-4o-mini"      # Fast, cheap - try first
SECONDARY_MODEL = "gpt-4o"           # Better quality fallback
TERTIARY_MODEL  = "gpt-4-turbo"      # Turbo model (faster GPT-4)
QUATERNARY_MODEL = "gpt-4"           # Original GPT-4 (expensive)
GEMINI_MODEL = "gemini-2.5-flash"    # Final fallback (free tier)
MAX_FILE_MB     = 1
TARGET_SIZE     = 150 * 1024  # Reduced to 150KB for even faster upload
MAX_DIMENSION   = 512  # Even smaller for speed
SUPPORTED_FORMATS = {"image/jpeg", "image/jpg", "image/png", "image/avif", "image/webp", "application/octet-stream"}
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".avif", ".webp"}

# Timezone
TZNAME = "Asia/Kolkata"
IST = pytz.timezone(TZNAME)

# OpenAI client with production-grade optimizations and connection pooling
import httpx
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from datetime import datetime as dt

# Create persistent HTTP client with connection pooling for high concurrency
http_client = httpx.AsyncClient(
    limits=httpx.Limits(
        max_keepalive_connections=100,  # Keep 100 connections alive
        max_connections=200,  # Allow 200 total connections
        keepalive_expiry=30.0,  # Keep connections alive for 30s
    ),
    timeout=httpx.Timeout(15.0, connect=5.0),  # 15s total, 5s connect
    http2=True,  # Enable HTTP/2 for better performance
)

openai_client = AsyncOpenAI(
    api_key=settings.openai_api_key,
    default_headers={"User-Agent": "food-scanner/1.0"},
    timeout=12.0,  # Increased for reliability
    max_retries=2,  # Enable retries for production
    http_client=http_client,  # Use persistent client
)

# Synchronous OpenAI client for Celery/gevent workers
from openai import OpenAI
openai_client_sync = OpenAI(
    api_key=settings.openai_api_key,
    default_headers={"User-Agent": "food-scanner/1.0"},
    timeout=15.0,
    max_retries=2,
)

# Gemini client configuration
genai.configure(api_key=settings.gemini_api_key)
gemini_model = genai.GenerativeModel(GEMINI_MODEL)

# ─── CONCURRENCY CONTROLS FOR 100K+ USERS ─────────────────────
# Limits tuned from latest load testing so they live together for quick tweaks.
OPENAI_CONCURRENCY_LIMIT = 50
GEMINI_CONCURRENCY_LIMIT = 30
CPU_POOL_SIZE = 20

# Fail fast when backlogs grow instead of letting requests hang forever.
OPENAI_QUEUE_TIMEOUT = 5.0      # seconds
GEMINI_QUEUE_TIMEOUT = 5.0      # seconds
CPU_QUEUE_TIMEOUT = 5.0         # seconds
OPENAI_REQUEST_TIMEOUT = 15.0   # seconds per upstream request
GEMINI_REQUEST_TIMEOUT = 15.0   # seconds per upstream request

# Semaphores: Limit concurrent API calls to prevent rate limit exhaustion
# NOTE: Lazy initialization to avoid "no current event loop" error in Celery workers (Python 3.9)
_openai_semaphore = None
_gemini_semaphore = None
_cpu_semaphore = None

def get_openai_semaphore():
    global _openai_semaphore
    if _openai_semaphore is None:
        _openai_semaphore = asyncio.Semaphore(OPENAI_CONCURRENCY_LIMIT)
    return _openai_semaphore

def get_gemini_semaphore():
    global _gemini_semaphore
    if _gemini_semaphore is None:
        _gemini_semaphore = asyncio.Semaphore(GEMINI_CONCURRENCY_LIMIT)
    return _gemini_semaphore

def get_cpu_semaphore():
    global _cpu_semaphore
    if _cpu_semaphore is None:
        _cpu_semaphore = asyncio.Semaphore(CPU_POOL_SIZE)
    return _cpu_semaphore

# Bounded thread pool: Prevent CPU work from blocking the loop
cpu_executor = ThreadPoolExecutor(max_workers=CPU_POOL_SIZE, thread_name_prefix="food-scanner-cpu")

# Circuit breaker: Track failures and auto-disable failing services
class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, timeout_seconds: int = 60):
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds
        self.failures = defaultdict(int)
        self.last_failure_time = defaultdict(lambda: dt.min)
        self.is_open = defaultdict(bool)

    def record_success(self, service: str):
        """Reset failure count on success"""
        self.failures[service] = 0
        self.is_open[service] = False

    def record_failure(self, service: str):
        """Increment failure count and open circuit if threshold reached"""
        self.failures[service] += 1
        self.last_failure_time[service] = dt.now()

        if self.failures[service] >= self.failure_threshold:
            self.is_open[service] = True
            logger.warning("Circuit breaker OPEN for %s (%d failures)", service, self.failures[service])

    def can_attempt(self, service: str) -> bool:
        """Check if we should attempt calling this service"""
        if not self.is_open[service]:
            return True

        # Auto-reset after timeout
        time_since_failure = dt.now() - self.last_failure_time[service]
        if time_since_failure.total_seconds() > self.timeout_seconds:
            logger.info("Circuit breaker HALF-OPEN for %s (trying again)", service)
            self.is_open[service] = False
            self.failures[service] = 0
            return True

        return False

circuit_breaker = CircuitBreaker(failure_threshold=5, timeout_seconds=60)

# Metrics: Track API performance
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

router = APIRouter(prefix="/food_scanner", tags=["food_scanner"])

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

            wait_seconds: float
            if retry_after_header:
                try:
                    wait_seconds = float(retry_after_header)
                except ValueError:
                    wait_seconds = min(8.0, (2 ** (attempt - 1)) + random.random())
            else:
                wait_seconds = min(8.0, (2 ** (attempt - 1)) + random.random())

            logger.info("Backing off %.2fs before retry %d", wait_seconds, attempt + 1)
            await asyncio.sleep(wait_seconds)

# Cleanup on shutdown
@router.on_event("shutdown")
async def shutdown_event():
    """Close HTTP client connection pool and executor on shutdown"""
    await http_client.aclose()
    cpu_executor.shutdown(wait=True)
    logger.info("HTTP client closed")
    logger.info("CPU executor shutdown")
    logger.info("Final metrics: %s", metrics.get_stats())

# ─── ULTRA-FAST IMAGE COMPRESSION ──────────────────────────
def _compress(raw: bytes, original_format: str = "JPEG") -> Tuple[bytes, str]:
    """Ultra-fast compression optimized for speed """
    if len(raw) <= TARGET_SIZE:
        return raw, f"image/{original_format.lower()}"

    with Image.open(io.BytesIO(raw)) as im:
        # Optimization 1: Use thumbnail() - faster than resize()
        # thumbnail() modifies in-place and uses LANCZOS by default, but we can override
        if max(im.width, im.height) > MAX_DIMENSION:
            # Calculate size maintaining aspect ratio
            if im.width > im.height:
                new_size = (MAX_DIMENSION, int(im.height * MAX_DIMENSION / im.width))
            else:
                new_size = (int(im.width * MAX_DIMENSION / im.height), MAX_DIMENSION)

            # Optimization 2: Use NEAREST for 3-4x faster resize (acceptable for AI)
            # NEAREST is fastest, BILINEAR is medium, LANCZOS is slowest
            im.thumbnail(new_size, Image.NEAREST)  # Changed from BILINEAR for speed

        # Optimization 3: Only convert if needed (skip unnecessary conversions)
        if im.mode not in ("RGB", "L"):  # L = grayscale, also works for JPEG
            if im.mode == "RGBA":
                # Optimization 4: Fast RGBA -> RGB conversion with white background
                background = Image.new("RGB", im.size, (255, 255, 255))
                background.paste(im, mask=im.split()[3] if im.mode == "RGBA" else None)
                im = background
            else:
                im = im.convert("RGB")
        elif im.mode == "L":
            # Keep grayscale, JPEG supports it
            pass

        # Optimization 5: Aggressive JPEG compression for maximum speed
        buf = io.BytesIO()
        im.save(
            buf,
            format="JPEG",
            quality=55,  # Reduced from 60 (AI doesn't need high quality)
            optimize=False,  # Skip optimization pass (saves time)
            progressive=False,  # Disable progressive encoding (faster)
            subsampling=2  # 4:2:0 chroma subsampling (fastest, smallest)
        )
        return buf.getvalue(), "image/jpeg"

async def _compress_async(raw: bytes, original_format: str = "JPEG") -> Tuple[bytes, str]:
    """Async wrapper for compression - runs in bounded thread pool"""
    loop = asyncio.get_event_loop()

    try:
        await asyncio.wait_for(get_cpu_semaphore().acquire(), timeout=CPU_QUEUE_TIMEOUT)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="Image processing is busy, please retry")

    try:
        return await loop.run_in_executor(cpu_executor, _compress, raw, original_format)
    finally:
        get_cpu_semaphore().release()

def _get_image_format(content_type: str, filename: str = "") -> str:
    if content_type and content_type != "application/octet-stream":
        if "jpeg" in content_type or "jpg" in content_type:
            return "JPEG"
        elif "png" in content_type:
            return "PNG"
        elif "avif" in content_type:
            return "AVIF"
        elif "webp" in content_type:
            return "WEBP"
    if filename:
        ext = filename.lower().split('.')[-1]
        if ext in ['jpg', 'jpeg']:
            return "JPEG"
        elif ext == 'png':
            return "PNG"
        elif ext == 'avif':
            return "AVIF"
        elif ext == 'webp':
            return "WEBP"
    return "JPEG"

def _is_supported_image(content_type: str, filename: str = "") -> bool:
    if content_type in {"image/jpeg", "image/jpg", "image/png", "image/avif", "image/webp"}:
        return True
    if content_type == "application/octet-stream" and filename:
        ext = f".{filename.lower().split('.')[-1]}" if '.' in filename else ""
        return ext in SUPPORTED_EXTENSIONS
    return False

def _num(x: Optional[float]) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0

# ─── OPTIMIZED JSON PARSER ─────────────────────────────────
_fence = re.compile(r"^```(?:json)?\s*|\s*```$", re.S | re.I)
_number = r"[-+]?\d*\.?\d+"

def _strip_fence(t: str) -> str:
    return _fence.sub("", t.strip()).strip()

def _robust_json_parse(txt: str) -> Union[Dict[str, object], List[Dict[str, object]], List[str]]:
    txt = _strip_fence(txt)
    try:
        obj = json.loads(txt)
        # Return dict directly if it has the expected structure
        if isinstance(obj, dict):
            return obj
        if isinstance(obj, list):
            return obj
    except json.JSONDecodeError:
        pass
    out: list[dict[str, object]] = []
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
            out.append(
                {
                    "label": lab.group(1),
                    "calories": float(cal.group(1)) if cal else None,
                }
            )
    return out or re.findall(r'"label"\s*:\s*"([^"]+)"', txt)

# ─── ASYNC GEMINI MODEL QUERY ──────────────────────────────
async def _ask_gemini_async(image_bytes: bytes, brief: bool = False) -> dict:
    """Async wrapper for Gemini API calls with semaphore and circuit breaker"""
    # Check circuit breaker
    if not circuit_breaker.can_attempt("gemini"):
        logger.warning("Gemini circuit breaker is OPEN, skipping")
        return {"items": [], "insights": []}

    semaphore_acquired = False
    try:
        await asyncio.wait_for(get_gemini_semaphore().acquire(), timeout=GEMINI_QUEUE_TIMEOUT)
        semaphore_acquired = True
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="Gemini queue full, please retry")

    start_time = time.time()
    try:
        cpu_acquired = False
        try:
            await asyncio.wait_for(get_cpu_semaphore().acquire(), timeout=CPU_QUEUE_TIMEOUT)
            cpu_acquired = True
        except asyncio.TimeoutError:
            raise HTTPException(status_code=503, detail="Image processing queue full, please retry")

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(cpu_executor, _ask_gemini_sync, image_bytes, brief)
        finally:
            if cpu_acquired:
                get_cpu_semaphore().release()

        latency_ms = (time.time() - start_time) * 1000
        circuit_breaker.record_success("gemini")
        metrics.record_call("gemini", success=True, latency_ms=latency_ms)

        if not result:
            logger.warning("Gemini returned no items (treated as success)")

        return result
    except HTTPException as exc:
        raise exc
    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000
        circuit_breaker.record_failure("gemini")
        metrics.record_call("gemini", success=False, latency_ms=latency_ms)
        logger.warning("Gemini error: %s", e)
        return {"items": [], "insights": []}
    finally:
        if semaphore_acquired:
            get_gemini_semaphore().release()

def _ask_gemini_sync(image_bytes: bytes, brief: bool = False) -> dict:
    """Synchronous Gemini query with improved prompt"""
    try:
        prompt = """Analyze this food image and return ONLY a valid JSON object with this EXACT structure:

{
  "items": [
    {
      "label": "Food Name",
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
    }
  ],
  "insights": ["insight 1", "insight 2"]
}

CRITICAL RULES:
1. Use field name "label" (NOT "name")
2. ALL fields must be at the same level (NO nested objects)
3. ALL numeric values must be plain numbers (NO units like "g" or "mg")
4. Use exact field names: protein_g, carbs_g, fat_g, fibre_g, sugar_g, calcium_mg, magnesium_mg, sodium_mg, potassium_mg, iron_mg, iodine_mcg

For insights:
- HEALTHY foods: Give positive health benefits
- JUNK foods: Acknowledge treat and suggest balancing with healthy foods
- Return empty [] only for "Unknown" or non-food items

Return ONLY valid JSON, no other text."""

        image = Image.open(io.BytesIO(image_bytes))
        response = gemini_model.generate_content([prompt, image])

        if response.text:
            logger.debug("Gemini raw response: %s", response.text[:500])
            parsed = _robust_json_parse(response.text)
            # If AI returns object with items and insights, return it
            if isinstance(parsed, dict) and "items" in parsed:
                logger.debug("Gemini parsed insights: %s", parsed.get('insights', []))
                return parsed
            # Fallback: if AI returns just array, wrap it
            logger.debug("Gemini returned array format, wrapping with empty insights")
            return {"items": parsed if isinstance(parsed, list) else [], "insights": []}
        else:
            logger.debug("Gemini returned no text")
            return {"items": [], "insights": []}

    except Exception as e:
        logger.warning("Gemini API error: %s", e)
        return {"items": [], "insights": []}

# ─── OPENAI MODEL QUERY ────────────────────────────────────
async def _ask_openai(model: str, uri: str, brief: bool = False) -> dict:
    """Query OpenAI with clear, structured prompt, semaphore, and circuit breaker"""
    service_name = f"openai-{model}"

    # Check circuit breaker
    if not circuit_breaker.can_attempt(service_name):
        logger.warning("%s circuit breaker is OPEN, skipping", service_name)
        return {"items": [], "insights": []}

    semaphore_acquired = False
    try:
        await asyncio.wait_for(get_openai_semaphore().acquire(), timeout=OPENAI_QUEUE_TIMEOUT)
        semaphore_acquired = True
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="OpenAI queue full, please retry")

    start_time = time.time()
    try:
        prompt = """Analyze this food image and return ONLY a valid JSON object with this EXACT structure:

{
  "items": [
    {
      "label": "Food Name",
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
    }
  ],
  "insights": ["insight 1", "insight 2"]
}

CRITICAL RULES:
1. Use field name "label" (NOT "name")
2. ALL fields must be at the same level (NO nested objects)
3. ALL numeric values must be plain numbers (NO units like "g" or "mg")
4. Use exact field names: protein_g, carbs_g, fat_g, fibre_g, sugar_g, calcium_mg, magnesium_mg, sodium_mg, potassium_mg, iron_mg, iodine_mcg

For insights:
- HEALTHY foods: Give positive health benefits
- JUNK foods: Acknowledge treat and suggest balancing with healthy foods
- Return empty [] only for "Unknown" or non-food items

Return ONLY valid JSON, no other text."""

        async def invoke_openai():
            return await asyncio.wait_for(
                openai_client.chat.completions.create(
                    model=model,
                    temperature=0,
                    max_tokens=800,  # Increased to ensure room for insights
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "image_url", "image_url": {"url": uri, "detail": "low"}},
                                {"type": "text", "text": prompt},
                            ],
                        }
                    ],
                ),
                timeout=OPENAI_REQUEST_TIMEOUT,
            )

        rsp = await _retry_with_backoff(invoke_openai)

        raw_content = rsp.choices[0].message.content
        logger.debug("%s raw response (first 800 chars): %s", service_name, raw_content[:800] if raw_content else 'NONE')
        logger.debug("%s raw response length: %d", service_name, len(raw_content) if raw_content else 0)

        parsed = _robust_json_parse(raw_content)
        logger.debug("%s parsed type: %s, value: %s", service_name, type(parsed), parsed)
        latency_ms = (time.time() - start_time) * 1000

        circuit_breaker.record_success(service_name)
        metrics.record_call(service_name, success=True, latency_ms=latency_ms)

        # If AI returns object with items and insights, return it
        if isinstance(parsed, dict) and "items" in parsed:
            logger.debug("%s parsed insights: %s", service_name, parsed.get('insights', []))
            return parsed
        # Fallback: if AI returns just array, wrap it
        logger.debug("%s returned array format, wrapping with empty insights", service_name)
        result = {"items": parsed if isinstance(parsed, list) else [], "insights": []}

        if not result["items"]:
            logger.warning("%s returned no items (treated as success)", service_name)

        return result
    except asyncio.TimeoutError as e:
        latency_ms = (time.time() - start_time) * 1000
        circuit_breaker.record_failure(service_name)
        metrics.record_call(service_name, success=False, latency_ms=latency_ms)
        logger.warning("%s timeout", service_name)
        raise e
    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000
        circuit_breaker.record_failure(service_name)
        metrics.record_call(service_name, success=False, latency_ms=latency_ms)
        logger.warning("OpenAI %s error: %s", model, e)
        return {"items": [], "insights": []}
    finally:
        if semaphore_acquired:
            get_openai_semaphore().release()

# ─── SMART FALLBACK WITH CIRCUIT BREAKERS ─────────────────
async def _ask(image_bytes: bytes, content_type: str, brief: bool = False) -> dict:
    """Try OpenAI first, fallback only on actual errors (not on valid Unknown responses)"""

    uri = f"data:{content_type};base64,{base64.b64encode(image_bytes).decode()}"
    logger.debug("Image size: %d bytes, content_type: %s, URI length: %d", len(image_bytes), content_type, len(uri))

    # Try primary OpenAI model - accept any result (even Unknown), only retry on errors
    try:
        logger.info("Trying %s", PRIMARY_MODEL)
        result = await _ask_openai(PRIMARY_MODEL, uri, brief)
        # Accept any result from first model (including empty/Unknown)
        logger.info("%s returned result (accepting even if Unknown)", PRIMARY_MODEL)
        return result
    except asyncio.TimeoutError:
        logger.warning("%s timeout, trying fallback", PRIMARY_MODEL)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("%s error: %s, trying fallback", PRIMARY_MODEL, e)

    # Fallback to secondary OpenAI model (only if primary had error)
    try:
        logger.info("Trying fallback: %s", SECONDARY_MODEL)
        result = await _ask_openai(SECONDARY_MODEL, uri, brief)
        logger.info("%s returned result", SECONDARY_MODEL)
        return result
    except asyncio.TimeoutError:
        logger.warning("%s timeout, trying last resort", SECONDARY_MODEL)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("%s failed: %s, trying last resort", SECONDARY_MODEL, e)

    # Last resort: Gemini (only if both OpenAI models had errors)
    try:
        logger.info("Trying last resort: Gemini")
        result = await _ask_gemini_async(image_bytes, brief)
        logger.info("Gemini returned result")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Gemini failed: %s", e)

    logger.warning("All AI models exhausted")
    return {"items": [], "insights": []}


def _ask_openai_sync(model: str, uri: str, brief: bool = False) -> dict:
    """Fully synchronous OpenAI Vision call - works with gevent"""
    service_name = f"openai-{model}"

    if not circuit_breaker.can_attempt(service_name):
        logger.warning("%s circuit breaker is OPEN, skipping", service_name)
        return {"items": [], "insights": []}

    start_time = time.time()
    try:
        prompt = """Analyze this food image and return ONLY a valid JSON object with this EXACT structure:

{
  "items": [
    {
      "label": "Food Name",
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
    }
  ],
  "insights": ["insight 1", "insight 2"]
}

CRITICAL RULES:
1. Use field name "label" (NOT "name")
2. ALL fields must be at the same level (NO nested objects)
3. ALL numeric values must be plain numbers (NO units like "g" or "mg")
4. Use exact field names: protein_g, carbs_g, fat_g, fibre_g, sugar_g, calcium_mg, magnesium_mg, sodium_mg, potassium_mg, iron_mg, iodine_mcg

For insights:
- HEALTHY foods: Give positive health benefits
- JUNK foods: Acknowledge treat and suggest balancing with healthy foods
- Return empty [] only for "Unknown" or non-food items

Return ONLY valid JSON, no other text."""

        rsp = openai_client_sync.chat.completions.create(
            model=model,
            temperature=0,
            max_tokens=800,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": uri, "detail": "low"}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )

        raw_content = rsp.choices[0].message.content
        logger.debug("%s sync raw response (first 800 chars): %s", service_name, raw_content[:800] if raw_content else 'NONE')

        parsed = _robust_json_parse(raw_content)
        latency_ms = (time.time() - start_time) * 1000

        circuit_breaker.record_success(service_name)
        metrics.record_call(service_name, success=True, latency_ms=latency_ms)

        if isinstance(parsed, dict) and "items" in parsed:
            return parsed
        return {"items": parsed if isinstance(parsed, list) else [], "insights": []}

    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000
        circuit_breaker.record_failure(service_name)
        metrics.record_call(service_name, success=False, latency_ms=latency_ms)
        logger.warning("OpenAI sync %s error: %s", model, e)
        raise e


def _ask_sync(image_bytes: bytes, content_type: str, brief: bool = False) -> dict:
    """Fully synchronous AI call - works with gevent/Celery (no asyncio)"""
    uri = f"data:{content_type};base64,{base64.b64encode(image_bytes).decode()}"
    logger.debug("_ask_sync Image size: %d bytes, content_type: %s", len(image_bytes), content_type)

    # Try primary OpenAI model (sync)
    try:
        logger.info("Trying %s (sync)", PRIMARY_MODEL)
        result = _ask_openai_sync(PRIMARY_MODEL, uri, brief)
        logger.info("%s sync returned result", PRIMARY_MODEL)
        return result
    except Exception as e:
        logger.warning("%s sync error: %s, trying fallback", PRIMARY_MODEL, e)

    # Fallback to secondary OpenAI model (sync)
    try:
        logger.info("Trying fallback: %s (sync)", SECONDARY_MODEL)
        result = _ask_openai_sync(SECONDARY_MODEL, uri, brief)
        logger.info("%s sync returned result", SECONDARY_MODEL)
        return result
    except Exception as e:
        logger.warning("%s sync failed: %s, trying Gemini", SECONDARY_MODEL, e)

    # Last resort: Gemini (already sync)
    try:
        logger.info("Trying last resort: Gemini (sync)")
        result = _ask_gemini_sync(image_bytes, brief)
        logger.info("Gemini sync returned result")
        return result
    except Exception as e:
        logger.warning("Gemini sync failed: %s", e)

    logger.warning("All AI models exhausted (sync)")
    return {"items": [], "insights": []}


def _is_valid_result(result: list) -> bool:
    """Check if result contains valid nutritional data"""
    if not result:
        return False
    
    for item in result:
        if isinstance(item, dict):
            if any(
                item.get(k) not in (None, 0, "", 0.0)
                for k in ["calories", "protein_g", "carbs_g", "fat_g"]
            ):
                return True
    return False

# ─── SIMPLIFIED CONSISTENCY (FASTER) ───────────────────────
def _normalize_food_name(food_name: str) -> str:
    return food_name.lower().strip().replace(' ', '_')

def _estimate_portion_consistency(items: list[dict[str, object]]) -> list[dict[str, object]]:
    """Simplified version for speed"""
    if len(items) <= 1:
        return items
    
    food_groups = {}
    for item in items:
        food_key = _normalize_food_name(str(item.get("label", "unknown")))
        if food_key not in food_groups:
            food_groups[food_key] = []
        food_groups[food_key].append(item)
    
    normalized_items = []
    for food_key, group_items in food_groups.items():
        if len(group_items) == 1:
            normalized_items.extend(group_items)
        else:
            # Just take the most complete item, skip complex calculations
            best_item = max(group_items, key=lambda x: sum(
                1 for k in ["calories", "protein_g", "carbs_g", "fat_g"]
                if x.get(k) not in (None, 0, "", 0.0)
            ))
            normalized_items.append(best_item)
    
    return normalized_items

# ─── NORMALISER ────────────────────────────────────────────
def _normalise(items: list) -> list[dict[str, object]]:
    norm: list[dict[str, object]] = []
    for itm in items:
        if isinstance(itm, dict):
            d = {k: itm.get(k) for k in
                 ["label", "calories", "protein_g", "carbs_g",
                  "fat_g", "fibre_g", "sugar_g", "calcium_mg",
                  "magnesium_mg", "sodium_mg", "potassium_mg",
                  "iron_mg", "iodine_mcg"]}
            # Check for "label" first, then "name" as fallback (AI sometimes returns "name" instead)
            d["label"] = d.get("label") or itm.get("name") or "Unknown"
            norm.append(d)
        else:
            norm.append(
                {"label": str(itm), "calories": None, "protein_g": None,
                 "carbs_g": None, "fat_g": None, "fibre_g": None, "sugar_g": None,
                 "calcium_mg": None, "magnesium_mg": None, "sodium_mg": None,
                 "potassium_mg": None, "iron_mg": None, "iodine_mcg": None}
            )
    result = norm or [{"label": "Unknown", "calories": None}]
    return _estimate_portion_consistency(result)

# ─── FOOD KNOWLEDGE DATABASE ──────────────────────────────
FOOD_BENEFITS = {
    # Fruits
    "papaya": "🍈 Rich in digestive enzymes (papain) & Vitamin C! Great for gut health and immunity.",
    "banana": "🍌 Excellent potassium source! Perfect pre/post workout for energy and muscle recovery.",
    "apple": "🍎 High in fiber & antioxidants! Supports heart health and keeps you full longer.",
    "orange": "🍊 Vitamin C powerhouse! Boosts immunity and skin health.",
    "mango": "🥭 Rich in Vitamin A & C! Great for eye health and immunity.",
    "watermelon": "🍉 Hydrating & rich in lycopene! Perfect for post-workout recovery.",
    "pomegranate": "💎 Loaded with antioxidants! Supports heart health and inflammation reduction.",
    "guava": "🌿 4x more Vitamin C than oranges! Excellent for immunity.",
    "grapes": "🍇 Rich in antioxidants! Supports brain and heart health.",

    # Vegetables
    "broccoli": "🥦 Vitamin K & fiber rich! Supports bone health and detoxification.",
    "spinach": "🌱 Iron & magnesium powerhouse! Great for energy and muscle function.",
    "carrot": "🥕 Beta-carotene rich! Excellent for eye health and skin.",
    "tomato": "🍅 Lycopene rich! Supports heart health and skin protection.",
    "cucumber": "🥒 Hydrating & low-calorie! Perfect for weight management.",
    "beetroot": "❤️ Nitrate rich! Boosts exercise performance and blood flow.",

    # Proteins
    "chicken": "🍗 Lean protein source! Great for muscle building and recovery.",
    "egg": "🥚 Complete protein with all amino acids! Brain health superfood.",
    "fish": "🐟 Omega-3 rich! Supports brain, heart, and joint health.",
    "paneer": "🧀 High protein & calcium! Great for vegetarians.",
    "tofu": "🥡 Plant-based complete protein! Low-cal, heart-healthy option.",
    "dal": "🥘 Protein & fiber combo! Budget-friendly nutrition powerhouse.",
    "rajma": "🫘 High protein & iron! Great for vegetarians and fitness goals.",

    # Grains
    "oats": "🌾 Soluble fiber rich! Lowers cholesterol and stabilizes blood sugar.",
    "brown rice": "🍚 Complex carbs & fiber! Sustained energy release.",
    "quinoa": "⭐ Complete protein grain! All 9 essential amino acids.",

    # Junk indicators
    "pizza": "🍕 Treat yourself, but balance it out! Add a salad and stay hydrated.",
    "burger": "🍔 Occasional indulgence is okay! Consider a workout today.",
    "fries": "🍟 High in sodium & trans fats. Pair with protein for balance.",
    "ice cream": "🍦 Sweet treat! Enjoy in moderation - your goals matter more.",
    "chips": "🥔 High sodium snack. Better alternatives: nuts, fruits, or popcorn.",
    "cake": "🎂 Celebrate life! Just remember your fitness journey tomorrow.",
    "samosa": "🥟 Tasty but fried! Balance with veggies and extra water.",
    "pakora": "🌟 Fried snack! Enjoy mindfully - tomorrow is a new day.",
}

JUNK_KEYWORDS = [
    "pizza", "burger", "hamburger", "cheeseburger", "fries", "chips",
    "cake", "pastry", "croissant", "donut", "doughnut", "candy",
    "soda", "cookie", "cookies", "ice cream", "chocolate", "samosa", "pakora", "vada",
    "fried chicken", "nachos", "hot dog", "hotdog", "taco bell", "mcdonald",
    "french fries", "onion rings", "milkshake", "brownie", "cupcake",
    "waffle", "pancake syrup", "churro", "pretzel", "popcorn butter",
    "cheese fries", "loaded fries", "deep fried", "fried", "breaded"
]

# ─── INTELLIGENT FOOD-SPECIFIC INSIGHTS ───────────────────
def _get_smart_insights(items: list[str], totals: dict, micro_nutrients: dict) -> list[str]:
    """Generate health-focused insights - positive for healthy foods, balanced advice for junk."""
    max_insights = 2
    insights: list[str] = []

    # Check if all items are unknown/non-food - if so, return empty insights
    items_lower = [item.lower().strip() for item in items]
    if all(item == "unknown" for item in items_lower):
        return []

    # Normalize food names for matching against heuristics/keywords.
    has_junk = any(junk in item for item in items_lower for junk in JUNK_KEYWORDS)

    # Pull nutrient values defensively and convert to float.
    def _as_float(value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    calories = _as_float(totals.get("calories"))
    protein = _as_float(totals.get("protein_g"))
    fiber = _as_float(totals.get("fibre_g"))
    sugar = _as_float(totals.get("sugar_g"))
    sodium = _as_float(micro_nutrients.get("sodium_mg"))
    potassium = _as_float(micro_nutrients.get("potassium_mg"))
    calcium = _as_float(micro_nutrients.get("calcium_mg"))
    iron = _as_float(micro_nutrients.get("iron_mg"))
    fat = _as_float(totals.get("fat_g"))

    # For JUNK FOOD: Acknowledge the treat and suggest balancing
    if has_junk:
        junk_insights = [
            "🍕 Tasty treat! Remember to balance with veggies and lean protein later.",
            "💧 High in calories and sodium. Stay hydrated and make your next meal lighter.",
            "🌟 Enjoying a treat is fine! Balance it with healthier choices throughout the day.",
            "⚖️ Processed food detected. Add fresh vegetables and fruits to your next meal.",
            "💪 One indulgence won't hurt! Just keep your fitness goals in mind for later meals.",
        ]

        # Add junk-specific insights
        if calories >= 700:
            insights.append(f"🔥 High calorie meal! Balance it out with a lighter, veggie-rich meal later.")
        elif sodium >= 900:
            insights.append(f"💧 High sodium content. Stay hydrated and choose fresh foods next time.")
        else:
            insights.append(random.choice(junk_insights[:2]))

        # Add supportive second insight
        if len(insights) < max_insights:
            supportive = [
                "✨ Small adjustments add up. Hydrate and add greens later today.",
                "💪 Enjoy mindfully! Your next meal is a chance to balance things out.",
            ]
            insights.append(random.choice(supportive))

        return insights[:max_insights]

    # For HEALTHY FOOD: Emphasize health benefits
    healthy_insights: list[str] = []

    # Check for specific food benefits
    food_specific_insight: Optional[str] = None
    for food_item in items_lower:
        for key, benefit in FOOD_BENEFITS.items():
            if key in food_item:
                food_specific_insight = benefit
                break
        if food_specific_insight:
            break

    if food_specific_insight:
        healthy_insights.append(food_specific_insight)

    # Balanced meal check
    balanced_plate = (
        calories >= 300
        and calories <= 650
        and protein >= 12
        and fiber >= 3
        and sugar <= 25
        and fat <= 25
    )
    if balanced_plate:
        healthy_insights.insert(0, "🎯 Balanced meal! Great combination for sustained energy and nutrition.")

    # Protein benefits
    if protein >= 25:
        healthy_insights.append(f"💪 Excellent protein content! Great for muscle recovery and strength.")
    elif protein >= 15:
        healthy_insights.append(f"💪 Good protein source. Helps keep you full and supports fitness goals.")

    # Fiber benefits
    if fiber >= 8:
        healthy_insights.append(f"🌾 High fiber content! Excellent for digestion and sustained energy.")
    elif fiber >= 5:
        healthy_insights.append(f"🌾 Good fiber! Supports healthy digestion and keeps you satisfied.")

    # Micronutrient benefits
    if calcium >= 200:
        healthy_insights.append(f"🦴 Rich in calcium! Great for bone health and recovery.")
    if iron >= 4:
        healthy_insights.append(f"⚡ Good iron content! Helps maintain energy levels.")
    if potassium >= 700:
        healthy_insights.append(f"💧 High potassium! Supports heart and muscle function.")

    # Low calorie benefit
    if calories < 350 and protein >= 10:
        healthy_insights.append("✨ Low calorie, nutrient-dense meal. Perfect for weight management!")

    # If we have healthy insights, return the best 2
    if healthy_insights:
        return healthy_insights[:max_insights]

    # Default encouraging message
    return ["✨ Keep tracking your meals! Staying mindful helps you reach your goals.",
            "💪 Good choice! Every healthy meal brings you closer to your fitness goals."]

# ─── RESPONSE MODELS ───────────────────────────────────────
class MicroNutrients(BaseModel):
    calcium_mg: float
    magnesium_mg: float
    sodium_mg: float
    potassium_mg: float
    iron_mg: float
    iodine_mcg: float

class PlateTotals(BaseModel):
    calories: float
    protein_g: float
    carbs_g: float
    fat_g: float
    fibre_g: float
    sugar_g: float

class PlateResponse(BaseModel):
    image_index: int
    items: List[str]
    totals: PlateTotals
    micro_nutrients: MicroNutrients
    insights: List[str]

# ─── HEALTH & METRICS ENDPOINT ────────────────────────────
@router.get("/health")
async def health_check():
    """Health check endpoint with metrics and circuit breaker status"""
    return {
        "status": "healthy",
        "timestamp": datetime.now(IST).isoformat(),
        "metrics": metrics.get_stats(),
        "circuit_breakers": {
            "openai_primary": {
                "is_open": circuit_breaker.is_open.get(f"openai-{PRIMARY_MODEL}", False),
                "failures": circuit_breaker.failures.get(f"openai-{PRIMARY_MODEL}", 0),
            },
            "openai_secondary": {
                "is_open": circuit_breaker.is_open.get(f"openai-{SECONDARY_MODEL}", False),
                "failures": circuit_breaker.failures.get(f"openai-{SECONDARY_MODEL}", 0),
            },
            "gemini": {
                "is_open": circuit_breaker.is_open.get("gemini", False),
                "failures": circuit_breaker.failures.get("gemini", 0),
            }
        },
        "concurrency": {
            "openai_semaphore_available": get_openai_semaphore()._value if _openai_semaphore else OPENAI_CONCURRENCY_LIMIT,
            "gemini_semaphore_available": get_gemini_semaphore()._value if _gemini_semaphore else GEMINI_CONCURRENCY_LIMIT,
            "cpu_semaphore_available": get_cpu_semaphore()._value if _cpu_semaphore else CPU_POOL_SIZE,
            "cpu_executor_threads": cpu_executor._max_workers,
        }
    }

@router.post("/analyze")
async def analyze(
    files: List[UploadFile] = File(...),
    food_scan: Optional[bool] = Form(None),
    client_id: Optional[int] = Form(None),
    redis: Redis = Depends(get_redis)
):
    
    start_time = time.time()

    # Mark food scan as used for freemium users
    if food_scan is not None and client_id is not None:
        today_date = date.today().strftime("%Y-%m-%d")
        food_scan_key = f"{client_id}:food_scan:{today_date}"
        await redis.set(food_scan_key, "used", ex=86400)

    # Abuse prevention: limit total scans per client per day
    if client_id is not None:
        today_date = date.today().strftime("%Y-%m-%d")
        scans_key = f"{client_id}:food_scan_count:{today_date}"

        current_scans_raw = await redis.get(scans_key)
        current_scans = int(current_scans_raw) if current_scans_raw else 0

        if current_scans >= 30:
            return {
                "status": 200,
                "message": "Daily food scan limit reached. Please try again tomorrow.",
                "data": None,
            }

        new_scan_total = await redis.incr(scans_key)
        if new_scan_total == 1:
            await redis.expire(scans_key, 86400)
        if new_scan_total > 30:
            await redis.decr(scans_key)
            return {
                "status": 200,
                "message": "Daily food scan limit reached. Please try again tomorrow.",
                "data": None,
            }

    if not files:
        raise HTTPException(status_code=400, detail="No files supplied")

    if len(files) > MAX_IMAGES_PER_REQUEST:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_IMAGES_PER_REQUEST} images per request")

    # Validate and prepare raw images for Celery (NEW: Full Celery Migration)
    raw_image_data_list = []
    for uf in files:
        # Basic validation only - compression now happens in Celery
        if not _is_supported_image(uf.content_type, uf.filename or ""):
            supported_formats = "JPEG, JPG, PNG, AVIF, WebP"
            raise HTTPException(status_code=400, detail=f"Unsupported file format. Please upload: {supported_formats}")

        # Read raw image (no compression in FastAPI)
        raw = await uf.read()

        # Validate image magic bytes to prevent disguised file uploads
        if not validate_image_magic_bytes(raw):
            raise HTTPException(status_code=400, detail="Invalid image file. File content does not match a supported image format.")

        # Create image data structure with metadata
        image_data = {
            "raw_data": base64.b64encode(raw).decode('utf-8'),
            "content_type": uf.content_type,
            "filename": uf.filename or ""
        }
        raw_image_data_list.append(image_data)

    logger.info("Compressing %d images via Celery, AI analysis inline", len(raw_image_data_list))

    # Step 1: Queue ONLY compression to Celery (CPU-bound)
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

    # Step 2: AI analysis inline in FastAPI (I/O-bound - async)
    from app.services.ai_services import analyze_food_images_inline

    result = await analyze_food_images_inline(
        [(img["compressed_b64"], img["content_type"]) for img in compressed_images],
        food_scan=food_scan if food_scan is not None else True
    )

    body = {
        "status": 200,
        "data": {
            "items": result.get("items", []),
            "totals": result.get("totals", {}),
            "micro_nutrients": result.get("micro_nutrients", {}),
            "insights": result.get("insights", []),
        },
    }

    logger.info("TOTAL REQUEST TIME: %.2fs", time.time() - start_time)
    return body



AVAILABLE_MODELS = [
    "gpt-4o-mini",      # Fast, cheap (default)
    "gpt-4o",           # Better quality
    "gpt-4-turbo",      # Turbo model
    "gpt-4",            # Original GPT-4
    "gpt-3.5-turbo",    # Cheapest
]

class FoodItemInput(BaseModel):
    name: str
    quantity: float = 1.0
    unit: str = "serving"

class FoodTextAnalyzeRequest(BaseModel):
    food_items: List[FoodItemInput]
    client_id: Optional[int] = None
    model: Optional[str] = None  # Optional: specify model for testing (gpt-4o-mini, gpt-4o, gpt-4.1-mini, gpt-4.1)

@router.post("/analyse_text")
async def analyze_text(
    request: FoodTextAnalyzeRequest,
    redis: Redis = Depends(get_redis)
):

    start_time = time.time()

    client_id = request.client_id
    food_items = request.food_items
    model = request.model

    logger.debug("/analyse_text called - client_id: %s, model: %s, items: %d", client_id, model, len(food_items) if food_items else 0)

    def _error_response(message: str):
        return {
            "status": 200,
            "data": {
                "items": [],
                "totals": {
                    "calories": 0, "protein_g": 0, "carbs_g": 0,
                    "fat_g": 0, "fibre_g": 0, "sugar_g": 0,
                },
                "micro_nutrients": {
                    "calcium_mg": 0, "magnesium_mg": 0, "sodium_mg": 0,
                    "potassium_mg": 0, "iron_mg": 0, "iodine_mcg": 0,
                },
                "insights": [],
                "message": message,
            },
        }

    # Validate model if provided - DON'T raise, just use default
    if model and model not in AVAILABLE_MODELS:
        logger.debug("Invalid model '%s', using default: %s", model, PRIMARY_MODEL)
        model = None  # Will use PRIMARY_MODEL

    # Use PRIMARY_MODEL if no model specified
    selected_model = model or PRIMARY_MODEL

    logger.info("Text food analysis request: %d items for client %s, model: %s", len(food_items) if food_items else 0, client_id, selected_model)

    # Return empty response instead of raising errors
    if not food_items:
        logger.debug("No food items provided")
        return _error_response("No food items provided")

    if len(food_items) > 20:
        logger.debug("Too many items: %d", len(food_items))
        return _error_response("Maximum 20 food items per request")

    # Abuse prevention: limit total scans per client per day
    if client_id is not None:
        today_date = date.today().strftime("%Y-%m-%d")
        scans_key = f"{client_id}:food_text_count:{today_date}"

        current_scans_raw = await redis.get(scans_key)
        current_scans = int(current_scans_raw) if current_scans_raw else 0

        if current_scans >= 50:
            return {
                "status": 200,
                "message": "Daily food text analysis limit reached. Please try again tomorrow.",
                "data": None,
            }

        new_scan_total = await redis.incr(scans_key)
        if new_scan_total == 1:
            await redis.expire(scans_key, 86400)
        if new_scan_total > 50:
            await redis.decr(scans_key)
            return {
                "status": 200,
                "message": "Daily food text analysis limit reached. Please try again tomorrow.",
                "data": None,
            }

    # Convert Pydantic models to dicts for Celery (sanitize food names)
    food_items_list = [
        {"name": sanitize_food_name(item.name), "quantity": item.quantity, "unit": item.unit}
        for item in food_items
    ]

    logger.info("Analyzing %d food items inline, model: %s", len(food_items_list), selected_model)

    # Direct async analysis - no Celery needed (pure I/O)
    from app.services.ai_services import analyze_food_text_inline

    try:
        result = await analyze_food_text_inline(
            user_id=client_id or 0,
            food_items=food_items_list,
            model=selected_model
        )

        body = {
            "status": 200,
            "data": {
                "items": result.get("items", []),
                "totals": result.get("totals", {}),
                "micro_nutrients": result.get("micro_nutrients", {}),
                "insights": result.get("insights", []),
            },
        }

        logger.info("TOTAL REQUEST TIME: %.2fs", time.time() - start_time)
        return body

    except Exception as e:
        logger.warning("Text analysis error: %s: %s", type(e).__name__, e)
        import traceback
        logger.debug("Traceback:\n%s", traceback.format_exc())
        return {
            "status": 200,
            "data": {
                "items": [],
                "totals": {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0, "fibre_g": 0, "sugar_g": 0},
                "micro_nutrients": {"calcium_mg": 0, "magnesium_mg": 0, "sodium_mg": 0, "potassium_mg": 0, "iron_mg": 0, "iodine_mcg": 0},
                "insights": [],
                "message": "An error occurred. Please try again.",
            },
        }


# ─── ASYNC IMAGE ANALYSIS WITH CELERY QUEUE (PRODUCTION) ────────────────────────
@router.post("/analyze_async")
async def analyze_async(
    files: List[UploadFile] = File(...),
    food_scan: Optional[bool] = Form(None),
    client_id: Optional[int] = Form(None),
    redis: Redis = Depends(get_redis)
):

    start_time = time.time()

    if food_scan is not None and client_id is not None:
        today_date = date.today().strftime("%Y-%m-%d")
        food_scan_key = f"{client_id}:food_scan:{today_date}"
        await redis.set(food_scan_key, "used", ex=86400)


    if client_id is not None:
        today_date = date.today().strftime("%Y-%m-%d")
        scans_key = f"{client_id}:food_scan_count:{today_date}"

        current_scans_raw = await redis.get(scans_key)
        current_scans = int(current_scans_raw) if current_scans_raw else 0

        if current_scans >= 30:
            return {
                "status": 200,
                "message": "Daily food scan limit reached. Please try again tomorrow.",
                "data": None,
            }

        new_scan_total = await redis.incr(scans_key)
        if new_scan_total == 1:
            await redis.expire(scans_key, 86400)
        if new_scan_total > 30:
            await redis.decr(scans_key)
            return {
                "status": 200,
                "message": "Daily food scan limit reached. Please try again tomorrow.",
                "data": None,
            }

    if not files:
        raise HTTPException(status_code=400, detail="No files supplied")


    raw_image_data_list = []
    for uf in files:
        if not _is_supported_image(uf.content_type, uf.filename or ""):
            supported_formats = "JPEG, JPG, PNG, AVIF, WebP"
            raise HTTPException(status_code=400, detail=f"Unsupported file format. Please upload: {supported_formats}")

        
        raw = await uf.read()

        image_data = {
            "raw_data": base64.b64encode(raw).decode('utf-8'),
            "content_type": uf.content_type,
            "filename": uf.filename or ""
        }
        raw_image_data_list.append(image_data)

    from app.tasks.image_scanner_tasks import analyze_food_image_v2
    task = analyze_food_image_v2.delay(
        user_id=client_id or 0,
        raw_image_data_list=raw_image_data_list,
        food_scan=food_scan or True
    )


    return {
        "status": 200,
        "message": "Images queued for processing",
        "job_id": task.id,
        "poll_url": f"/analyze/status/{task.id}",
        "images_count": len(files)
    }


@router.get("/analyze/status/{job_id}")
async def get_analysis_status(job_id: str):
    """
    Check status of image analysis job
    Poll this endpoint to get results
    """
    from celery.result import AsyncResult

    task = AsyncResult(job_id)

    if task.ready():
        if task.successful():
            result = task.result
            return {
                "status": 200,
                "state": "completed",
                "data": result
            }
        else:
            return {
                "status": 500,
                "state": "failed",
                "error": str(task.info)
            }
    else:
        # Task still processing
        progress = task.info.get("progress", 0) if isinstance(task.info, dict) else 0
        return {
            "status": 200,
            "state": "processing",
            "progress": progress,
            "message": "Analyzing images..."
        }


# ─── SAVE SCANNED FOOD TO DATABASE ────────────────────────
class SaveFoodRequest(BaseModel):
    client_id: int
    items: List[str]
    totals: Dict[str, float]
    micro_nutrients: Dict[str, float]
    meal: Optional[str] = None  # If not provided, auto-detect by time


def get_meal_by_current_time():
    """Determine which meal category based on current time - finds nearest meal"""
    # Get current time in IST
    now = datetime.now(IST)
    current_time = now.time()

    # Meal time ranges
    time_mapping = [
        (5, 30, 6, 0, "Early morning Detox"),
        (6, 30, 7, 0, "Pre workout"),
        (7, 0, 7, 30, "Pre-Breakfast / Pre-Meal Starter"),
        (7, 30, 8, 0, "Post workout"),
        (8, 30, 9, 30, "Breakfast"),
        (10, 0, 11, 0, "Mid-Morning snack"),
        (13, 0, 14, 0, "Lunch"),
        (16, 0, 17, 0, "Evening snack"),
        (19, 30, 20, 30, "Dinner"),
        (21, 30, 22, 0, "Bed time"),
    ]

    current_minutes = current_time.hour * 60 + current_time.minute

    # First check if current time falls within any meal range
    for start_h, start_m, end_h, end_m, meal_name in time_mapping:
        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m

        if start_minutes <= current_minutes <= end_minutes:
            logger.debug("Current time %s is within %s range", current_time, meal_name)
            return meal_name

    # If not in any range, find the nearest meal
    min_distance = float('inf')
    nearest_meal = "Breakfast"

    for start_h, start_m, end_h, end_m, meal_name in time_mapping:
        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m
        midpoint_minutes = (start_minutes + end_minutes) // 2

        distance = abs(current_minutes - midpoint_minutes)

        if distance > 720:
            distance = 1440 - distance

        if distance < min_distance:
            min_distance = distance
            nearest_meal = meal_name

    logger.debug("Current time %s - nearest meal: %s", current_time, nearest_meal)
    return nearest_meal


def get_default_diet_structure():
    """Return the default diet structure"""
    return [
        {"id": "1", "title": "Pre workout", "tagline": "Energy boost", "foodList": [], "timeRange": "6:30-7:00 AM", "itemsCount": 0},
        {"id": "2", "title": "Post workout", "tagline": "Recovery fuel", "foodList": [], "timeRange": "7:30-8:00 AM", "itemsCount": 0},
        {"id": "3", "title": "Early morning Detox", "tagline": "Early morning nutrition", "foodList": [], "timeRange": "5:30-6:00 AM", "itemsCount": 0},
        {"id": "4", "title": "Pre-Breakfast / Pre-Meal Starter", "tagline": "Pre-breakfast fuel", "foodList": [], "timeRange": "7:00-7:30 AM", "itemsCount": 0},
        {"id": "5", "title": "Breakfast", "tagline": "Start your day right", "foodList": [], "timeRange": "8:30-9:30 AM", "itemsCount": 0},
        {"id": "6", "title": "Mid-Morning snack", "tagline": "Healthy meal", "foodList": [], "timeRange": "10:00-11:00 AM", "itemsCount": 0},
        {"id": "7", "title": "Lunch", "tagline": "Nutritious midday meal", "foodList": [], "timeRange": "1:00-2:00 PM", "itemsCount": 0},
        {"id": "8", "title": "Evening snack", "tagline": "Healthy meal", "foodList": [], "timeRange": "4:00-5:00 PM", "itemsCount": 0},
        {"id": "9", "title": "Dinner", "tagline": "End your day well", "foodList": [], "timeRange": "7:30-8:30 PM", "itemsCount": 0},
        {"id": "10", "title": "Bed time", "tagline": "Rest well", "foodList": [], "timeRange": "9:30-10:00 PM", "itemsCount": 0}
    ]


@router.post("/save")
async def save_scanned_food(
    data: SaveFoodRequest,
    db: Session = Depends(get_db)
):

    try:
        # Auto-detect meal if not provided
        meal = data.meal or get_meal_by_current_time()
        today = datetime.now(IST).date()
        today_str = today.strftime("%Y-%m-%d")

        logger.debug("Saving scanned food for client %s, meal: %s", data.client_id, meal)

        # Create food item with unique ID
        unique_id = str(int(time.time() * 1000000)) + str(random.randint(10000, 99999))

        # Combine all scanned items into one food entry
        food_name = ", ".join(sanitize_food_name(item) for item in data.items)

        food_item = {
            "id": unique_id,
            "name": food_name,
            "quantity": "1 serving",
            "calories": int(data.totals.get("calories", 0)),
            "protein": round(data.totals.get("protein_g", 0), 1),
            "carbs": round(data.totals.get("carbs_g", 0), 1),
            "fat": round(data.totals.get("fat_g", 0), 1),
            "fiber": round(data.totals.get("fibre_g", 0), 1),
            "sugar": round(data.totals.get("sugar_g", 0), 1),
            "calcium": round(data.micro_nutrients.get("calcium_mg", 0), 1),
            "magnesium": round(data.micro_nutrients.get("magnesium_mg", 0), 1),
            "sodium": round(data.micro_nutrients.get("sodium_mg", 0), 1),
            "potassium": round(data.micro_nutrients.get("potassium_mg", 0), 1),
            "iron": round(data.micro_nutrients.get("iron_mg", 0), 1),
            "iodine": round(data.micro_nutrients.get("iodine_mcg", 0), 1),
            "image_url": ""
        }

        # Check if entry exists
        existing_entry = db.query(ActualDiet).filter(
            ActualDiet.client_id == data.client_id,
            ActualDiet.date == today_str
        ).first()

        if existing_entry:
            diet_data = existing_entry.diet_data if existing_entry.diet_data else []

            meal_found = False
            for meal_category in diet_data:
                if meal_category.get("title", "").lower() == meal.lower():
                    meal_category["foodList"].append(food_item)
                    meal_category["itemsCount"] = len(meal_category["foodList"])
                    meal_found = True
                    break

            if not meal_found:
                default_structure = get_default_diet_structure()
                for default_meal in default_structure:
                    if default_meal.get("title", "").lower() == meal.lower():
                        default_meal["foodList"] = [food_item]
                        default_meal["itemsCount"] = 1
                        diet_data.append(default_meal)
                        break

            from sqlalchemy.orm import attributes
            attributes.flag_modified(existing_entry, "diet_data")
            existing_entry.diet_data = diet_data
            db.commit()
        else:
            diet_data = get_default_diet_structure()
            for meal_category in diet_data:
                if meal_category.get("title", "").lower() == meal.lower():
                    meal_category["foodList"] = [food_item]
                    meal_category["itemsCount"] = 1
                    break

            new_entry = ActualDiet(
                client_id=data.client_id,
                date=today_str,
                diet_data=diet_data
            )
            db.add(new_entry)
            db.commit()

        total_calories = data.totals.get("calories", 0)

        client_target = db.query(ClientTarget).filter(ClientTarget.client_id == data.client_id).first()
        client_target_calories = client_target.calories if client_target else 0

        if client_target_calories > 0:
            ratio = total_calories / client_target_calories
            if ratio > 1:
                ratio = 1
        else:
            ratio = 0

        calorie_points = int(round(ratio * 50))
        logger.debug("XP calc -> calories=%s, target=%s, points=%s", total_calories, client_target_calories, calorie_points)

        # Check existing calorie event
        calorie_event = db.query(CalorieEvent).filter(
            CalorieEvent.client_id == data.client_id,
            CalorieEvent.event_date == today
        ).first()

        if not calorie_event:
            calorie_event = CalorieEvent(
                client_id=data.client_id,
                event_date=today,
                calories_added=0,
            )
            db.add(calorie_event)
            db.commit()

        if not calorie_event.calories_added:
            calorie_event.calories_added = 0

        added_calory = calorie_event.calories_added

        if added_calory < 50:
            if added_calory + calorie_points > 50:
                calorie_points = 50 - added_calory

            # Update Daily Leaderboard
            daily_record = db.query(LeaderboardDaily).filter(
                LeaderboardDaily.client_id == data.client_id,
                LeaderboardDaily.date == today,
            ).first()

            if daily_record:
                daily_record.xp += calorie_points
            else:
                new_daily = LeaderboardDaily(
                    client_id=data.client_id,
                    xp=calorie_points,
                    date=today,
                )
                db.add(new_daily)

            # Update Monthly Leaderboard
            month_date = today.replace(day=1)
            monthly_record = db.query(LeaderboardMonthly).filter(
                LeaderboardMonthly.client_id == data.client_id,
                LeaderboardMonthly.month == month_date,
            ).first()

            if monthly_record:
                monthly_record.xp += calorie_points
            else:
                new_monthly = LeaderboardMonthly(
                    client_id=data.client_id,
                    xp=calorie_points,
                    month=month_date,
                )
                db.add(new_monthly)

            # Update Overall Leaderboard
            overall_record = db.query(LeaderboardOverall).filter(
                LeaderboardOverall.client_id == data.client_id
            ).first()

            if overall_record:
                overall_record.xp += calorie_points
            else:
                new_overall = LeaderboardOverall(
                    client_id=data.client_id,
                    xp=calorie_points
                )
                db.add(new_overall)

            # Update CalorieEvent
            calorie_event.calories_added += calorie_points

            db.commit()
            logger.debug("XP awarded: %s", calorie_points)
        else:
            calorie_points = 0

        return {
            "status": 200,
            "message": "Food saved successfully",
            "meal": meal,
            "reward_point": calorie_points
        }

    except Exception as e:
        db.rollback()
        logger.error("Error saving scanned food: %s", e)
        raise HTTPException(status_code=500, detail=f"Error saving food: {str(e)}")
