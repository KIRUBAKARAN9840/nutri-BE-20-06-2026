"""LLM helpers for chat-diet: generate the initial 7-day plan and follow-up plans."""

import json
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypeVar

from app.utils.async_openai import async_openai_call
from app.utils.logging_setup import jlog
from app.utils.openai_pool import get_openai_client

PRIMARY_MODEL = "gpt-5.1"
SECONDARY_MODEL = "gpt-4o"

T = TypeVar("T")


# ─── Shared output-format rules (apply to every prompt) ────────────

_INGREDIENTS_FORMAT_RULE = """
    INGREDIENTS FIELD FORMAT (MANDATORY — apply IDENTICALLY for breakfast, lunch, dinner, and snacks):
    - The "ingredients" field MUST be a single string in this EXACT shape:
      "<ingredient name> - <quantity> g; <ingredient name> - <quantity> g; ..."
    - Separator between ingredients: semicolon followed by ONE space ("; ").
    - Separator between name and quantity: " - " (space, hyphen, space).
    - Always end each quantity with a single space and lowercase "g". Never use "kg", "ml", "tsp", "tbsp", or no unit.
    - Do NOT use newlines (\\n), commas, bullets, dashes-as-bullets, or numbered lists inside the string.
    - Example (the ONLY accepted shape): "Rolled oats (dry) - 60 g; Almond milk - 250 g; Banana sliced - 80 g; Almonds chopped - 15 g"
""".strip()


_INTEGER_ONLY_RULE = """
    NUMERIC OUTPUT FORMAT (MANDATORY — applies to EVERY meal item):
    - calories, protein, carbs, fat, fiber, sugar, sodium, calcium, iron, magnesium, potassium
      MUST be WHOLE INTEGERS. No decimal points. No fractions. No trailing ".0".
    - If your computed value is 13.7, return 14. If it is 13.2, return 13. Round HALF UP.
    - "calories" must be a positive integer (e.g., 320, NOT 320.5, NOT "320 kcal").
    - "protein" / "carbs" / "fat" / "fiber" / "sugar" are integer GRAMS (no unit string).
    - "sodium" / "calcium" / "iron" / "magnesium" / "potassium" are integer MILLIGRAMS (no unit string).
    - Never output "0.0", "12.5", "1500.0" — only plain integers like 0, 13, 1500.
""".strip()


_RECIPE_FORMAT_RULE = """
    RECIPE FIELD FORMAT (MANDATORY — apply IDENTICALLY for every meal):
    - The "recipe" field MUST be a single string with numbered steps.
    - Each step MUST start with a number followed by a period and a space: "1. ", "2. ", "3. ", etc.
    - Steps must be separated by a SINGLE space (not newlines, not semicolons).
    - Example (EXACT format required): "1. Heat oil in a pan over medium flame. 2. Add chopped onions and sauté until golden. 3. Add minced garlic and cook for 1 minute."
    - INVALID formats (will cause errors): "1) Heat oil 2) Add onions" | "Step 1: Heat oil, Step 2: Add onions" | "Heat oil, then add onions" | any single paragraph without numbers.
    - The recipe must contain at least 3 steps for lunch/dinner and at least 2 steps for snacks.
""".strip()


# ─── Followup prompt blocks (step-specific) ────────────────────────

_FOLLOWUP_INTENT = {
    1: """
    THIS IS A FOLLOW-UP — WEEK 2.
    The user has just completed 7 days on the previous plan (summarized below).
    Your task: design the NEXT 7 days that progresses naturally from week 1.
    - Vary the meals: do NOT repeat any single dish from the previous plan more than 2 times this week.
    - Slight calorie adjustment: -8% to -12% of total daily calories if goal is "fat loss"; +3% to +6% if goal is "muscle gain".
    - Keep ALL dietary restrictions, allergies, and stated preferences identical.
    - Introduce 2-3 NEW dishes the user did not see in the previous week.
    """,
    2: """
    THIS IS A FOLLOW-UP — WEEK 3.
    The user has completed 14 days. Refine and progress further.
    - Tune macros: increase protein by ~10g/day if goal is "muscle gain"; raise fiber by ~5g/day if "fat loss".
    - Rotate dish variety meaningfully — at least 50% of meals should be different from week 2.
    - Add 2 quick-prep options (under 15 min) for busy weekdays.
    - Continue respecting allergies, preferences, and dietary preference exactly.
    """,
    3: """
    THIS IS THE FINAL FOLLOW-UP — WEEK 4 (LAST IN SERIES).
    The user is wrapping a 4-week journey. Focus on long-term sustainability.
    - Mix the user's most-rotated favorites from prior weeks with 2-3 new long-term options.
    - Emphasize meal-prep-friendly dishes the user can repeat independently after this week.
    - Maintenance-oriented calorie/macro balance — neither aggressive deficit nor surplus.
    - Same dietary restrictions and allergies — never compromise these.
    """,
}


