"""Business logic for Diet Personal Templates.

Orchestrates repository + nutrition calculation.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.logging_utils import FittbotHTTPException

from ..utils import sum_nutrients_from_meals
from .repository import PersonalTemplateRepository
from .schemas import (
    AddDietTemplateRequest,
    UpdateDietTemplateRequest,
    EditDietTemplateNameRequest,
    NutritionTotals,
    TemplateItem,
    TemplateListResponse,
    SingleTemplateData,
    SingleTemplateResponse,
    AddedTemplateData,
    AddTemplateResponse,
    MessageResponse,
    FoodItem,
    CommonFoodResponse,
    SearchFoodResponse,
)

NUTRIENT_KEYS = [
    "calories", "protein", "carbs", "fat", "fiber", "sugar",
    "calcium", "magnesium", "iron", "sodium", "potassium",
]


class PersonalTemplateService:

    def __init__(self, db: AsyncSession):
        self.repo = PersonalTemplateRepository(db)

    def _nutrition_totals(self, diet_data: list) -> NutritionTotals:
        totals = sum_nutrients_from_meals(diet_data, NUTRIENT_KEYS)
        # Response uses "fats" key but food items store as "fat"
        totals["fats"] = totals.pop("fat")
        rounded = {k: round(v) for k, v in totals.items()}
        return NutritionTotals(**rounded)

    async def list_templates(self, client_id: int) -> TemplateListResponse:
        templates = await self.repo.get_personal_templates(client_id)
        data = [
            TemplateItem(
                id=t.id,
                name=t.template_name,
                nutrition_totals=self._nutrition_totals(t.diet_data),
            )
            for t in templates
        ]
        return TemplateListResponse(message="Template listed successfully", data=data)

    async def get_template(self, client_id: int, template_id: int) -> SingleTemplateResponse:
        template = await self.repo.get_template_by_id_and_client(template_id, client_id)
        if not template:
            raise FittbotHTTPException(
                status_code=404,
                detail="Template not found",
                error_code="DIET_TEMPLATE_NOT_FOUND",
                log_data={"id": template_id, "client_id": client_id},
            )
        return SingleTemplateResponse(
            message="Template retrieved successfully",
            data=SingleTemplateData(
                id=template.id,
                name=template.template_name,
                diet_data=template.diet_data,
            ),
        )

    async def add_template(self, client_id: int, request: AddDietTemplateRequest) -> AddTemplateResponse:
        if await self.repo.check_duplicate_name(client_id, request.template_name):
            raise FittbotHTTPException(
                status_code=400,
                detail=f"Template name '{request.template_name}' is already there",
                error_code="DIET_TEMPLATE_DUPLICATE",
                log_data={"client_id": client_id, "template_name": request.template_name},
            )

        template = await self.repo.create_template(
            client_id, request.template_name, request.diet_data,
        )
        return AddTemplateResponse(
            message="Diet template added successfully",
            data=AddedTemplateData(
                id=template.id,
                client_id=template.client_id,
                template_name=template.template_name,
                diet_data=template.diet_data,
            ),
        )

    async def edit_template_data(self, client_id: int, request: UpdateDietTemplateRequest) -> MessageResponse:
        updated = await self.repo.update_template_data(request.id, client_id, request.diet_data)
        if not updated:
            raise FittbotHTTPException(
                status_code=404,
                detail="Template not found with the given ID",
                error_code="DIET_TEMPLATE_NOT_FOUND",
                log_data={"id": request.id, "client_id": client_id},
            )
        return MessageResponse(message="Diet template updated successfully")

    async def edit_template_name(self, client_id: int, request: EditDietTemplateNameRequest) -> MessageResponse:
        updated = await self.repo.update_template_name(request.id, client_id, request.template_name)
        if not updated:
            raise FittbotHTTPException(
                status_code=404,
                detail="Template not found",
                error_code="DIET_TEMPLATE_NOT_FOUND",
                log_data={"id": request.id, "client_id": client_id},
            )
        return MessageResponse(message="Diet template name updated successfully")

    async def delete_template(self, client_id: int, template_id: int) -> MessageResponse:
        deleted = await self.repo.delete_template(template_id, client_id)
        if not deleted:
            raise FittbotHTTPException(
                status_code=404,
                detail="Template not found with the given ID",
                error_code="DIET_TEMPLATE_NOT_FOUND",
                log_data={"id": template_id, "client_id": client_id},
            )
        return MessageResponse(message="Diet template deleted successfully")

    # ── Common Food ──────────────────────────────────────────────

    @staticmethod
    def _to_food_item(food) -> FoodItem:
        return FoodItem(
            id=food.id,
            name=food.item,
            calories=food.calories,
            protein=food.protein,
            carbs=food.carbs,
            fat=food.fat,
            fiber=food.fiber,
            sugar=food.sugar,
            quantity=food.quantity,
            pic=food.pic,
            calcium=food.calcium,
            magnesium=food.magnesium,
            potassium=food.potassium,
            iron=food.iron,
            sodium=food.sodium,
        )

    async def get_common_foods(self) -> CommonFoodResponse:
        foods = await self.repo.get_common_foods()
        return CommonFoodResponse(
            message="Food data fetched successfully",
            data=[self._to_food_item(f) for f in foods],
        )

    async def search_foods(self, query: str) -> SearchFoodResponse:
        foods = await self.repo.search_foods(query.strip())
        return SearchFoodResponse(
            data=[self._to_food_item(f) for f in foods],
        )
