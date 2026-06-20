

import asyncio
import json
import logging
import os
import random
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import quote_plus

# Bump urllib3 connection pool defaults BEFORE firebase_admin / requests import.
# Firebase SDK (via google.auth.transport.requests.AuthorizedSession) creates its own
# Session and HTTPAdapter internally; default pool_maxsize is 10, which gets exhausted
# instantly under parallel sends. We patch HTTPAdapter.__init__ directly so EVERY
# adapter — Session-default or AuthorizedSession-default — gets a large pool.
import requests.adapters
import requests.sessions

_POOL_TARGET = 128

_orig_adapter_init = requests.adapters.HTTPAdapter.__init__
def _patched_adapter_init(self, pool_connections=_POOL_TARGET, pool_maxsize=_POOL_TARGET,
                          max_retries=0, pool_block=False):
    # Force-bump anyone who explicitly passed a small size (some libs hard-code 10).
    pool_connections = max(pool_connections, _POOL_TARGET)
    pool_maxsize = max(pool_maxsize, _POOL_TARGET)
    _orig_adapter_init(
        self,
        pool_connections=pool_connections,
        pool_maxsize=pool_maxsize,
        max_retries=max_retries,
        pool_block=pool_block,
    )
requests.adapters.HTTPAdapter.__init__ = _patched_adapter_init

# Belt-and-suspenders: also remount adapters on every new Session, in case some
# library bypasses HTTPAdapter.__init__ via deepcopy or other tricks.
_orig_session_init = requests.sessions.Session.__init__
def _patched_session_init(self):
    _orig_session_init(self)
    adapter = requests.adapters.HTTPAdapter()  # picks up our bumped defaults
    self.mount("https://", adapter)
    self.mount("http://", adapter)
requests.sessions.Session.__init__ = _patched_session_init

import boto3
import firebase_admin
from redis.asyncio import Redis as AsyncRedis, ConnectionPool as AsyncConnectionPool
from redis.exceptions import ConnectionError, TimeoutError
from firebase_admin import credentials, messaging
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, JSON, func
from sqlalchemy.orm import sessionmaker, declarative_base

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────

ENVIRONMENT = os.getenv("ENVIRONMENT", "production").lower()
REGION = os.getenv("AWS_REGION", "ap-south-2")
SECRET_NAME = "fittbot/secrets"

# FCM multicast hard limit per call
FCM_BATCH_LIMIT = 500
# DB rows per chunk when streaming clients
DB_CHUNK_SIZE = 1000
# Number of clients sent in parallel. Each does ~1 FCM call with 1-3 tokens.
# At 25 × ~3 tokens × ~5 calls/sec ≈ 375 tokens/sec — well under FCM's 10k/sec project quota.
PARALLEL_SENDS = 25
# Per-client FCM call timeout. Kills hung HTTP requests so one bad token can't
# block the whole task. Firebase SDK has no built-in per-call timeout.
PER_CLIENT_TIMEOUT_SEC = 30

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

# ─────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────

log = logging.getLogger("rich_notif_task")
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s  %(message)s",
)

# ─────────────────────────────────────────────────────────────
# MINIMAL ORM MODEL (mirrors the main app's Client)
# ─────────────────────────────────────────────────────────────

Base = declarative_base()


class Client(Base):
    __tablename__ = "clients"
    client_id = Column(Integer, primary_key=True)
    name = Column(String(100))
    device_token = Column(JSON)


class RichNotificationLog(Base):
    __tablename__ = "rich_notification_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    category = Column(String(50), nullable=False)
    sub_category = Column(String(50), nullable=True)
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    image_url = Column(String(500), nullable=True)
    total_clients = Column(Integer, nullable=False, default=0)
    total_sent = Column(Integer, nullable=False, default=0)
    total_failed = Column(Integer, nullable=False, default=0)
    invalid_tokens_cleaned = Column(Integer, nullable=False, default=0)
    sent_at = Column(DateTime, nullable=False, server_default=func.now())


# ─────────────────────────────────────────────────────────────
# DB CREDENTIALS (same pattern as water_Notifications/reminder_ecs)
# ─────────────────────────────────────────────────────────────

