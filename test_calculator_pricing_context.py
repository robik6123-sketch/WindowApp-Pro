import unittest
import copy
import math
import json
from calculator import (
    WindowCalculator,
    CalculatorPricingError,
    UnknownMaterialError,
    MissingResolvedPriceError
)
from pricing_context_provider import get_default_pricing_context
from settings_models import PricingContext, TaxProfileSettings

class TestCalculatorPricingContext(unittest.TestCase):

    def setUp(self):
        self.calc = WindowCalculator(use_firestore=False)
        # Load production materials
        with open("materials.json", "r", encoding="utf-8") as f:
            self.catalog = json.load(f)
        self.ctx = get_default_pricing_context(self.catalog)

    def get_minimal_payload(self):
        return {
            "width": 1000,
            "height": 1000,
            "profile": "REHAU_Euro_70",
            "glass": "glass_24",
            "color": "white",
            "panels": [{"type": "fixed", "proportion": 100.0}]
        }

    def test_calculate_project_requires_pricing_context(self):
        payload = self.get_minimal_payload()
        with self.assertRaises(TypeError):
            self.calc.calculate_project(payload)

    def test_profile_price_resolved_from_context(self):
        payload = self.get_minimal_payload()
        self.ctx.resolved_prices.profiles["REHAU_Euro_70"] = 150.0
        res = self.calc.calculate_project(payload, self.ctx)
        self.assertEqual(res["status"], "success")
        # Perimeter = 4000 mm = 4.0 m. Price per m = 150.0. Color mult = 1.0. Total profile = 600.0.
        self.assertEqual(res["cost_details"]["profile"], 600.0)

    def test_filling_price_resolved_from_context(self):
        payload = self.get_minimal_payload()
        self.ctx.resolved_prices.fillings["glass_24"] = 350.0
        res = self.calc.calculate_project(payload, self.ctx)
        self.assertEqual(res["status"], "success")
        # Area = 1.0 m2. Price per m2 = 350.0. Total glass = 350.0.
        self.assertEqual(res["cost_details"]["glass"], 350.0)

    def test_hardware_price_for_turn(self):
        payload = self.get_minimal_payload()
        payload["panels"] = [{"type": "turn", "proportion": 100.0}]
        self.ctx.resolved_prices.hardware["turn"] = 250.0
        res = self.calc.calculate_project(payload, self.ctx)
        self.assertEqual(res["cost_details"]["hardware"], 250.0)

    def test_hardware_price_for_tilt_turn(self):
        payload = self.get_minimal_payload()
        payload["panels"] = [{"type": "tilt_turn", "proportion": 100.0}]
        self.ctx.resolved_prices.hardware["tilt_turn"] = 450.0
        res = self.calc.calculate_project(payload, self.ctx)
        self.assertEqual(res["cost_details"]["hardware"], 450.0)

    def test_hardware_price_for_door(self):
        payload = self.get_minimal_payload()
        payload["panels"] = [{"type": "door", "proportion": 100.0}]
        self.ctx.resolved_prices.hardware["door_lock_strip"] = 950.0
        res = self.calc.calculate_project(payload, self.ctx)
        self.assertEqual(res["cost_details"]["hardware"], 950.0)

    def test_aliases_left_right(self):
        payload = self.get_minimal_payload()
        payload["panels"] = [{"type": "turn_left", "proportion": 100.0}]
        self.ctx.resolved_prices.hardware["turn"] = 250.0
        res = self.calc.calculate_project(payload, self.ctx)
        self.assertEqual(res["cost_details"]["hardware"], 250.0)

        payload["panels"] = [{"type": "tilt_turn_right", "proportion": 100.0}]
        self.ctx.resolved_prices.hardware["tilt_turn"] = 450.0
        res = self.calc.calculate_project(payload, self.ctx)
        self.assertEqual(res["cost_details"]["hardware"], 450.0)

    def test_fixed_hardware_cost_zero(self):
        payload = self.get_minimal_payload()
        payload["panels"] = [{"type": "fixed", "proportion": 100.0}]
        # Delete hardware prices from context to show lookup isn't even done
        self.ctx.resolved_prices.hardware.clear()
        res = self.calc.calculate_project(payload, self.ctx)
        self.assertEqual(res["cost_details"]["hardware"], 0.0)

    def test_unknown_panel_type_falls_back_to_turn(self):
        payload = self.get_minimal_payload()
        payload["panels"] = [{"type": "some_unknown_type", "proportion": 100.0}]
        self.ctx.resolved_prices.hardware["turn"] = 250.0
        res = self.calc.calculate_project(payload, self.ctx)
        self.assertEqual(res["cost_details"]["hardware"], 250.0)

    def test_bending_price_from_context(self):
        payload = self.get_minimal_payload()
        payload["type"] = "arched"
        payload["arc_height"] = 500
        self.ctx.resolved_prices.extras["bending"] = 300.0
        res = self.calc.calculate_project(payload, self.ctx)
        # Bending = (arc_len / 1000.0) * base_bending * bend_multiplier
        # arc_len of semicircle = pi * 500 = 1570.79 mm = 1.57 m.
        # 1.57 * 300 * 1.5 = 706.85 approx.
        self.assertGreater(res["cost_details"]["extras"], 700.0)

    def test_mosquito_net_price_from_context(self):
        payload = self.get_minimal_payload()
        payload["panels"] = [{"type": "fixed", "proportion": 50.0}, {"type": "turn", "proportion": 50.0, "mosquito": True}]
        self.ctx.resolved_prices.extras["mosquito_net"] = 200.0
        res = self.calc.calculate_project(payload, self.ctx)
        # net_area = (500 * 1000) / 1_000_000 = 0.5 m2. Cost = 0.5 * 200 = 100.0.
        self.assertIn("extras", res["cost_details"])

    def test_sill_price_from_context(self):
        payload = self.get_minimal_payload()
        payload["sill_length"] = 1200
        payload["sill_width"] = 200
        self.ctx.resolved_prices.extras["sill"] = 500.0
        res = self.calc.calculate_project(payload, self.ctx)
        # Area = 1.2 * 0.2 = 0.24 m2. Cost = 0.24 * 500 = 120.0.
        self.assertEqual(res["cost_details"]["extras"], 120.0)

    def test_window_board_price_from_context_plastolit(self):
        payload = self.get_minimal_payload()
        payload["window_board"] = "window_board_plastolit_matte"
        payload["window_board_length"] = 1200
        payload["window_board_depth"] = 300
        self.ctx.resolved_prices.extras["window_board_plastolit_matte"] = 800.0
        res = self.calc.calculate_project(payload, self.ctx)
        # Area = 1.2 * 0.3 = 0.36 m2. Cost = 0.36 * 800 = 288.0.
        self.assertEqual(res["cost_details"]["extras"], 288.0)

    def test_context_price_zero_preserved(self):
        payload = self.get_minimal_payload()
        self.ctx.resolved_prices.profiles["REHAU_Euro_70"] = 0.0
        res = self.calc.calculate_project(payload, self.ctx)
        self.assertEqual(res["cost_details"]["profile"], 0.0)

    def test_missing_profile_price_raises_missing_resolved_price_error(self):
        payload = self.get_minimal_payload()
        del self.ctx.resolved_prices.profiles["REHAU_Euro_70"]
        with self.assertRaises(MissingResolvedPriceError):
            self.calc.calculate_project(payload, self.ctx)

    def test_missing_filling_price_raises_missing_resolved_price_error(self):
        payload = self.get_minimal_payload()
        del self.ctx.resolved_prices.fillings["glass_24"]
        with self.assertRaises(MissingResolvedPriceError):
            self.calc.calculate_project(payload, self.ctx)

    def test_missing_hardware_price_raises_missing_resolved_price_error(self):
        payload = self.get_minimal_payload()
        payload["panels"] = [{"type": "turn", "proportion": 100.0}]
        del self.ctx.resolved_prices.hardware["turn"]
        with self.assertRaises(MissingResolvedPriceError):
            self.calc.calculate_project(payload, self.ctx)

    def test_missing_extra_price_raises_missing_resolved_price_error(self):
        payload = self.get_minimal_payload()
        payload["sill_length"] = 1000
        payload["sill_width"] = 200
        del self.ctx.resolved_prices.extras["sill"]
        with self.assertRaises(MissingResolvedPriceError):
            self.calc.calculate_project(payload, self.ctx)

    def test_unknown_profile_raises_unknown_material_error(self):
        payload = self.get_minimal_payload()
        payload["profile"] = "unknown_prof_id"
        with self.assertRaises(UnknownMaterialError):
            self.calc.calculate_project(payload, self.ctx)

    def test_unknown_filling_raises_unknown_material_error(self):
        payload = self.get_minimal_payload()
        payload["glass"] = "unknown_glass_id"
        with self.assertRaises(UnknownMaterialError):
            self.calc.calculate_project(payload, self.ctx)

    def test_unknown_color_raises_unknown_material_error(self):
        payload = self.get_minimal_payload()
        payload["color"] = "unknown_color_id"
        with self.assertRaises(UnknownMaterialError):
            self.calc.calculate_project(payload, self.ctx)

    def test_client_custom_prices_ignored(self):
        payload = self.get_minimal_payload()
        payload["custom_prices"] = {"profile_price": 999.0, "glass_price": 999.0}
        self.ctx.resolved_prices.profiles["REHAU_Euro_70"] = 100.0
        self.ctx.resolved_prices.fillings["glass_24"] = 200.0
        res = self.calc.calculate_project(payload, self.ctx)
        # Profile cost = 4.0 * 100.0 = 400.0
        self.assertEqual(res["cost_details"]["profile"], 400.0)

    def test_client_tax_profile_id_ignored(self):
        payload = self.get_minimal_payload()
        payload["tax_profile_id"] = "vat_ukraine" # In materials, this is 20%
        # Our context tax profile is "no_tax" (0%)
        res = self.calc.calculate_project(payload, self.ctx)
        self.assertEqual(res["vat_amount"], 0.0)

    def test_trusted_tax_profile_determines_vat(self):
        payload = self.get_minimal_payload()
        self.ctx.tax_profile = TaxProfileSettings(name="ПДВ (Україна)", rate=0.20, included_in_price=False)
        res = self.calc.calculate_project(payload, self.ctx)
        subtotal = res["net_price"]
        self.assertAlmostEqual(res["vat_amount"], subtotal * 0.20, places=2)

    def test_no_tax_context(self):
        payload = self.get_minimal_payload()
        self.ctx.tax_profile = TaxProfileSettings(name="Без податку", rate=0.0, included_in_price=False)
        res = self.calc.calculate_project(payload, self.ctx)
        self.assertEqual(res["vat_amount"], 0.0)

    def test_included_in_price_does_not_change_formula(self):
        payload = self.get_minimal_payload()
        self.ctx.tax_profile = TaxProfileSettings(name="ПДВ", rate=0.20, included_in_price=True)
        res1 = self.calc.calculate_project(payload, self.ctx)
        self.ctx.tax_profile = TaxProfileSettings(name="ПДВ", rate=0.20, included_in_price=False)
        res2 = self.calc.calculate_project(payload, self.ctx)
        self.assertEqual(res1["vat_amount"], res2["vat_amount"])
        self.assertEqual(res1["cost_details"]["total"], res2["cost_details"]["total"])

    def test_known_legal_reference_mapping(self):
        payload = self.get_minimal_payload()
        # "Платник ПДВ (20%)" with 0.20 exists in self.calc.taxes (loaded from tax_profiles.json)
        self.ctx.tax_profile = TaxProfileSettings(name="Платник ПДВ (20%)", rate=0.20, included_in_price=False)
        res = self.calc.calculate_project(payload, self.ctx)
        self.assertEqual(res["legal_reference"], "Пункт 193.1 ПКУ")

    def test_unmatched_legal_reference_empty_string(self):
        payload = self.get_minimal_payload()
        self.ctx.tax_profile = TaxProfileSettings(name="Some Custom Tax", rate=0.15, included_in_price=False)
        res = self.calc.calculate_project(payload, self.ctx)
        self.assertEqual(res["legal_reference"], "")

    def test_color_multiplier_ranges(self):
        payload = self.get_minimal_payload()

        # multiplier = 0.0
        self.calc.materials["colors"]["white"]["price_multiplier"] = 0.0
        res = self.calc.calculate_project(payload, self.ctx)
        self.assertEqual(res["cost_details"]["profile"], 0.0)

        # multiplier = 0.5
        self.calc.materials["colors"]["white"]["price_multiplier"] = 0.5
        res = self.calc.calculate_project(payload, self.ctx)
        # 4.0 * 250.0 * 0.5 = 500.0
        self.assertEqual(res["cost_details"]["profile"], 500.0)

        # multiplier = 1.0
        self.calc.materials["colors"]["white"]["price_multiplier"] = 1.0
        res = self.calc.calculate_project(payload, self.ctx)
        self.assertEqual(res["cost_details"]["profile"], 1000.0)

        # multiplier = 1.5
        self.calc.materials["colors"]["white"]["price_multiplier"] = 1.5
        res = self.calc.calculate_project(payload, self.ctx)
        self.assertEqual(res["cost_details"]["profile"], 1500.0)

    def test_invalid_color_multipliers(self):
        payload = self.get_minimal_payload()

        # bool
        self.calc.materials["colors"]["white"]["price_multiplier"] = True
        with self.assertRaises(CalculatorPricingError):
            self.calc.calculate_project(payload, self.ctx)

        # NaN
        self.calc.materials["colors"]["white"]["price_multiplier"] = float("nan")
        with self.assertRaises(CalculatorPricingError):
            self.calc.calculate_project(payload, self.ctx)

        # Infinity
        self.calc.materials["colors"]["white"]["price_multiplier"] = float("inf")
        with self.assertRaises(CalculatorPricingError):
            self.calc.calculate_project(payload, self.ctx)

        # negative
        self.calc.materials["colors"]["white"]["price_multiplier"] = -1.2
        with self.assertRaises(CalculatorPricingError):
            self.calc.calculate_project(payload, self.ctx)

        # missing
        del self.calc.materials["colors"]["white"]["price_multiplier"]
        with self.assertRaises(CalculatorPricingError):
            self.calc.calculate_project(payload, self.ctx)

    def test_pricing_context_not_mutated(self):
        payload = self.get_minimal_payload()
        ctx_copy = copy.deepcopy(self.ctx)
        self.calc.calculate_project(payload, self.ctx)
        self.assertEqual(self.ctx, ctx_copy)

    def test_source_catalog_not_mutated(self):
        payload = self.get_minimal_payload()
        catalog_copy = copy.deepcopy(self.calc.materials)
        self.calc.calculate_project(payload, self.ctx)
        self.assertEqual(self.calc.materials, catalog_copy)

    def test_no_state_leakage_between_calls(self):
        payload = self.get_minimal_payload()
        ctx1 = get_default_pricing_context(self.catalog)
        ctx2 = get_default_pricing_context(self.catalog)
        ctx1.resolved_prices.profiles["REHAU_Euro_70"] = 100.0
        ctx2.resolved_prices.profiles["REHAU_Euro_70"] = 200.0
        res1 = self.calc.calculate_project(payload, ctx1)
        res2 = self.calc.calculate_project(payload, ctx2)
        self.assertEqual(res1["cost_details"]["profile"], 400.0)
        self.assertEqual(res2["cost_details"]["profile"], 800.0)

    def test_calculator_imports_isolation(self):
        with open("calculator.py", "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("import main", content)
        self.assertNotIn("pricing_context_provider", content)
        self.assertNotIn("repository", content)

if __name__ == "__main__":
    unittest.main()
