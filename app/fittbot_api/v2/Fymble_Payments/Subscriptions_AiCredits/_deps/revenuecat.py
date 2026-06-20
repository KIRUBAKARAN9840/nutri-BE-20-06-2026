"""
RevenueCat REST API Client with Circuit Breaker and Retry Logic.
"""

import logging
import time
from typing import Any, Dict, Optional

import requests
from requests.exceptions import Timeout, ConnectionError

from app.utils.circuit_breaker import CircuitBreaker, CircuitOpenError

logger = logging.getLogger("payments.revenuecat.client")

RC_API_BASE = "https://api.revenuecat.com/v1"

revenuecat_circuit_breaker = CircuitBreaker(
    name="revenuecat",
    failure_threshold=5,
    recovery_timeout=45.0,
    half_open_max_calls=3,
    success_threshold=2,
)


class RevenueCatAPIError(Exception):
    pass


def _is_retryable_error(status_code: Optional[int] = None) -> bool:
    if status_code and status_code in [429, 500, 502, 503, 504]:
        return True
    return False


def get_subscriber(
    app_user_id: str,
    api_key: str,
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> Dict[str, Any]:
    """Get subscriber information from RevenueCat API with circuit breaker and retry."""
    try:
        revenuecat_circuit_breaker._before_call()
    except CircuitOpenError as e:
        raise RevenueCatAPIError(f"Service temporarily unavailable. Retry in {e.remaining_seconds:.1f}s")

    url = f"{RC_API_BASE}/subscribers/{app_user_id}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Platform": "android",
    }

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            start_time = time.time()
            response = requests.get(url, headers=headers, timeout=10)
            duration_ms = (time.time() - start_time) * 1000

            if response.status_code == 200:
                revenuecat_circuit_breaker.record_success()
                return response.json()

            if _is_retryable_error(response.status_code):
                last_error = f"HTTP {response.status_code}"
                if attempt < max_retries:
                    time.sleep(base_delay * (2 ** (attempt - 1)))
                    continue

            if response.status_code == 404:
                raise RevenueCatAPIError(f"Subscriber {app_user_id} not found")
            elif response.status_code == 401:
                raise RevenueCatAPIError("Invalid RevenueCat API key")
            else:
                response.raise_for_status()

        except (Timeout, ConnectionError) as e:
            last_error = str(e)
            if attempt < max_retries:
                time.sleep(base_delay * (2 ** (attempt - 1)))
                continue
        except RevenueCatAPIError:
            raise
        except requests.exceptions.HTTPError as e:
            last_error = str(e)
            break
        except Exception as e:
            last_error = str(e)
            break

    revenuecat_circuit_breaker.record_failure(Exception(last_error or "Unknown error"))
    raise RevenueCatAPIError(f"RevenueCat failed after {max_retries} attempts: {last_error}")


def verify_purchase(
    app_user_id: str, api_key: str
) -> tuple:
    """
    Verify if user has any active subscription or non-subscription purchase.
    Returns: (has_active_purchase, purchase_data, error_message)
    """
    try:
        subscriber_data = get_subscriber(app_user_id, api_key)
        subscriber = subscriber_data.get("subscriber", {})
        subscriptions = subscriber.get("subscriptions", {})
        entitlements = subscriber.get("entitlements", {})
        non_subscriptions = subscriber.get("non_subscriptions", {})

        for product_id, sub_info in subscriptions.items():
            expires_date = sub_info.get("expires_date")
            unsubscribe_detected_at = sub_info.get("unsubscribe_detected_at")
            is_active = expires_date and not unsubscribe_detected_at

            if is_active:
                sub_info["product_identifier"] = product_id
                return True, sub_info, None

        for ent_id, ent_data in entitlements.items():
            if ent_data.get("expires_date"):
                return True, ent_data, None

        # Check non-subscription purchases (credits, consumables)
        for product_id, purchases in non_subscriptions.items():
            if purchases:
                latest = purchases[-1]  # most recent purchase
                latest["product_identifier"] = product_id
                return True, latest, None

        return False, None, f"No active purchases found for user {app_user_id}"

    except RevenueCatAPIError as e:
        return False, None, str(e)
    except Exception as e:
        return False, None, str(e)


def get_revenuecat_circuit_status() -> Dict[str, Any]:
    return revenuecat_circuit_breaker.get_status()