def load_env_file():
    """Load environment variables from .env file for local development"""
    if not ENV_FILE.exists():
        return {}

    env_vars = {}
    with open(ENV_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'\"")
            env_vars[key] = value
    return env_vars


def get_db_credentials():
    """Get database credentials based on environment"""

    if ENVIRONMENT in ("local", "development", "dev"):
        # Load from .env file for local
        env_vars = load_env_file()

        db_username = env_vars.get("DB_USERNAME") or os.getenv("DB_USERNAME", "root")
        db_password = env_vars.get("DB_PASSWORD") or os.getenv("DB_PASSWORD", "")
        db_host = env_vars.get("DB_HOST") or os.getenv("DB_HOST", "localhost")
        db_name = env_vars.get("DB_NAME") or os.getenv("DB_NAME", "fittbot_local")

        return {
            "DB_USERNAME": db_username,
            "DB_PASSWORD": db_password,
            "DB_HOST": db_host,
            "DB_NAME": db_name,
        }
    else:
        # Production: fetch from AWS Secrets Manager
        sm = boto3.client("secretsmanager", region_name=REGION)
        val = sm.get_secret_value(SecretId=SECRET_NAME)
        return json.loads(val["SecretString"])


def build_connection_string(creds):
    """Build MySQL connection string with proper URL encoding"""
    username = creds.get("DB_USERNAME")
    password = creds.get("DB_PASSWORD")
    host = creds.get("DB_HOST")
    db_name = creds.get("DB_NAME")

    if password:
        return f"mysql+pymysql://{username}:{quote_plus(password)}@{host}/{db_name}"
    else:
        return f"mysql+pymysql://{username}@{host}/{db_name}"


# ─────────────────────────────────────────────────────────────
# FIREBASE
# ─────────────────────────────────────────────────────────────

def init_firebase():
    """Initialise Firebase Admin SDK."""
    if firebase_admin._apps:
        return
    sa_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "firebase",
        "fittbot-c72eb-firebase-adminsdk-fbsvc-bfc6a7f7e9.json",
    )
    sa_path = os.path.normpath(sa_path)
    if not os.path.exists(sa_path):
        log.error("Firebase service account JSON not found at %s", sa_path)
        sys.exit(1)
    cred = credentials.Certificate(sa_path)
    firebase_admin.initialize_app(cred)
    log.info("Firebase initialised (project: %s)", cred.project_id)


# ─────────────────────────────────────────────────────────────
# TEMPLATES
# ─────────────────────────────────────────────────────────────

def load_templates(category: str) -> dict:
    """Load templates JSON and return the chosen category block."""
    tpl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notification_templates.json")
    with open(tpl_path, "r") as f:
        data = json.load(f)
    if category not in data:
        log.error("Category '%s' not found in templates. Available: %s", category, list(data.keys()))
        sys.exit(1)
    return data[category]


# ─────────────────────────────────────────────────────────────
# REDIS — cycle tracking (mirrors app/utils/redis_config.py)
# ─────────────────────────────────────────────────────────────

# Global connection pool — reused across calls (same pattern as main app)

_redis_pool: None | AsyncConnectionPool = None
_redis_client: None | AsyncRedis = None


def _get_redis_target() -> dict:
    """Determine Redis endpoint/connection sizing from env."""
    environment = os.getenv("ENVIRONMENT", "production").lower()
    host = os.getenv("REDIS_HOST", "localhost")
    port = int(os.getenv("REDIS_PORT", "6379"))

    if environment == "production":
        max_conn = 200
    elif environment == "staging":
        max_conn = 150
    else:
        max_conn = 100

    target = {"host": host, "port": port, "max_connections": max_conn}
    log.info("[redis-config] ENV=%s target=%s", environment, target)
    return target


def _get_async_connection_kwargs() -> dict:
    """Socket tuning for asyncio redis pools."""
    return dict(
        decode_responses=True,
        socket_keepalive=True,
        socket_keepalive_options={},
        retry_on_timeout=True,
        retry_on_error=[ConnectionError, TimeoutError],
        health_check_interval=30,
        socket_connect_timeout=5,
        socket_timeout=5,
    )


def create_redis_pool() -> AsyncConnectionPool:
    """Create Redis connection pool for enterprise connection management."""
    target = _get_redis_target()
    connection_kwargs = _get_async_connection_kwargs()

    if "url" in target:
        return AsyncConnectionPool.from_url(
            target["url"],
            max_connections=target["max_connections"],
            **connection_kwargs,
        )

    return AsyncConnectionPool(
        host=target["host"],
        port=target["port"],
        max_connections=target["max_connections"],
        **connection_kwargs,
    )


