from datetime import datetime, timezone
from settings_models import UserSettingsStored, PricingContext
from pricing_context_builder import build_pricing_context

def get_default_pricing_context(materials_catalog: dict) -> PricingContext:
    """
    Generates a default, trusted PricingContext.
    Uses default settings stored instance with UTC timestamp.
    """
    default_settings = UserSettingsStored(
        updated_at=datetime.now(timezone.utc)
    )
    return build_pricing_context(materials_catalog, default_settings)
