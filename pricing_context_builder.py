import math
from typing import Dict, Any
import pydantic
from settings_models import (
    UserSettingsStored,
    PricingContext,
    ResolvedMaterialPrices,
)

class PricingContextBuilderError(Exception):
    """Базовий клас для помилок builder."""
    pass

class InvalidGlobalCatalogError(PricingContextBuilderError):
    """Невалідний або пошкоджений глобальний каталог матеріалів."""
    pass

class UnknownMaterialOverrideError(PricingContextBuilderError):
    """Override налаштувань користувача посилається на невідомий матеріал."""
    pass

class PricingContextValidationError(PricingContextBuilderError):
    """Помилка валідації Pydantic моделі PricingContext."""
    pass

def _validate_price(price: Any) -> float:
    # bool is a subclass of int in Python, so we must check it explicitly first
    if isinstance(price, bool):
        raise ValueError("Boolean values are not allowed for pricing")
    if not isinstance(price, (int, float)):
        raise ValueError("Price must be a float or int")
    if not math.isfinite(price):
        raise ValueError("Price must be a finite number")
    if price < 0.0:
        raise ValueError("Price cannot be negative")
    return float(price)

def build_pricing_context(
    materials_catalog: dict,
    settings: UserSettingsStored,
) -> PricingContext:
    if not isinstance(materials_catalog, dict):
        raise InvalidGlobalCatalogError("materials_catalog must be a dictionary")

    if not isinstance(settings, UserSettingsStored):
        raise TypeError("settings must be UserSettingsStored")

    categories = ["profiles", "fillings", "hardware", "extras"]

    # Verify categories presence and types
    for cat in categories:
        if cat not in materials_catalog:
            raise InvalidGlobalCatalogError(f"Category '{cat}' is missing from catalog")
        if not isinstance(materials_catalog[cat], dict):
            raise InvalidGlobalCatalogError(f"Category '{cat}' must be a dictionary")

    # Phase 1: Validate the whole global catalog first
    validated_prices = {}
    for cat in categories:
        global_dict = materials_catalog[cat]
        validated_global_prices = {}
        for mat_id, entry in global_dict.items():
            # Validate material ID
            if not isinstance(mat_id, str):
                raise InvalidGlobalCatalogError(f"Material ID '{mat_id}' in '{cat}' must be a string")
            if not mat_id.strip():
                raise InvalidGlobalCatalogError(f"Empty or whitespace-only material ID found in '{cat}'")

            # Validate entry
            if not isinstance(entry, dict):
                raise InvalidGlobalCatalogError(f"Entry for '{mat_id}' in '{cat}' must be a dictionary")

            # Determine global price based on category
            if cat == "profiles":
                field_name = "price_per_m"
                if field_name not in entry:
                    raise InvalidGlobalCatalogError(f"Profile '{mat_id}' is missing field '{field_name}'")
                try:
                    global_price = _validate_price(entry[field_name])
                except ValueError as exc:
                    raise InvalidGlobalCatalogError(f"Invalid price for profile '{mat_id}': {exc}") from exc
            elif cat == "fillings":
                field_name = "price_per_m2"
                if field_name not in entry:
                    raise InvalidGlobalCatalogError(f"Filling '{mat_id}' is missing field '{field_name}'")
                try:
                    global_price = _validate_price(entry[field_name])
                except ValueError as exc:
                    raise InvalidGlobalCatalogError(f"Invalid price for filling '{mat_id}': {exc}") from exc
            elif cat == "hardware":
                field_name = "price"
                if field_name not in entry:
                    raise InvalidGlobalCatalogError(f"Hardware '{mat_id}' is missing field '{field_name}'")
                try:
                    global_price = _validate_price(entry[field_name])
                except ValueError as exc:
                    raise InvalidGlobalCatalogError(f"Invalid price for hardware '{mat_id}': {exc}") from exc
            elif cat == "extras":
                fields = ["price_per_m2", "price_per_m", "price"]
                found_fields = [f for f in fields if f in entry]
                if len(found_fields) == 0:
                    raise InvalidGlobalCatalogError(f"Extra '{mat_id}' is missing any price field")
                if len(found_fields) > 1:
                    raise InvalidGlobalCatalogError(f"Extra '{mat_id}' has multiple price fields: {found_fields}")
                field_name = found_fields[0]
                try:
                    global_price = _validate_price(entry[field_name])
                except ValueError as exc:
                    raise InvalidGlobalCatalogError(f"Invalid price for extra '{mat_id}': {exc}") from exc

            validated_global_prices[mat_id] = global_price
        validated_prices[cat] = validated_global_prices

    # Phase 2: Overrides and resolution
    resolved_data = {}
    for cat in categories:
        overrides = getattr(settings.pricing, cat, {})
        # Verify unknown overrides after catalog validation is done for all categories
        unknown_ids = set(overrides.keys()) - set(validated_prices[cat].keys())
        if unknown_ids:
            raise UnknownMaterialOverrideError(
                f"Unknown material overrides in category '{cat}': {unknown_ids}"
            )

        resolved_cat = {}
        for mat_id, global_price in validated_prices[cat].items():
            if mat_id in overrides:
                resolved_cat[mat_id] = overrides[mat_id]
            else:
                resolved_cat[mat_id] = global_price

        resolved_data[cat] = resolved_cat

    try:
        resolved_prices = ResolvedMaterialPrices(
            profiles=resolved_data["profiles"],
            fillings=resolved_data["fillings"],
            hardware=resolved_data["hardware"],
            extras=resolved_data["extras"]
        )
    except pydantic.ValidationError as exc:
        raise PricingContextValidationError("Failed to construct ResolvedMaterialPrices") from exc

    try:
        return PricingContext(
            additional_costs=[
                cost.model_copy(deep=True)
                for cost in settings.additional_costs
            ],
            currency=settings.currency,
            resolved_prices=resolved_prices,
            commercial=settings.commercial.model_copy(deep=True),
            tax_profile=settings.tax_profile.model_copy(deep=True),
            settings_schema_version=settings.schema_version
        )
    except pydantic.ValidationError as exc:
        raise PricingContextValidationError("Failed to construct PricingContext") from exc