async def get_redis() -> AsyncRedis:
    """Get Redis client with enterprise connection pooling."""
    global _redis_pool, _redis_client

    if _redis_client is None:
        if _redis_pool is None:
            _redis_pool = create_redis_pool()

        _redis_client = AsyncRedis(connection_pool=_redis_pool)

        # Test connection
        try:
            await _redis_client.ping()
        except Exception as e:
            log.warning("Redis connection failed: %s", e)
            # Reset and retry once
            _redis_client = None
            _redis_pool = None
            if _redis_pool is None:
                _redis_pool = create_redis_pool()
            _redis_client = AsyncRedis(connection_pool=_redis_pool)

    return _redis_client


async def close_redis():
    """Close Redis connections gracefully."""
    global _redis_pool, _redis_client

    if _redis_client:
        await _redis_client.close()
        _redis_client = None

    if _redis_pool:
        await _redis_pool.disconnect()
        _redis_pool = None


async def pick_from_cycle(rds: AsyncRedis, category: str, pool_name: str, pool_size: int) -> int:
    """
    Return the next unused random index from a pool.
    Once every index has been used the cycle resets automatically.
    """
    key = f"notif:cycle:{category}:{pool_name}:used"
    used = await rds.smembers(key)
    used_indices = {int(x) for x in used}
    all_indices = set(range(pool_size))
    remaining = all_indices - used_indices

    if not remaining:
        await rds.delete(key)
        remaining = all_indices
        log.info("Cycle reset for %s:%s (all %d items used)", category, pool_name, pool_size)

    chosen = random.choice(sorted(remaining))
    await rds.sadd(key, chosen)
    log.info("Picked %s:%s index %d  (%d/%d used after this pick)",
             category, pool_name, chosen,
             pool_size - len(remaining) + 1, pool_size)
    return chosen


# ─────────────────────────────────────────────────────────────
# FCM SEND
# ─────────────────────────────────────────────────────────────

# Exception class names from firebase_admin.messaging that mean the token is
# permanently bad — safe to delete from DB. Matching by type name (not message
# text) avoids brittle keyword checks; e.g. UnregisteredError's text reads
# "Requested entity was not found." with a space, which would never match
# "NOT_FOUND" with an underscore.
PERMANENT_TOKEN_ERROR_TYPES = frozenset({
    "UnregisteredError",          # app uninstalled / token revoked
    "SenderIdMismatchError",      # token from a different Firebase project
    "InvalidArgumentError",       # malformed token
    "ThirdPartyAuthError",        # APNs cert mismatch on iOS
})

# Fallback keyword matching for any error type we don't recognize by name.
# Most messages are matched by the type set above; this catches edge cases.
PERMANENT_TOKEN_ERROR_KEYWORDS = (
    "UNREGISTERED",
    "NOT FOUND",              # space form (FCM v1 API messages)
    "NOT_FOUND",              # underscore form (older code paths)
    "INVALID REGISTRATION",
    "INVALID_REGISTRATION",
    "INVALID ARGUMENT",
    "INVALID_ARGUMENT",
    "SENDER ID MISMATCH",
    "SENDER_ID_MISMATCH",
    "MISMATCHED CREDENTIAL",
    "MISMATCHED_CREDENTIAL",
)


def send_fcm_batch(tokens, title, body, image_url):
    """Send a multicast FCM message.
    Returns (success, failure, invalid_tokens, error_summary).
    - invalid_tokens: tokens with permanent errors (safe to delete from DB)
    - error_summary: Counter of error type-name → count, plus a 'sample' list of
      up to 3 raw error strings for inspection.
    """
    notification = messaging.Notification(title=title, body=body, image=image_url)

    android_config = messaging.AndroidConfig(
        priority="high",
        notification=messaging.AndroidNotification(
            channel_id="rich_notifications",
            image=image_url,
            sound="default",
        ),
    )

    apns_config = messaging.APNSConfig(
        payload=messaging.APNSPayload(
            aps=messaging.Aps(
                alert=messaging.ApsAlert(title=title, body=body),
                mutable_content=True,
                sound="default",
            ),
        ),
        fcm_options=messaging.APNSFCMOptions(image=image_url) if image_url else None,
    )

    msg = messaging.MulticastMessage(
        tokens=tokens,
        notification=notification,
        android=android_config,
        apns=apns_config,
        data={"type": "rich_notification"},
    )

    resp = messaging.send_each_for_multicast(msg)

    invalid = []
    error_counts = Counter()
    sample_errors = []
    for i, sr in enumerate(resp.responses):
        if sr.exception:
            etype = type(sr.exception).__name__
            error_counts[etype] += 1
            err_str = str(sr.exception)
            if len(sample_errors) < 3:
                sample_errors.append(f"{etype}: {err_str[:200]}")
            # Primary check: exception class name. Fallback: keyword in message.
            if etype in PERMANENT_TOKEN_ERROR_TYPES or any(
                k in err_str.upper() for k in PERMANENT_TOKEN_ERROR_KEYWORDS
            ):
                invalid.append(tokens[i])

    return resp.success_count, resp.failure_count, invalid, {
        "counts": error_counts,
        "samples": sample_errors,
    }


