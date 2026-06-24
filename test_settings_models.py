import unittest
from datetime import datetime
from pydantic import ValidationError
from settings_models import (
    UserSettingsUpdate,
    UserSettingsStored,
    UserSettingsResponse,
    PricingContext,
    AdditionalCostSettings,
    MaterialPricingOverrides,
    ResolvedMaterialPrices,
    CommercialSettings,
    TaxProfileSettings,
    CalculationType,
)

class TestUserSettingsModels(unittest.TestCase):

    def test_01_valid_user_settings_update(self):
        """1. Fully valid UserSettingsUpdate input is parsed successfully."""
        data = {
            "currency": "UAH",
            "pricing": {
                "profiles": {"REHAU_Euro_70": 260.0, "WDS_500": 190.0},
                "fillings": {"glass_24": 850.0},
                "hardware": {"tilt_turn": 900.0},
                "extras": {"mosquito_net": 380.0}
            },
            "additional_costs": [
                {
                    "id": "delivery_city",
                    "name": "Доставка по місту",
                    "calculation_type": "fixed_per_order",
                    "value": 500.0,
                    "enabled": True,
                    "sort_order": 10
                }
            ],
            "commercial": {
                "markup_rate": 15.0,
                "discount_rate": 5.0
            },
            "tax_profile": {
                "name": "Платник ПДВ (20%)",
                "rate": 0.20,
                "included_in_price": False
            }
        }
        model = UserSettingsUpdate(**data)
        self.assertEqual(model.currency, "UAH")
        self.assertEqual(model.pricing.profiles["REHAU_Euro_70"], 260.0)
        self.assertEqual(len(model.additional_costs), 1)
        self.assertEqual(model.additional_costs[0].id, "delivery_city")
        self.assertEqual(model.commercial.markup_rate, 15.0)
        self.assertEqual(model.tax_profile.rate, 0.20)

    def test_02_empty_put_normalization(self):
        """2. Empty input fully normalizes to clean defaults (full replacement semantics)."""
        model = UserSettingsUpdate(**{})
        self.assertEqual(model.currency, "UAH")
        self.assertEqual(model.pricing.profiles, {})
        self.assertEqual(model.additional_costs, [])
        self.assertEqual(model.commercial.markup_rate, 0.0)
        self.assertEqual(model.commercial.discount_rate, 0.0)
        self.assertEqual(model.tax_profile.name, "Без податку")
        self.assertEqual(model.tax_profile.rate, 0.0)
        self.assertEqual(model.tax_profile.included_in_price, False)

    def test_03_zero_price_override_allowed(self):
        """3. Zero price material override is allowed."""
        pricing_data = {
            "profiles": {"FREE_PROFILE": 0.0}
        }
        model = MaterialPricingOverrides(**pricing_data)
        self.assertEqual(model.profiles["FREE_PROFILE"], 0.0)

    def test_04_tax_rate_20_allowed(self):
        """4. Tax rate of 0.20 (20%) is allowed."""
        model = TaxProfileSettings(name="ПДВ", rate=0.20)
        self.assertEqual(model.rate, 0.20)

    def test_05_all_calculation_types_accepted(self):
        """5. All five allowed CalculationTypes are parsed correctly."""
        for calc_type in [
            "fixed_per_order",
            "fixed_per_item",
            "per_m2",
            "per_linear_meter",
            "percent_of_materials"
        ]:
            model = AdditionalCostSettings(
                id="cost",
                name="Test",
                calculation_type=calc_type,
                value=50.0
            )
            self.assertEqual(model.calculation_type, calc_type)

    def test_06_user_settings_stored_version_1(self):
        """6. UserSettingsStored accepts schema_version=1 and datetime."""
        now = datetime.utcnow()
        stored = UserSettingsStored(schema_version=1, updated_at=now)
        self.assertEqual(stored.schema_version, 1)
        self.assertEqual(stored.updated_at, now)

    def test_07_user_settings_response_default(self):
        """7. UserSettingsResponse supports is_default field."""
        now = datetime.utcnow()
        resp = UserSettingsResponse(schema_version=1, updated_at=now, is_default=True)
        self.assertTrue(resp.is_default)

    def test_08_pricing_context_trusted_data(self):
        """8. PricingContext initializes successfully with trusted data."""
        ctx = PricingContext(
            resolved_prices={
                "profiles": {"WDS_400": 150.0},
                "fillings": {},
                "hardware": {},
                "extras": {}
            },
            additional_costs=[],
            commercial={"markup_rate": 10.0, "discount_rate": 0.0},
            tax_profile={"name": "VAT", "rate": 0.20, "included_in_price": True},
            settings_schema_version=1
        )
        self.assertEqual(ctx.settings_schema_version, 1)
        self.assertEqual(ctx.resolved_prices.profiles["WDS_400"], 150.0)
        self.assertIsInstance(ctx.resolved_prices, ResolvedMaterialPrices)

    def test_09_negative_override_price_rejected(self):
        """9. Negative override price is rejected."""
        with self.assertRaises(ValidationError):
            MaterialPricingOverrides(profiles={"REHAU": -10.0})

    def test_10_nan_in_pricing_rejected(self):
        """10. NaN in pricing override is rejected."""
        with self.assertRaises(ValidationError):
            MaterialPricingOverrides(profiles={"REHAU": float("nan")})

    def test_11_infinity_in_pricing_rejected(self):
        """11. Infinity in pricing override is rejected."""
        with self.assertRaises(ValidationError):
            MaterialPricingOverrides(profiles={"REHAU": float("inf")})

    def test_12_too_many_overrides_rejected(self):
        """12. Over 100 overrides in a single category is rejected."""
        overrides = {f"profile_{i}": 100.0 for i in range(101)}
        with self.assertRaises(ValidationError):
            MaterialPricingOverrides(profiles=overrides)

    def test_13_too_many_additional_costs_rejected(self):
        """13. Over 20 additional costs is rejected in UserSettingsUpdate."""
        costs = [
            AdditionalCostSettings(id=f"c_{i}", name="cost", calculation_type="fixed_per_order", value=10.0)
            for i in range(21)
        ]
        with self.assertRaises(ValidationError):
            UserSettingsUpdate(additional_costs=costs)

    def test_14_duplicate_cost_ids_rejected(self):
        """14. Duplicate additional cost IDs are rejected across all models."""
        costs = [
            AdditionalCostSettings(id="dup", name="Cost 1", calculation_type="fixed_per_order", value=10.0),
            AdditionalCostSettings(id="dup", name="Cost 2", calculation_type="fixed_per_order", value=20.0)
        ]
        # Test UserSettingsUpdate
        with self.assertRaises(ValidationError):
            UserSettingsUpdate(additional_costs=costs)
        # Test UserSettingsStored
        with self.assertRaises(ValidationError):
            UserSettingsStored(additional_costs=costs, updated_at=datetime.utcnow())
        # Test UserSettingsResponse
        with self.assertRaises(ValidationError):
            UserSettingsResponse(additional_costs=costs, updated_at=datetime.utcnow())
        # Test PricingContext
        with self.assertRaises(ValidationError):
            PricingContext(
                resolved_prices={"profiles": {}, "fillings": {}, "hardware": {}, "extras": {}},
                additional_costs=costs,
                commercial={"markup_rate": 0.0, "discount_rate": 0.0},
                tax_profile={"name": "VAT", "rate": 0.0, "included_in_price": False}
            )

    def test_15_invalid_calculation_type_rejected(self):
        """15. Invalid calculation_type string is rejected."""
        with self.assertRaises(ValidationError):
            AdditionalCostSettings(id="c", name="cost", calculation_type="invalid_type", value=10.0)

    def test_16_negative_additional_cost_value_rejected(self):
        """16. Negative value in additional cost is rejected."""
        with self.assertRaises(ValidationError):
            AdditionalCostSettings(id="c", name="cost", calculation_type="fixed_per_order", value=-5.0)

    def test_17_percent_of_materials_bounds(self):
        """17. percent_of_materials value > 100 is rejected."""
        with self.assertRaises(ValidationError):
            AdditionalCostSettings(id="c", name="cost", calculation_type="percent_of_materials", value=105.0)

    def test_18_markup_rate_bounds(self):
        """18. markup_rate > 500 or negative is rejected."""
        with self.assertRaises(ValidationError):
            CommercialSettings(markup_rate=505.0)
        with self.assertRaises(ValidationError):
            CommercialSettings(markup_rate=-5.0)

    def test_19_discount_rate_bounds(self):
        """19. discount_rate > 100 or negative is rejected."""
        with self.assertRaises(ValidationError):
            CommercialSettings(discount_rate=105.0)
        with self.assertRaises(ValidationError):
            CommercialSettings(discount_rate=-5.0)

    def test_20_tax_rate_bounds(self):
        """20. tax_rate > 1.0 or negative is rejected."""
        with self.assertRaises(ValidationError):
            TaxProfileSettings(rate=1.1)
        with self.assertRaises(ValidationError):
            TaxProfileSettings(rate=-0.05)

    def test_21_empty_or_whitespace_strings_rejected(self):
        """21. Whitespace-only values are rejected for material ID, cost name, cost ID, and tax profile name."""
        # Whitespace material ID in overrides
        with self.assertRaises(ValidationError):
            MaterialPricingOverrides(profiles={"   ": 100.0})
        # Whitespace additional cost name
        with self.assertRaises(ValidationError):
            AdditionalCostSettings(id="cost", name="   ", calculation_type="fixed_per_order", value=10.0)
        # Whitespace additional cost ID (fails pattern match)
        with self.assertRaises(ValidationError):
            AdditionalCostSettings(id="   ", name="Cost", calculation_type="fixed_per_order", value=10.0)
        # Whitespace tax profile name
        with self.assertRaises(ValidationError):
            TaxProfileSettings(name="   ", rate=0.0)

    def test_22_extra_fields_forbidden(self):
        """22. Extra fields are rejected (allow_extra=forbid). Check metadata fields too."""
        # Extra fields in UserSettingsUpdate
        with self.assertRaises(ValidationError):
            UserSettingsUpdate(extra_field="error")
        # Attacking with UID or email fields
        with self.assertRaises(ValidationError):
            UserSettingsUpdate(uid="user123")
        with self.assertRaises(ValidationError):
            UserSettingsUpdate(owner_uid="user123")
        with self.assertRaises(ValidationError):
            UserSettingsUpdate(user_email="attack@example.com")

    def test_23_schema_version_in_update_rejected(self):
        """23. Providing schema_version in UserSettingsUpdate is rejected."""
        with self.assertRaises(ValidationError):
            UserSettingsUpdate(schema_version=1)

    def test_24_updated_at_in_update_rejected(self):
        """24. Providing updated_at in UserSettingsUpdate is rejected."""
        with self.assertRaises(ValidationError):
            UserSettingsUpdate(updated_at=datetime.utcnow())

    def test_25_invalid_currency_rejected(self):
        """25. Currency other than 'UAH' is rejected (Literal validation)."""
        with self.assertRaises(ValidationError):
            UserSettingsUpdate(currency="USD")

    def test_26_invalid_schema_version_rejected(self):
        """26. Invalid schema_version in stored model or pricing context is rejected."""
        # Stored model
        with self.assertRaises(ValidationError):
            UserSettingsStored(schema_version=2, updated_at=datetime.utcnow())
        # Pricing context
        with self.assertRaises(ValidationError):
            PricingContext(
                resolved_prices={"profiles": {}, "fillings": {}, "hardware": {}, "extras": {}},
                additional_costs=[],
                commercial={"markup_rate": 0.0, "discount_rate": 0.0},
                tax_profile={"name": "VAT", "rate": 0.0, "included_in_price": False},
                settings_schema_version=2
            )

    def test_27_taxable_field_rejected(self):
        """27. Presence of 'taxable' field in AdditionalCostSettings is rejected."""
        with self.assertRaises(ValidationError):
            AdditionalCostSettings.model_validate({
                "id": "delivery",
                "name": "Delivery",
                "calculation_type": "fixed_per_order",
                "value": 100.0,
                "taxable": True
            })

    def test_28_percent_of_subtotal_rejected(self):
        """28. percent_of_subtotal type is rejected."""
        with self.assertRaises(ValidationError):
            AdditionalCostSettings(id="c", name="cost", calculation_type="percent_of_subtotal", value=5.0)

    def test_29_nan_inf_rejected_in_all_float_fields(self):
        """29. NaN and Infinity are rejected in cost values, markups, discounts, and tax rates."""
        # Additional cost value
        with self.assertRaises(ValidationError):
            AdditionalCostSettings(id="c", name="cost", calculation_type="fixed_per_order", value=float("nan"))
        with self.assertRaises(ValidationError):
            AdditionalCostSettings(id="c", name="cost", calculation_type="fixed_per_order", value=float("inf"))

        # Markup/Discount rates
        with self.assertRaises(ValidationError):
            CommercialSettings(markup_rate=float("nan"))
        with self.assertRaises(ValidationError):
            CommercialSettings(markup_rate=float("inf"))
        with self.assertRaises(ValidationError):
            CommercialSettings(discount_rate=float("nan"))
        with self.assertRaises(ValidationError):
            CommercialSettings(discount_rate=float("inf"))

        # Tax rate
        with self.assertRaises(ValidationError):
            TaxProfileSettings(rate=float("nan"))
        with self.assertRaises(ValidationError):
            TaxProfileSettings(rate=float("inf"))

    def test_30_resolved_material_prices_valid(self):
        """30. ResolvedMaterialPrices accepts valid pricing data."""
        resolved = ResolvedMaterialPrices(
            profiles={"REHAU_70": 250.0},
            fillings={"glass_24": 800.0},
            hardware={"tilt_turn": 950.0},
            extras={"mosquito": 300.0}
        )
        self.assertEqual(resolved.profiles["REHAU_70"], 250.0)

    def test_31_resolved_material_prices_over_100_allowed(self):
        """31. ResolvedMaterialPrices allows more than 100 items per category."""
        large_dict = {f"profile_{i}": 100.0 for i in range(150)}
        resolved = ResolvedMaterialPrices(profiles=large_dict)
        self.assertEqual(len(resolved.profiles), 150)

    def test_32_resolved_material_prices_invalid_values(self):
        """32. ResolvedMaterialPrices rejects negative, NaN, and Infinity prices."""
        with self.assertRaises(ValidationError):
            ResolvedMaterialPrices(profiles={"REHAU": -5.0})
        with self.assertRaises(ValidationError):
            ResolvedMaterialPrices(profiles={"REHAU": float("nan")})
        with self.assertRaises(ValidationError):
            ResolvedMaterialPrices(profiles={"REHAU": float("inf")})
        # Empty or whitespace material ID
        with self.assertRaises(ValidationError):
            ResolvedMaterialPrices(profiles={"   ": 100.0})

    def test_33_pricing_context_uses_resolved_material_prices(self):
        """33. PricingContext expects and validates ResolvedMaterialPrices."""
        ctx = PricingContext(
            resolved_prices={
                "profiles": {"REHAU": 250.0},
                "fillings": {},
                "hardware": {},
                "extras": {}
            },
            additional_costs=[],
            commercial={"markup_rate": 0.0, "discount_rate": 0.0},
            tax_profile={"name": "VAT", "rate": 0.0, "included_in_price": False}
        )
        self.assertIsInstance(ctx.resolved_prices, ResolvedMaterialPrices)

    def test_34_material_pricing_overrides_limit_continues_to_apply(self):
        """34. MaterialPricingOverrides continues to reject over 100 items."""
        large_dict = {f"profile_{i}": 100.0 for i in range(101)}
        with self.assertRaises(ValidationError):
            MaterialPricingOverrides(profiles=large_dict)

if __name__ == "__main__":
    unittest.main()