def _summarize_previous_plan(plan: List[Dict[str, Any]]) -> str:
    """Compact text view of the prior plan — dish names + key macros, no recipes.

    Sending the full JSON would burn many tokens; this gives the AI enough
    context to vary meals without seeing every nutrient field.
    """
    if not plan:
        return "(no previous plan available)"

    lines: List[str] = []
    for day in plan:
        day_num = day.get("day", "?")
        parts: List[str] = []
        for meal_key in ("breakfast", "lunch", "dinner", "snacks"):
            items = day.get(meal_key, []) or []
            for item in items:
                name = item.get("name", "?")
                cal = item.get("calories", 0)
                prot = item.get("protein", 0)
                parts.append(f"{meal_key}: {name} ({cal} cal, {prot}g protein)")
        lines.append(f"Day {day_num}: " + "; ".join(parts))
    return "\n".join(lines)


# ─── Shared model fallback wrapper ─────────────────────────────────


async def _with_model_fallback(
    op: Callable[[str], Awaitable[T]], error_code: str,
) -> T:
    """Run *op* against the primary model; on any error, retry on the secondary."""
    try:
        return await op(PRIMARY_MODEL)
    except Exception as primary_err:
        jlog("warning", {
            "type": "ai_primary_failure",
            "error_code": error_code,
            "model": PRIMARY_MODEL,
            "detail": str(primary_err),
        })
        return await op(SECONDARY_MODEL)