# ─────────────────────────────────────────────────────────────
# PARALLEL SEND PIPELINE
# ─────────────────────────────────────────────────────────────

async def _send_one_client(client, template, image_url, semaphore):
    """
    Send a personalized push to one client (all their device tokens).
    Bounded by semaphore (concurrency limit) and per-call timeout.
    Returns (client_id, sent, failed, invalid_tokens, error_info).
    """
    async with semaphore:
        tokens = client.device_token
        if not isinstance(tokens, list):
            tokens = [tokens]
        tokens = [t for t in tokens if t]
        if not tokens:
            return client.client_id, 0, 0, [], {"counts": Counter(), "samples": []}

        client_name = client.name or "there"
        title = template["title"].replace("{{name}}", client_name)
        body = template["body"].replace("{{name}}", client_name)

        try:
            # send_fcm_batch is sync (Firebase SDK is blocking). Run in a thread
            # and bound it with wait_for so a hung HTTP call can't stall the run.
            sent, failed, invalid, errors = await asyncio.wait_for(
                asyncio.to_thread(send_fcm_batch, tokens, title, body, image_url),
                timeout=PER_CLIENT_TIMEOUT_SEC,
            )
            return client.client_id, sent, failed, invalid, errors
        except asyncio.TimeoutError:
            log.warning("Client %s FCM call timed out after %ds (%d tokens)",
                        client.client_id, PER_CLIENT_TIMEOUT_SEC, len(tokens))
            return client.client_id, 0, len(tokens), [], {
                "counts": Counter({"Timeout": len(tokens)}),
                "samples": [],
            }
        except Exception as e:
            log.warning("Client %s FCM call errored: %s", client.client_id, e)
            return client.client_id, 0, len(tokens), [], {
                "counts": Counter({type(e).__name__: len(tokens)}),
                "samples": [f"{type(e).__name__}: {str(e)[:200]}"],
            }


def _load_clients(db, test_ids):
    """Chunked load of clients with device tokens. Returns a list."""
    query = db.query(Client).filter(Client.device_token.isnot(None))
    if test_ids:
        query = query.filter(Client.client_id.in_(test_ids))

    clients = []
    offset = 0
    while True:
        chunk = (
            query.order_by(Client.client_id)
            .offset(offset).limit(DB_CHUNK_SIZE).all()
        )
        if not chunk:
            break
        clients.extend(chunk)
        offset += DB_CHUNK_SIZE
        log.info("Loaded chunk: total so far %d", len(clients))
    return clients


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

