import unittest
from unittest.mock import patch
import pydantic
import math
import copy
import json
from datetime import datetime, timezone

from settings_models import (
    UserSettingsStored,
    PricingContext,
    ResolvedMaterialPrices,
    MaterialPricingOverrides,
    CommercialSettings,
    TaxProfileSettings,
    AdditionalCostSettings,
)

from pricing_context_builder import (
    build_pricing_context,
    PricingContextBuilderError,
    InvalidGlobalCatalogError,
    UnknownMaterialOverrideError,
    PricingContextValidationError,
)

class TestPricingContextBuilder(unittest.TestCase):

    def get_valid_catalog(self):
        return {
            "profiles": {
                "P1": {"price_per_m": 10.0}
            },
            "fillings": {
                "F1": {"price_per_m2": 20.0}
            },
            "hardware": {
                "H1": {"price": 30.0}
            },
            "extras": {
                "E1": {"price_per_m2": 40.0}
            }
        }

    def get_valid_settings(self):
        return UserSettingsStored(
            schema_version=1,
            updated_at=datetime.now(timezone.utc),
            pricing=MaterialPricingOverrides(
                profiles={"P1": 15.0},
                fillings={},
                hardware={},
                extras={}
            ),
            commercial=CommercialSettings(markup_rate=10.0, discount_rate=5.0),
            tax_profile=TaxProfileSettings(name="VAT", rate=0.2, included_in_price=False),
            additional_costs=[
                AdditionalCostSettings(
                    id="cost_1",
                    name="Cost 1",
                    calculation_type="fixed_per_order",
                    value=100.0,
                    enabled=True,
                    sort_order=5
                ),
                AdditionalCostSettings(
                    id="cost_2",
                    name="Cost 2",
                    calculation_type="per_m2",
                    value=10.0,
                    enabled=False,
                    sort_order=1
                )
            ]
        )

    def test_non_dict_catalog_rejected(self):
        settings = self.get_valid_settings()
        with self.assertRaises(InvalidGlobalCatalogError):
            build_pricing_context("not a catalog dict", settings)

    def test_raw_settings_dict_rejected_before_resolution(self):
        catalog = self.get_valid_catalog()
        with self.assertRaises(TypeError) as ctx:
            build_pricing_context(catalog, {"schema_version": 1})
        self.assertIn("settings must be UserSettingsStored", str(ctx.exception))

    def test_missing_catalog_category_raises_invalid_global_catalog_error(self):
        catalog = self.get_valid_catalog()
        del catalog["hardware"]
        settings = self.get_valid_settings()
        with self.assertRaises(InvalidGlobalCatalogError):
            build_pricing_context(catalog, settings)

    def test_non_dict_catalog_category_raises_invalid_global_catalog_error(self):
        catalog = self.get_valid_catalog()
        catalog["hardware"] = "not a dict"
        settings = self.get_valid_settings()
        with self.assertRaises(InvalidGlobalCatalogError):
            build_pricing_context(catalog, settings)

    def test_non_string_material_id_rejected(self):
        catalog = self.get_valid_catalog()
        catalog["profiles"] = {123: {"price_per_m": 10.0}}
        settings = self.get_valid_settings()
        with self.assertRaises(InvalidGlobalCatalogError):
            build_pricing_context(catalog, settings)

    def test_empty_material_id_rejected(self):
        catalog = self.get_valid_catalog()
        catalog["profiles"] = {"": {"price_per_m": 10.0}}
        settings = self.get_valid_settings()
        with self.assertRaises(InvalidGlobalCatalogError):
            build_pricing_context(catalog, settings)

    def test_non_dict_catalog_entry_rejected(self):
        catalog = self.get_valid_catalog()
        catalog["profiles"]["P1"] = 150.0
        settings = self.get_valid_settings()
        with self.assertRaises(InvalidGlobalCatalogError):
            build_pricing_context(catalog, settings)

    def test_extra_with_multiple_price_fields_rejected(self):
        catalog = self.get_valid_catalog()
        catalog["extras"]["E1"] = {"price_per_m2": 40.0, "price": 50.0}
        settings = self.get_valid_settings()
        with self.assertRaises(InvalidGlobalCatalogError):
            build_pricing_context(catalog, settings)

    def test_nan_catalog_price_rejected(self):
        catalog = self.get_valid_catalog()
        catalog["profiles"]["P1"]["price_per_m"] = float("nan")
        settings = self.get_valid_settings()
        with self.assertRaises(InvalidGlobalCatalogError):
            build_pricing_context(catalog, settings)

    def test_positive_infinity_catalog_price_rejected(self):
        catalog = self.get_valid_catalog()
        catalog["profiles"]["P1"]["price_per_m"] = float("inf")
        settings = self.get_valid_settings()
        with self.assertRaises(InvalidGlobalCatalogError):
            build_pricing_context(catalog, settings)

    def test_negative_infinity_catalog_price_rejected(self):
        catalog = self.get_valid_catalog()
        catalog["profiles"]["P1"]["price_per_m"] = float("-inf")
        settings = self.get_valid_settings()
        with self.assertRaises(InvalidGlobalCatalogError):
            build_pricing_context(catalog, settings)

    def test_global_profiles_transferred_to_resolved_prices(self):
        catalog = self.get_valid_catalog()
        settings = self.get_valid_settings()
        settings.pricing.profiles = {}  # clear overrides
        ctx = build_pricing_context(catalog, settings)
        self.assertEqual(ctx.resolved_prices.profiles["P1"], 10.0)

    def test_global_fillings_transferred_to_resolved_prices(self):
        catalog = self.get_valid_catalog()
        settings = self.get_valid_settings()
        ctx = build_pricing_context(catalog, settings)
        self.assertEqual(ctx.resolved_prices.fillings["F1"], 20.0)

    def test_global_hardware_transferred_to_resolved_prices(self):
        catalog = self.get_valid_catalog()
        settings = self.get_valid_settings()
        ctx = build_pricing_context(catalog, settings)
        self.assertEqual(ctx.resolved_prices.hardware["H1"], 30.0)

    def test_global_extras_transferred_to_resolved_prices(self):
        catalog = self.get_valid_catalog()
        settings = self.get_valid_settings()
        ctx = build_pricing_context(catalog, settings)
        self.assertEqual(ctx.resolved_prices.extras["E1"], 40.0)

    def test_override_profile_precedes_global_price(self):
        catalog = self.get_valid_catalog()
        settings = self.get_valid_settings()
        settings.pricing.profiles = {"P1": 15.0}
        ctx = build_pricing_context(catalog, settings)
        self.assertEqual(ctx.resolved_prices.profiles["P1"], 15.0)

    def test_override_filling_precedes_global_price(self):
        catalog = self.get_valid_catalog()
        settings = self.get_valid_settings()
        settings.pricing.fillings = {"F1": 25.0}
        ctx = build_pricing_context(catalog, settings)
        self.assertEqual(ctx.resolved_prices.fillings["F1"], 25.0)

    def test_override_hardware_precedes_global_price(self):
        catalog = self.get_valid_catalog()
        settings = self.get_valid_settings()
        settings.pricing.hardware = {"H1": 35.0}
        ctx = build_pricing_context(catalog, settings)
        self.assertEqual(ctx.resolved_prices.hardware["H1"], 35.0)

    def test_override_extra_precedes_global_price(self):
        catalog = self.get_valid_catalog()
        settings = self.get_valid_settings()
        settings.pricing.extras = {"E1": 45.0}
        ctx = build_pricing_context(catalog, settings)
        self.assertEqual(ctx.resolved_prices.extras["E1"], 45.0)

    def test_zero_override_price_preserved_in_resolved_prices(self):
        catalog = self.get_valid_catalog()
        settings = self.get_valid_settings()
        settings.pricing.profiles = {"P1": 0.0}
        ctx = build_pricing_context(catalog, settings)
        self.assertEqual(ctx.resolved_prices.profiles["P1"], 0.0)

    def test_missing_override_falls_back_to_global_price(self):
        catalog = self.get_valid_catalog()
        settings = self.get_valid_settings()
        settings.pricing.profiles = {}
        ctx = build_pricing_context(catalog, settings)
        self.assertEqual(ctx.resolved_prices.profiles["P1"], 10.0)

    def test_unknown_profile_override_raises_unknown_material_override_error(self):
        catalog = self.get_valid_catalog()
        settings = self.get_valid_settings()
        settings.pricing.profiles = {"UNKNOWN_P": 15.0}
        with self.assertRaises(UnknownMaterialOverrideError) as err:
            build_pricing_context(catalog, settings)
        self.assertIn("profiles", str(err.exception))
        self.assertIn("UNKNOWN_P", str(err.exception))

    def test_unknown_filling_override_raises_unknown_material_override_error(self):
        catalog = self.get_valid_catalog()
        settings = self.get_valid_settings()
        settings.pricing.fillings = {"UNKNOWN_F": 25.0}
        with self.assertRaises(UnknownMaterialOverrideError):
            build_pricing_context(catalog, settings)

    def test_unknown_hardware_override_raises_unknown_material_override_error(self):
        catalog = self.get_valid_catalog()
        settings = self.get_valid_settings()
        settings.pricing.hardware = {"UNKNOWN_H": 35.0}
        with self.assertRaises(UnknownMaterialOverrideError):
            build_pricing_context(catalog, settings)

    def test_unknown_extra_override_raises_unknown_material_override_error(self):
        catalog = self.get_valid_catalog()
        settings = self.get_valid_settings()
        settings.pricing.extras = {"UNKNOWN_E": 45.0}
        with self.assertRaises(UnknownMaterialOverrideError):
            build_pricing_context(catalog, settings)

    def test_catalog_entry_without_price_raises_invalid_global_catalog_error(self):
        catalog = self.get_valid_catalog()
        del catalog["profiles"]["P1"]["price_per_m"]
        settings = self.get_valid_settings()
        with self.assertRaises(InvalidGlobalCatalogError):
            build_pricing_context(catalog, settings)

    def test_boolean_catalog_price_raises_invalid_global_catalog_error(self):
        catalog = self.get_valid_catalog()
        catalog["profiles"]["P1"]["price_per_m"] = True
        settings = self.get_valid_settings()
        with self.assertRaises(InvalidGlobalCatalogError):
            build_pricing_context(catalog, settings)

    def test_negative_catalog_price_raises_invalid_global_catalog_error(self):
        catalog = self.get_valid_catalog()
        catalog["profiles"]["P1"]["price_per_m"] = -10.0
        settings = self.get_valid_settings()
        with self.assertRaises(InvalidGlobalCatalogError):
            build_pricing_context(catalog, settings)

    def test_settings_currency_transferred_to_pricing_context(self):
        catalog = self.get_valid_catalog()
        settings = self.get_valid_settings()
        ctx = build_pricing_context(catalog, settings)
        self.assertEqual(ctx.currency, "UAH")

    def test_commercial_settings_transferred_without_calculations(self):
        catalog = self.get_valid_catalog()
        settings = self.get_valid_settings()
        ctx = build_pricing_context(catalog, settings)
        self.assertEqual(ctx.commercial.markup_rate, 10.0)
        self.assertEqual(ctx.commercial.discount_rate, 5.0)

    def test_tax_profile_transferred_without_calculations(self):
        catalog = self.get_valid_catalog()
        settings = self.get_valid_settings()
        ctx = build_pricing_context(catalog, settings)
        self.assertEqual(ctx.tax_profile.name, "VAT")
        self.assertEqual(ctx.tax_profile.rate, 0.2)
        self.assertEqual(ctx.tax_profile.included_in_price, False)

    def test_additional_costs_order_and_sort_order_are_preserved(self):
        catalog = self.get_valid_catalog()
        settings = self.get_valid_settings()
        ctx = build_pricing_context(catalog, settings)
        # Should be exactly the same order as in settings: cost_1 then cost_2
        # (builder doesn't reorder them even though cost_2 has sort_order 1 and cost_1 has 5)
        self.assertEqual(len(ctx.additional_costs), 2)
        self.assertEqual(ctx.additional_costs[0].id, "cost_1")
        self.assertEqual(ctx.additional_costs[0].sort_order, 5)
        self.assertEqual(ctx.additional_costs[1].id, "cost_2")
        self.assertEqual(ctx.additional_costs[1].sort_order, 1)

    def test_settings_schema_version_transferred_to_pricing_context(self):
        catalog = self.get_valid_catalog()
        settings = self.get_valid_settings()
        ctx = build_pricing_context(catalog, settings)
        self.assertEqual(ctx.settings_schema_version, 1)

    def test_builder_output_is_pricing_context_instance(self):
        catalog = self.get_valid_catalog()
        settings = self.get_valid_settings()
        ctx = build_pricing_context(catalog, settings)
        self.assertIsInstance(ctx, PricingContext)

    def test_uid_email_owner_fields_not_present_in_pricing_context(self):
        catalog = self.get_valid_catalog()
        settings = self.get_valid_settings()
        ctx = build_pricing_context(catalog, settings)
        self.assertFalse(hasattr(ctx, "uid"))
        self.assertFalse(hasattr(ctx, "email"))
        self.assertFalse(hasattr(ctx, "owner_uid"))

    def test_builder_does_not_mutate_source_catalog(self):
        catalog = self.get_valid_catalog()
        catalog_copy = copy.deepcopy(catalog)
        settings = self.get_valid_settings()
        build_pricing_context(catalog, settings)
        self.assertEqual(catalog, catalog_copy)

    def test_builder_does_not_mutate_user_settings_stored(self):
        catalog = self.get_valid_catalog()
        settings = self.get_valid_settings()
        settings_copy = settings.model_dump()
        build_pricing_context(catalog, settings)
        self.assertEqual(settings.model_dump(), settings_copy)

    def test_builder_does_not_import_firebase_or_fastapi(self):
        with open("pricing_context_builder.py", "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("firebase", content)
        self.assertNotIn("fastapi", content)
        self.assertNotIn("main", content)
        self.assertNotIn("calculator", content)
        self.assertNotIn("user_settings_repository", content)

    @patch("pricing_context_builder.ResolvedMaterialPrices")
    def test_pydantic_validation_error_wrapped_in_pricing_context_validation_error(self, mock_resolved):
        class DummyModel(pydantic.BaseModel):
            x: int
        try:
            DummyModel(x="not an int")
        except pydantic.ValidationError as err:
            dummy_validation_error = err

        mock_resolved.side_effect = dummy_validation_error

        catalog = self.get_valid_catalog()
        settings = self.get_valid_settings()

        with self.assertRaises(PricingContextValidationError) as ctx:
            build_pricing_context(catalog, settings)

        self.assertIsInstance(ctx.exception.__cause__, pydantic.ValidationError)

    def test_compatibility_with_real_materials_json(self):
        with open("materials.json", "r", encoding="utf-8") as f:
            catalog = json.load(f)

        # Verify color category is present and doesn't interfere
        self.assertIn("colors", catalog)

        settings = self.get_valid_settings()
        # Clean overrides so that it doesn't try to look up "P1" in real catalog
        settings.pricing.profiles = {}

        ctx = build_pricing_context(catalog, settings)
        self.assertIsInstance(ctx, PricingContext)

        # Verify that multiple real IDs from different categories are correctly resolved
        self.assertIn("REHAU_Euro_70", ctx.resolved_prices.profiles)
        self.assertEqual(ctx.resolved_prices.profiles["REHAU_Euro_70"], 250.0)

        self.assertIn("glass_24", ctx.resolved_prices.fillings)
        self.assertEqual(ctx.resolved_prices.fillings["glass_24"], 800.0)

        self.assertIn("tilt_turn", ctx.resolved_prices.hardware)
        self.assertEqual(ctx.resolved_prices.hardware["tilt_turn"], 800.0)

        self.assertIn("mosquito_net", ctx.resolved_prices.extras)
        self.assertEqual(ctx.resolved_prices.extras["mosquito_net"], 350.0)

    def test_invalid_catalog_is_reported_before_unknown_overrides(self):
        catalog = self.get_valid_catalog()
        catalog["profiles"] = {
            123: {"price_per_m": 10.0}
        }

        settings = self.get_valid_settings()
        settings.pricing.profiles = {
            "UNKNOWN": 15.0
        }

        with self.assertRaises(InvalidGlobalCatalogError):
            build_pricing_context(catalog, settings)

    def test_invalid_later_catalog_category_precedes_unknown_earlier_override(self):
        catalog = self.get_valid_catalog()
        catalog["hardware"] = {
            "H1": {
                "price": True,
            }
        }

        settings = self.get_valid_settings()
        settings.pricing.profiles = {
            "UNKNOWN": 15.0,
        }

        with self.assertRaises(InvalidGlobalCatalogError):
            build_pricing_context(catalog, settings)

    def test_whitespace_only_material_id_rejected(self):
        catalog = self.get_valid_catalog()
        catalog["profiles"] = {
            "   ": {"price_per_m": 10.0}
        }

        settings = self.get_valid_settings()

        with self.assertRaises(InvalidGlobalCatalogError):
            build_pricing_context(catalog, settings)

    def test_pricing_context_independence_from_settings(self):
        catalog = self.get_valid_catalog()
        settings = self.get_valid_settings()
        context = build_pricing_context(catalog, settings)

        # Verify references are independent
        self.assertIsNot(context.commercial, settings.commercial)
        self.assertIsNot(context.tax_profile, settings.tax_profile)
        self.assertIsNot(context.additional_costs, settings.additional_costs)
        for c1, c2 in zip(context.additional_costs, settings.additional_costs):
            self.assertIsNot(c1, c2)

        # Mutate context and check settings are unaffected
        settings_dump_before = settings.model_dump()
        context.commercial.markup_rate = 999.0
        context.tax_profile.rate = 999.0
        context.additional_costs[0].value = 999.0
        self.assertEqual(settings.model_dump(), settings_dump_before)

        # Mutate settings and check context is unaffected
        settings.commercial.markup_rate = 888.0
        settings.tax_profile.rate = 888.0
        settings.additional_costs[0].value = 888.0
        self.assertEqual(context.commercial.markup_rate, 999.0)
        self.assertEqual(context.tax_profile.rate, 999.0)
        self.assertEqual(context.additional_costs[0].value, 999.0)

if __name__ == "__main__":
    unittest.main()