async def generate_diet_plan(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Generate a 7-day diet plan with recipes from the user's profile data."""
    prompt = f"""
    Create a detailed 7-day diet plan for a user with the following profile:
    - Height: {data.get('height')} cm
    - Current Weight: {data.get('weight')} kg
    - Target Weight: {data.get('target_weight')} kg
    - Goal: {data.get('goal')}
    - Allergies: {', '.join(data.get('allergies', [])) if data.get('allergies') else 'None'}
    - Preferences: {', '.join(data.get('preferences', [])) if data.get('preferences') else 'None specified'}
    - Dietary Preference: {data.get('dietary_preference') or 'None specified'}
    - Other (dislikes, medical conditions, etc.): {data.get('other') or 'None specified'}

    Consider the user's food preferences and dietary restrictions when selecting meals.

    Important: Do NOT repeat the same preference food every day. If a preference is given (e.g., "dosa"), use it max 2-3 times per week, NOT on consecutive days, and NOT always the same meal type.

    For each day, provide:
    - Breakfast
    - Lunch
    - Dinner
    - Snacks (only 1 item for evening, no morning snacks)

    Each meal must contain:
    - name: Name of the dish
    - calories: Estimated calories (integer)
    - protein: grams (integer)
    - carbs: grams (integer)
    - fat: grams (integer)
    - fiber: grams (integer)
    - sugar: grams (integer)
    - sodium: milligrams (integer)
    - calcium: milligrams (integer)
    - iron: milligrams (integer)
    - magnesium: milligrams (integer)
    - potassium: milligrams (integer)
    - ingredients: List of ingredients needed (string)
    - recipe: A concise step-by-step recipe

    STRICT NUTRITION RULES:
    1. INGREDIENTS: List every ingredient with exact gram weight in its cooked/prepared state.
    2. SODIUM: 1g table salt = 393mg | 1g black salt = 385mg | Sum ALL salt sources. Never underestimate.
    3. CALORIES: Must satisfy (protein×4) + (carbs×4) + (fat×9) ≈ total. Recheck if they don't match.
    4. PROTEIN: Use IFCT/USDA values. Vegetarian meals rarely exceed 20g. Apply 10% reduction for boiled dal.
    5. FIBER: Use real per-ingredient values. Don't inflate. (Boiled moong dal 100g = ~2g, Lauki 100g = ~0.5g)
    6. MICROS: Derive from ingredients. (Cooked moong dal 100g: Iron 1.4mg, Mg 48mg | Curd 100g: Ca 120mg)

    {_INGREDIENTS_FORMAT_RULE}

    {_INTEGER_ONLY_RULE}

    {_RECIPE_FORMAT_RULE}

    Return the plan as a JSON object with a key "plan" which is a list of days, following this structure:
    {{
      "plan": [
        {{
          "day": 1,
          "breakfast": [{{ "name": "...", "calories": 0, "protein": 0, "carbs": 0, "fat": 0, "fiber": 0, "sugar": 0, "sodium": 0, "calcium": 0, "iron": 0, "magnesium": 0, "potassium": 0, "ingredients": "...", "recipe": "1. Step one here. 2. Step two here. 3. Step three here." }}],
          "lunch": [...],
          "dinner": [...],
          "snacks": [...]
        }}
      ]
    }}

    Ensure the total daily calories and macros align with the user's goal ({data.get('goal')}).
    """
    client = get_openai_client()

    async def _generate(model_name: str) -> List[Dict[str, Any]]:
        response = await async_openai_call(
            client,
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content
        result = json.loads(content)

        jlog("info", {"type": "chat_diet_plan_output", "plan": result.get("plan", result)})
        print(f"[AI_SERVICE] generate_diet_plan result: {json.dumps(result, indent=2)}")

        if isinstance(result, dict) and "plan" in result:
            return result["plan"]
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and any(str(i) in result for i in range(1, 10)):
            return [result[str(i)] for i in range(1, len(result) + 1) if str(i) in result]
        return result.get("days", result)

    try:
        return await _with_model_fallback(_generate, "CHAT_DIET_GENERATE")
    except Exception as e:
        jlog("error", {
            "type": "ai_both_models_failed",
            "error_code": "CHAT_DIET_GENERATE",
            "detail": str(e),
        })
        raise Exception(f"AI Plan Generation failed on both models: {str(e)}")


async def generate_followup_diet_plan(
    data: Dict[str, Any],
    previous_plan: List[Dict[str, Any]],
    step: int,
    feedback: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Generate a follow-up 7-day plan (step 1, 2, or 3) using the prior plan as context.

    Output shape is identical to ``generate_diet_plan`` so the frontend renders
    initial and follow-up plans the same way.
    """
    intent_block = _FOLLOWUP_INTENT.get(step)
    if intent_block is None:
        raise ValueError(f"Unsupported follow-up step: {step}")

    previous_summary = _summarize_previous_plan(previous_plan)
    feedback_block = (
        f"\n    The user shared this feedback for this week:\n    \"{feedback}\"\n"
        if feedback else ""
    )

    prompt = f"""
    Create a detailed 7-day diet plan for a user with the following profile:
    - Height: {data.get('height')} cm
    - Current Weight: {data.get('weight')} kg
    - Target Weight: {data.get('target_weight')} kg
    - Goal: {data.get('goal')}
    - Allergies: {', '.join(data.get('allergies', [])) if data.get('allergies') else 'None'}
    - Preferences: {', '.join(data.get('preferences', [])) if data.get('preferences') else 'None specified'}
    - Dietary Preference: {data.get('dietary_preference') or 'None specified'}
    - Other (dislikes, medical conditions, etc.): {data.get('other') or 'None specified'}

    {intent_block.strip()}
    {feedback_block}
    PREVIOUS WEEK'S PLAN (for context — do not repeat):
    {previous_summary}

    For each day, provide:
    - Breakfast
    - Lunch
    - Dinner
    - Snacks (only 1 item for evening, no morning snacks)

    Each meal must contain:
    - name: Name of the dish
    - calories: Estimated calories (integer)
    - protein: grams (integer)
    - carbs: grams (integer)
    - fat: grams (integer)
    - fiber: grams (integer)
    - sugar: grams (integer)
    - sodium: milligrams (integer)
    - calcium: milligrams (integer)
    - iron: milligrams (integer)
    - magnesium: milligrams (integer)
    - potassium: milligrams (integer)
    - ingredients: List of ingredients needed (string)
    - recipe: A concise step-by-step recipe

    STRICT NUTRITION RULES:
    1. INGREDIENTS: List every ingredient with exact gram weight in its cooked/prepared state.
    2. SODIUM: 1g table salt = 393mg | 1g black salt = 385mg | Sum ALL salt sources. Never underestimate.
    3. CALORIES: Must satisfy (protein×4) + (carbs×4) + (fat×9) ≈ total. Recheck if they don't match.
    4. PROTEIN: Use IFCT/USDA values. Vegetarian meals rarely exceed 20g. Apply 10% reduction for boiled dal.
    5. FIBER: Use real per-ingredient values. Don't inflate. (Boiled moong dal 100g = ~2g, Lauki 100g = ~0.5g)
    6. MICROS: Derive from ingredients. (Cooked moong dal 100g: Iron 1.4mg, Mg 48mg | Curd 100g: Ca 120mg)

    {_INGREDIENTS_FORMAT_RULE}

    {_INTEGER_ONLY_RULE}

    {_RECIPE_FORMAT_RULE}

    Return the plan as a JSON object with a key "plan" which is a list of days, following this structure:
    {{
      "plan": [
        {{
          "day": 1,
          "breakfast": [{{ "name": "...", "calories": 0, "protein": 0, "carbs": 0, "fat": 0, "fiber": 0, "sugar": 0, "sodium": 0, "calcium": 0, "iron": 0, "magnesium": 0, "potassium": 0, "ingredients": "...", "recipe": "1. Step one here. 2. Step two here. 3. Step three here." }}],
          "lunch": [...],
          "dinner": [...],
          "snacks": [...]
        }}
      ]
    }}
    Ensure the total daily calories and macros align with the user's goal ({data.get('goal')}) AND the follow-up week intent above.
    """
    client = get_openai_client()

    async def _generate(model_name: str) -> List[Dict[str, Any]]:
        response = await async_openai_call(
            client,
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content
        result = json.loads(content)

        jlog("info", {"type": "chat_diet_followup_plan_output", "step": step, "plan": result.get("plan", result)})
        print(f"[AI_SERVICE] generate_followup_diet_plan (step {step}) result: {json.dumps(result, indent=2)}")

        if isinstance(result, dict) and "plan" in result:
            return result["plan"]
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and any(str(i) in result for i in range(1, 10)):
            return [result[str(i)] for i in range(1, len(result) + 1) if str(i) in result]
        return result.get("days", result)

    error_code = f"CHAT_DIET_FOLLOWUP_{step}"
    try:
        return await _with_model_fallback(_generate, error_code)
    except Exception as e:
        jlog("error", {
            "type": "ai_both_models_failed",
            "error_code": error_code,
            "step": step,
            "detail": str(e),
        })
        raise Exception(f"AI Follow-up Plan Generation (step {step}) failed: {str(e)}")


# ─── Single-meal swap ──────────────────────────────────────────────


_MEAL_TYPE_GUIDANCE = {
    "breakfast": "Choose something appropriate for morning eating — light to moderate calories, energy-providing.",
    "lunch": "Choose a balanced midday meal with adequate protein, carbs, and vegetables.",
    "dinner": "Choose a satisfying evening meal — moderate carbs, good protein.",
    "snacks": "Choose a single evening snack — small portion, between 100-250 calories typically.",
}


async def swap_meal_item(
    profile: Dict[str, Any],
    current_item: Dict[str, Any],
    day: int,
    meal_type: str,
    other_dish_names: List[str],
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate a single replacement meal item.

    Args:
        profile: original collected_data (height, weight, goal, allergies, etc.)
        current_item: the meal being swapped (full MealItem dict)
        day: 1-7 (just for context in prompt)
        meal_type: breakfast / lunch / dinner / snacks
        other_dish_names: names of every OTHER dish already in the plan,
                          so the AI doesn't pick a duplicate
        reason: user's optional reason for swapping

    Returns:
        A single meal item dict (same 14 fields as MealItem schema).
    """
    current_cal = current_item.get("calories", 0)
    cal_low = max(int(current_cal * 0.85), 0)
    cal_high = int(current_cal * 1.15)

    reason_block = (
        f"\n    The user requested this swap because: \"{reason.strip()}\"\n"
        if reason and reason.strip() else ""
    )

    other_dishes_block = (
        "\n".join(f"- {name}" for name in other_dish_names)
        if other_dish_names else "(no other dishes to avoid)"
    )

    meal_guidance = _MEAL_TYPE_GUIDANCE.get(meal_type, "")

    prompt = f"""
    You are swapping ONE meal item from a 7-day diet plan.
    Generate exactly ONE replacement that fits the same meal slot.

    USER PROFILE:
    - Height: {profile.get('height')} cm
    - Current Weight: {profile.get('weight')} kg
    - Target Weight: {profile.get('target_weight')} kg
    - Goal: {profile.get('goal')}
    - Allergies: {', '.join(profile.get('allergies', [])) if profile.get('allergies') else 'None'}
    - Preferences: {', '.join(profile.get('preferences', [])) if profile.get('preferences') else 'None specified'}
    - Dietary Preference: {profile.get('dietary_preference') or 'None specified'}
    - Other (dislikes, medical conditions): {profile.get('other') or 'None specified'}

    CURRENT MEAL TO REPLACE:
    - Day: {day}
    - Meal type: {meal_type}
    - Name: "{current_item.get('name')}"
    - Calories: {current_cal}
    - Protein: {current_item.get('protein')}g
    - Carbs: {current_item.get('carbs')}g
    - Fat: {current_item.get('fat')}g
    {reason_block}
    HARD CONSTRAINTS for the replacement:
    1. MUST fit "{meal_type}" slot. {meal_guidance}
    2. Calories MUST be between {cal_low} and {cal_high}.
    3. DIETARY PREFERENCE — interpret the user's dietary_preference STRICTLY:
       - If it contains "veg" but NOT "non" (i.e., vegetarian): the dish MUST contain ZERO meat,
         poultry, fish, shellfish, or any animal-derived stock/broth. Eggs and dairy are allowed
         only if not contradicted by allergies or 'other'.
       - If it contains "vegan": NO animal products at all — no meat, fish, eggs, dairy, honey, gelatin.
       - If it is "non-veg" / "non vegetarian" / "non-vegetarian": both vegetarian AND non-vegetarian
         dishes are allowed; prefer the same protein style as the meal being replaced unless the user's
         feedback says otherwise.
       - Other free-text values (e.g., "keto", "jain", "eggetarian"): apply standard interpretation
         and never contradict it.
    4. MUST respect ALL allergies, preferences, and 'other' constraints listed above (medical conditions,
       dislikes, religious restrictions). Treat allergies as hard exclusions.
    5. MUST NOT match any of these dishes already in the plan:
    {other_dishes_block}
    6. MUST be a real, edible dish (no gibberish). Indian foods are encouraged when they match
       the dietary preference.

    Return EXACTLY ONE replacement meal as a JSON object with this structure (no extra fields, no wrapper):
    {{
      "name": "...",
      "calories": 0,
      "protein": 0,
      "carbs": 0,
      "fat": 0,
      "fiber": 0,
      "sugar": 0,
      "sodium": 0,
      "calcium": 0,
      "iron": 0,
      "magnesium": 0,
      "potassium": 0,
      "ingredients": "...",
      "recipe": "..."
    }}

    STRICT NUTRITION RULES:
    1. INGREDIENTS: list every ingredient with exact gram weight in cooked state.
    2. SODIUM: 1g salt = ~393mg. Sum all salt sources.
    3. CALORIES: must satisfy (protein×4) + (carbs×4) + (fat×9) ≈ total.
    4. PROTEIN: use IFCT/USDA values, do not inflate.
    5. FIBER and MICROS: derive from real ingredient values.

    {_INGREDIENTS_FORMAT_RULE}

    {_INTEGER_ONLY_RULE}

    {_RECIPE_FORMAT_RULE}

    Return ONLY the JSON object — no commentary, no code fences.
    """
    client = get_openai_client()

    async def _generate(model_name: str) -> Dict[str, Any]:
        response = await async_openai_call(
            client,
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        result = json.loads(content)
        # Some models wrap in {"item": {...}} or {"meal": {...}}; unwrap defensively.
        if isinstance(result, dict) and "name" not in result:
            for key in ("item", "meal", "replacement", "data"):
                inner = result.get(key)
                if isinstance(inner, dict) and "name" in inner:
                    return inner
        return result

    error_code = "CHAT_DIET_SWAP"
    try:
        return await _with_model_fallback(_generate, error_code)
    except Exception as e:
        jlog("error", {
            "type": "ai_both_models_failed",
            "error_code": error_code,
            "day": day,
            "meal_type": meal_type,
            "detail": str(e),
        })
        raise Exception(f"AI Meal Swap failed on both models: {str(e)}")