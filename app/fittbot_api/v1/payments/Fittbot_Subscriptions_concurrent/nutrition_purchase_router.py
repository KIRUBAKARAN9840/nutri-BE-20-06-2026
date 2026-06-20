from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.utils.request_auth import resolve_authenticated_user_id

from .dependencies import (
    get_nutrition_purchase_command_dispatcher,
    get_nutrition_purchase_command_store,
)
from .schemas import (
    CommandStatusResponse,
    NutritionPurchaseCommandAccepted,
    NutritionPurchaseCheckoutRequest,
    NutritionPurchaseVerifyRequest,
)
from .services.nutrition_purchase_dispatcher import NutritionPurchaseCommandDispatcher
from .stores.command_store import CommandStore

router = APIRouter(
    prefix="/pay/nutrition_purchase_v2",
    tags=["Nutrition Purchase Payments v2"],
)


@router.post(
    "/checkout",
    response_model=NutritionPurchaseCommandAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_nutrition_purchase_checkout(
    request: Request,
    body: NutritionPurchaseCheckoutRequest,
    dispatcher: NutritionPurchaseCommandDispatcher = Depends(get_nutrition_purchase_command_dispatcher),
):
    client_id = resolve_authenticated_user_id(request, body.client_id)
    status_record = await dispatcher.enqueue_checkout(body, owner_id=client_id)
    status_url = request.url_for(
        "get_nutrition_purchase_command_status", command_id=status_record.request_id
    )

    # Track checkout initiation (non-blocking)
    from app.services.activity_tracker import track_event
    await track_event(
        int(client_id), "checkout_initiated",
        product_type="nutrition_consultation",
        source="payment_nutrition_purchase",
        command_id=status_record.request_id,
    )

    return NutritionPurchaseCommandAccepted(
        request_id=status_record.request_id,
        status=status_record.status,
        status_url=str(status_url),
    )


@router.post(
    "/verify",
    response_model=NutritionPurchaseCommandAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_nutrition_purchase_verify(
    request: Request,
    body: NutritionPurchaseVerifyRequest,
    dispatcher: NutritionPurchaseCommandDispatcher = Depends(get_nutrition_purchase_command_dispatcher),
):
    client_id = resolve_authenticated_user_id(request)
    status_record = await dispatcher.enqueue_verify(body, owner_id=client_id)
    status_url = request.url_for(
        "get_nutrition_purchase_command_status", command_id=status_record.request_id
    )

    # Track payment verification (non-blocking)
    from app.services.activity_tracker import track_event
    await track_event(
        int(client_id), "checkout_completed",
        product_type="nutrition_consultation",
        source="payment_nutrition_purchase",
        command_id=status_record.request_id,
    )

    return NutritionPurchaseCommandAccepted(
        request_id=status_record.request_id,
        status=status_record.status,
        status_url=str(status_url),
    )


@router.get(
    "/commands/{command_id}",
    response_model=CommandStatusResponse,
    name="get_nutrition_purchase_command_status",
)
async def get_nutrition_purchase_command_status(
    command_id: str,
    request: Request,
    store: CommandStore = Depends(get_nutrition_purchase_command_store),
):
    client_id = resolve_authenticated_user_id(request)
    record = await store.get(command_id, owner_id=client_id)
    if not record:
        raise HTTPException(status_code=404, detail="command_not_found")
    return record.to_response()



# Webhook is handled by the central webhook at POST /pay/razorpay_v2/webhook
# which routes to NutritionPurchaseProcessor.fulfill_from_webhook via flow="nutrition_purchase"