async def main():
    category = os.environ.get("NOTIF_CATEGORY", "dailypass")
    log.info("=== Rich Notification Task  |  category: %s  |  env: %s ===", category, ENVIRONMENT)

    # 1. Load template + image (Redis-backed cycle — no repeats until full rotation)
    cat_data = load_templates(category)

    # If the category is nested (sub-pools instead of templates), alternate by date.
    # e.g. "ai" → {"food_scanner": {...}, "diet_plan": {...}} — picks one sub-pool per day.
    cycle_key = category
    sub_category = None
    if "templates" not in cat_data:
        sub_keys = sorted(cat_data.keys())
        sub_idx = date.today().toordinal() % len(sub_keys)
        sub_category = sub_keys[sub_idx]
        log.info("Nested category — resolved %s → %s (day rotation %d/%d)",
                 category, sub_category, sub_idx, len(sub_keys))
        cat_data = cat_data[sub_category]
        cycle_key = f"{category}:{sub_category}"

    images = cat_data.get("images") or []
    try:
        rds = await get_redis()
        template_idx = await pick_from_cycle(rds, cycle_key, "templates", len(cat_data["templates"]))
        template = cat_data["templates"][template_idx]
        if template.get("image"):
            image_url = template["image"]
        elif images:
            image_idx = await pick_from_cycle(rds, cycle_key, "images", len(images))
            image_url = images[image_idx]
        else:
            image_url = None
    except Exception as exc:
        log.warning("Redis unavailable (%s) — falling back to random.choice", exc)
        template = random.choice(cat_data["templates"])
        if template.get("image"):
            image_url = template["image"]
        else:
            image_url = random.choice(images) if images else None

    log.info("Template  → %s", template["title"])
    log.info("Image     → %s", image_url)

    # 2. Firebase
    init_firebase()

    # 3. DB
    creds = get_db_credentials()
    conn = build_connection_string(creds)
    log.info("Connecting to DB: %s/%s", creds.get("DB_HOST"), creds.get("DB_NAME"))

    engine = create_engine(
        conn, pool_pre_ping=True, pool_size=5,
        max_overflow=2, pool_recycle=300,
    )
    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        # Optional test-mode restriction
        test_ids_env = os.environ.get("TEST_CLIENT_IDS")
        test_ids = None
        if test_ids_env:
            test_ids = [int(x.strip()) for x in test_ids_env.split(",")]
            log.info("TEST MODE — sending only to client IDs: %s", test_ids)

        # Chunked load
        clients = _load_clients(db, test_ids)
        log.info("Total clients with device tokens: %d", len(clients))

        if not clients:
            log.info("No clients to notify. Exiting.")
            return

        # Parallel send with bounded concurrency
        semaphore = asyncio.Semaphore(PARALLEL_SENDS)
        log.info("Starting parallel send: %d clients, %d concurrent workers, %ds per-call timeout",
                 len(clients), PARALLEL_SENDS, PER_CLIENT_TIMEOUT_SEC)

        tasks = [_send_one_client(c, template, image_url, semaphore) for c in clients]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Aggregate
        total_sent = 0
        total_failed = 0
        invalid_by_client = {}   # client_id -> set of invalid tokens
        error_breakdown = Counter()
        error_samples = []
        crashed = 0
        for r in results:
            if isinstance(r, Exception):
                crashed += 1
                continue
            cid, sent, failed, invalid, errors = r
            total_sent += sent
            total_failed += failed
            if invalid:
                invalid_by_client[cid] = set(invalid)
            error_breakdown.update(errors["counts"])
            if len(error_samples) < 10:
                for s in errors["samples"]:
                    if s not in error_samples and len(error_samples) < 10:
                        error_samples.append(s)

        if crashed:
            log.warning("%d send tasks raised unhandled exceptions", crashed)

        # Log a breakdown of every failure type — the actual "why" of the failure count
        if error_breakdown:
            log.info("Error type breakdown:")
            for etype, count in error_breakdown.most_common():
                log.info("  %s: %d", etype, count)
            log.info("Sample error messages:")
            for s in error_samples:
                log.info("  %s", s)

        # Batch token cleanup — single transaction
        invalid_total = sum(len(s) for s in invalid_by_client.values())
        if invalid_by_client:
            client_map = {c.client_id: c for c in clients}
            for cid, invalid_set in invalid_by_client.items():
                c = client_map.get(cid)
                if not c:
                    continue
                current = c.device_token if isinstance(c.device_token, list) else [c.device_token]
                updated = [t for t in current if t and t not in invalid_set]
                c.device_token = updated if updated else None
            db.commit()
            log.info("Cleaned %d invalid device tokens across %d clients",
                     invalid_total, len(invalid_by_client))

        log.info("=== DONE  |  sent: %d  |  failed: %d  |  clients: %d ===",
                 total_sent, total_failed, len(clients))

        # Persist run summary for reporting
        try:
            db.add(RichNotificationLog(
                category=category,
                sub_category=sub_category,
                title=template["title"],
                body=template["body"],
                image_url=image_url,
                total_clients=len(clients),
                total_sent=total_sent,
                total_failed=total_failed,
                invalid_tokens_cleaned=invalid_total,
            ))
            db.commit()
        except Exception:
            db.rollback()
            log.exception("Failed to write rich_notification_logs row")

    except Exception:
        db.rollback()
        log.exception("Fatal error during notification send")
        sys.exit(1)
    finally:
        db.close()
        await close_redis()


if __name__ == "__main__":
    asyncio.run(main())
