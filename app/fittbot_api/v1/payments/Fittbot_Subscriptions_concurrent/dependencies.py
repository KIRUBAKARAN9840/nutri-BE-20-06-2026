from fastapi import Depends

from app.utils.redis_config import get_redis_sync
from ..config.database import get_payment_db

from .config import HighConcurrencyConfig, get_high_concurrency_config
from .stores.command_store import CommandStore
from .services.command_dispatcher import CommandDispatcher
from .services.dailypass_dispatcher import DailyPassCommandDispatcher
from .services.dailypass_processor import DailyPassProcessor
from .services.gym_membership_dispatcher import GymMembershipCommandDispatcher
from .services.gym_membership_processor import GymMembershipProcessor
from .services.session_dispatcher import SessionCommandDispatcher
from .services.session_processor import SessionProcessor
from .services.nutrition_purchase_dispatcher import NutritionPurchaseCommandDispatcher
from .services.nutrition_purchase_processor import NutritionPurchaseProcessor
from app.fittbot_api.v2.Fymble_Payments.Subscriptions_AiCredits.credits.googleplay.dispatcher import GooglePlayCreditsDispatcher
from app.fittbot_api.v2.Fymble_Payments.Subscriptions_AiCredits.credits.googleplay.processor import GooglePlayCreditsProcessor
from .services.revenuecat_dispatcher import RevenueCatCommandDispatcher
from .services.revenuecat_processor import RevenueCatProcessor
from .services.subscription_processor import SubscriptionProcessor
from .services.webhook_processor import WebhookProcessor


async def get_config() -> HighConcurrencyConfig:
    return get_high_concurrency_config()


def get_command_store(
    config: HighConcurrencyConfig = Depends(get_config),
) -> CommandStore:
    redis = get_redis_sync()
    return CommandStore(redis, config)


async def get_command_dispatcher(
    store: CommandStore = Depends(get_command_store),
    config: HighConcurrencyConfig = Depends(get_config),
) -> CommandDispatcher:
    return CommandDispatcher(store, config)


async def get_subscription_processor(
    config: HighConcurrencyConfig = Depends(get_config),
) -> SubscriptionProcessor:
    return SubscriptionProcessor(config=config, payment_db=get_payment_db())


async def get_webhook_processor(
    config: HighConcurrencyConfig = Depends(get_config),
) -> WebhookProcessor:
    return WebhookProcessor(config=config, payment_db=get_payment_db())


def get_revenuecat_command_store(
    config: HighConcurrencyConfig = Depends(get_config),
) -> CommandStore:
    redis = get_redis_sync()
    return CommandStore(
        redis,
        config,
        redis_prefix=config.revenuecat_redis_prefix,
        command_id_prefix="rc_cmd",
    )


async def get_revenuecat_command_dispatcher(
    store: CommandStore = Depends(get_revenuecat_command_store),
    config: HighConcurrencyConfig = Depends(get_config),
) -> RevenueCatCommandDispatcher:
    return RevenueCatCommandDispatcher(store, config)


async def get_revenuecat_processor(
    config: HighConcurrencyConfig = Depends(get_config),
) -> RevenueCatProcessor:
    return RevenueCatProcessor(config=config, payment_db=get_payment_db())


def get_dailypass_command_store(
    config: HighConcurrencyConfig = Depends(get_config),
) -> CommandStore:
    redis = get_redis_sync()
    return CommandStore(
        redis,
        config,
        redis_prefix=config.dailypass_redis_prefix,
        command_id_prefix="dp_cmd",
    )


async def get_dailypass_command_dispatcher(
    store: CommandStore = Depends(get_dailypass_command_store),
    config: HighConcurrencyConfig = Depends(get_config),
) -> DailyPassCommandDispatcher:
    return DailyPassCommandDispatcher(
        store=store,
        config=config,
        checkout_queue=config.dailypass_checkout_queue_name,
        verify_queue=config.dailypass_verify_queue_name,
        upgrade_checkout_queue=config.dailypass_upgrade_checkout_queue_name,
        upgrade_verify_queue=config.dailypass_upgrade_verify_queue_name,
        edit_topup_checkout_queue=config.dailypass_edit_topup_checkout_queue_name,
        edit_topup_verify_queue=config.dailypass_edit_topup_verify_queue_name,
    )


async def get_dailypass_processor(
    config: HighConcurrencyConfig = Depends(get_config),
) -> DailyPassProcessor:
    return DailyPassProcessor(config=config, payment_db=get_payment_db(), redis=get_redis_sync())


def get_gym_membership_command_store(
    config: HighConcurrencyConfig = Depends(get_config),
) -> CommandStore:
    redis = get_redis_sync()
    return CommandStore(
        redis,
        config,
        redis_prefix=config.gym_membership_redis_prefix,
        command_id_prefix="gym_cmd",
    )


async def get_gym_membership_command_dispatcher(
    store: CommandStore = Depends(get_gym_membership_command_store),
    config: HighConcurrencyConfig = Depends(get_config),
) -> GymMembershipCommandDispatcher:
    return GymMembershipCommandDispatcher(store, config)


async def get_gym_membership_processor(
    config: HighConcurrencyConfig = Depends(get_config),
) -> GymMembershipProcessor:
    return GymMembershipProcessor(config=config, payment_db=get_payment_db())


def get_sessions_command_store(
    config: HighConcurrencyConfig = Depends(get_config),
) -> CommandStore:
    redis = get_redis_sync()
    return CommandStore(
        redis,
        config,
        redis_prefix=config.sessions_redis_prefix,
        command_id_prefix="sess_cmd",
    )


async def get_sessions_command_dispatcher(
    store: CommandStore = Depends(get_sessions_command_store),
    config: HighConcurrencyConfig = Depends(get_config),
) -> SessionCommandDispatcher:
    return SessionCommandDispatcher(store, config)


async def get_sessions_processor(
    config: HighConcurrencyConfig = Depends(get_config),
) -> SessionProcessor:
    return SessionProcessor(config=config, payment_db=get_payment_db(), redis=get_redis_sync())


def get_nutrition_purchase_command_store(
    config: HighConcurrencyConfig = Depends(get_config),
) -> CommandStore:
    redis = get_redis_sync()
    return CommandStore(
        redis,
        config,
        redis_prefix=config.nutrition_purchase_redis_prefix,
        command_id_prefix="nutr_cmd",
    )


async def get_nutrition_purchase_command_dispatcher(
    store: CommandStore = Depends(get_nutrition_purchase_command_store),
    config: HighConcurrencyConfig = Depends(get_config),
) -> NutritionPurchaseCommandDispatcher:
    return NutritionPurchaseCommandDispatcher(store, config)


# ── Credits (food-scanner) ──────────────────────────────────────────

def get_credits_command_store(
    config: HighConcurrencyConfig = Depends(get_config),
) -> CommandStore:
    redis = get_redis_sync()
    return CommandStore(
        redis,
        config,
        redis_prefix=config.credits_redis_prefix,
        command_id_prefix="cr_cmd",
    )


async def get_credits_command_dispatcher(
    store: CommandStore = Depends(get_credits_command_store),
    config: HighConcurrencyConfig = Depends(get_config),
) -> GooglePlayCreditsDispatcher:
    return GooglePlayCreditsDispatcher(store, config)
