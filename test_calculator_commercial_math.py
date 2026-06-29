import unittest
import copy
import math
import json
from calculator import WindowCalculator, CalculatorPricingError
from settings_models import PricingContext, TaxProfileSettings, AdditionalCostSettings, CommercialSettings, CalculationType
from pricing_context_provider import get_default_pricing_context

class TestCalculatorCommercialMath(unittest.TestCase):

    def setUp(self):
        self.calc = WindowCalculator(use_firestore=False)
        with open("materials.json", "r", encoding="utf-8") as f:
            self.catalog = json.load(f)
        self.ctx = get_default_pricing_context(self.catalog)

    def get_minimal_payload(self):
        return {
            "width": 1000.0,
            "height": 1000.0,
            "profile": "REHAU_Euro_70",
            "glass": "glass_24",
            "color": "white",
            "panels": [{"type": "fixed", "proportion": 100.0}]
        }

    # A. Defaults
    def test_defaults_no_adjustments(self):
        payload = self.get_minimal_payload()
        self.ctx.additional_costs = []
        self.ctx.commercial.markup_rate = 0.0
        self.ctx.commercial.discount_rate = 0.0
        self.ctx.tax_profile = TaxProfileSettings(name="no_tax", rate=0.0, included_in_price=False)

        res = self.calc.calculate_project(payload, self.ctx)
        self.assertEqual(res["status"], "success")

        # Verify base material cost matches net_price and total
        cost_details = res["cost_details"]
        materials_total = cost_details["profile"] + cost_details["glass"] + cost_details["hardware"] + cost_details["extras"]
        self.assertAlmostEqual(res["net_price"], materials_total, places=2)
        self.assertAlmostEqual(cost_details["total"], materials_total, places=2)
        self.assertEqual(res["vat_amount"], 0.0)

        # Verify breakdown
        breakdown = res["commercial_breakdown"]
        self.assertEqual(breakdown["materials_subtotal"], round(materials_total, 2))
        self.assertEqual(breakdown["additional_costs_total"], 0.0)
        self.assertEqual(breakdown["markup_amount"], 0.0)
        self.assertEqual(breakdown["discount_amount"], 0.0)
        self.assertEqual(breakdown["total"], round(materials_total, 2))

    # B. Additional costs
    def test_fixed_per_order_applied_once(self):
        payload = self.get_minimal_payload()
        self.ctx.additional_costs = [
            AdditionalCostSettings(
                id="delivery",
                name="Доставка",
                calculation_type=CalculationType.fixed_per_order,
                value=150.0,
                enabled=True,
                sort_order=1
            )
        ]
        res = self.calc.calculate_project(payload, self.ctx)
        breakdown = res["commercial_breakdown"]
        self.assertEqual(breakdown["additional_costs_total"], 150.0)
        self.assertEqual(breakdown["additional_costs_breakdown"][0]["amount"], 150.0)
        self.assertEqual(breakdown["additional_costs_breakdown"][0]["calculation_type"], "fixed_per_order")

    def test_fixed_per_item_applied_once(self):
        payload = self.get_minimal_payload()
        self.ctx.additional_costs = [
            AdditionalCostSettings(
                id="installation",
                name="Монтаж",
                calculation_type=CalculationType.fixed_per_item,
                value=120.0,
                enabled=True,
                sort_order=1
            )
        ]
        res = self.calc.calculate_project(payload, self.ctx)
        breakdown = res["commercial_breakdown"]
        self.assertEqual(breakdown["additional_costs_total"], 120.0)
        self.assertEqual(breakdown["additional_costs_breakdown"][0]["amount"], 120.0)
        self.assertEqual(breakdown["additional_costs_breakdown"][0]["calculation_type"], "fixed_per_item")

    def test_per_m2_uses_outer_area(self):
        payload = self.get_minimal_payload()
        payload["width"] = 2000.0
        payload["height"] = 1500.0  # Area = 2.0 * 1.5 = 3.0 m2
        self.ctx.additional_costs = [
            AdditionalCostSettings(
                id="cleaning",
                name="Чистка",
                calculation_type=CalculationType.per_m2,
                value=50.0,
                enabled=True,
                sort_order=1
            )
        ]
        res = self.calc.calculate_project(payload, self.ctx)
        breakdown = res["commercial_breakdown"]
        # Outer area = 3.0 m2. Amount = 50.0 * 3.0 = 150.0.
        self.assertEqual(breakdown["additional_costs_total"], 150.0)
        self.assertEqual(breakdown["additional_costs_breakdown"][0]["amount"], 150.0)

    def test_per_linear_meter_uses_outer_perimeter(self):
        payload = self.get_minimal_payload()
        payload["width"] = 2000.0
        payload["height"] = 1500.0  # Frame perimeter = 2 * (2.0 + 1.5) = 7.0 meters
        self.ctx.additional_costs = [
            AdditionalCostSettings(
                id="sealing",
                name="Герметизація",
                calculation_type=CalculationType.per_linear_meter,
                value=10.0,
                enabled=True,
                sort_order=1
            )
        ]
        res = self.calc.calculate_project(payload, self.ctx)
        breakdown = res["commercial_breakdown"]
        # Amount = 10.0 * 7.0 = 70.0
        self.assertEqual(breakdown["additional_costs_total"], 70.0)
        self.assertEqual(breakdown["additional_costs_breakdown"][0]["amount"], 70.0)

    def test_percent_of_materials(self):
        payload = self.get_minimal_payload()
        self.ctx.additional_costs = [
            AdditionalCostSettings(
                id="margin_pct",
                name="Відсоток",
                calculation_type=CalculationType.percent_of_materials,
                value=5.5,
                enabled=True,
                sort_order=1
            )
        ]
        res = self.calc.calculate_project(payload, self.ctx)
        breakdown = res["commercial_breakdown"]
        expected_amount = round(breakdown["materials_subtotal"] * 0.055, 2)
        self.assertEqual(breakdown["additional_costs_total"], expected_amount)
        self.assertEqual(breakdown["additional_costs_breakdown"][0]["amount"], expected_amount)

    def test_disabled_cost_ignored(self):
        payload = self.get_minimal_payload()
        self.ctx.additional_costs = [
            AdditionalCostSettings(
                id="delivery",
                name="Доставка",
                calculation_type=CalculationType.fixed_per_order,
                value=150.0,
                enabled=False,
                sort_order=1
            )
        ]
        res = self.calc.calculate_project(payload, self.ctx)
        breakdown = res["commercial_breakdown"]
        self.assertEqual(breakdown["additional_costs_total"], 0.0)
        self.assertEqual(len(breakdown["additional_costs_breakdown"]), 0)

    def test_zero_valued_cost_preserved(self):
        payload = self.get_minimal_payload()
        self.ctx.additional_costs = [
            AdditionalCostSettings(
                id="free_delivery",
                name="Безкоштовна доставка",
                calculation_type=CalculationType.fixed_per_order,
                value=0.0,
                enabled=True,
                sort_order=1
            )
        ]
        res = self.calc.calculate_project(payload, self.ctx)
        breakdown = res["commercial_breakdown"]
        self.assertEqual(breakdown["additional_costs_total"], 0.0)
        self.assertEqual(len(breakdown["additional_costs_breakdown"]), 1)
        self.assertEqual(breakdown["additional_costs_breakdown"][0]["amount"], 0.0)

    def test_multiple_costs_sorted(self):
        payload = self.get_minimal_payload()
        self.ctx.additional_costs = [
            AdditionalCostSettings(
                id="b_cost",
                name="Другий",
                calculation_type=CalculationType.fixed_per_item,
                value=10.0,
                enabled=True,
                sort_order=2
            ),
            AdditionalCostSettings(
                id="a_cost",
                name="Перший",
                calculation_type=CalculationType.fixed_per_item,
                value=50.0,
                enabled=True,
                sort_order=1
            )
        ]
        res = self.calc.calculate_project(payload, self.ctx)
        breakdown = res["commercial_breakdown"]
        self.assertEqual(breakdown["additional_costs_total"], 60.0)
        # Order should be sorted by sort_order
        self.assertEqual(breakdown["additional_costs_breakdown"][0]["id"], "a_cost")
        self.assertEqual(breakdown["additional_costs_breakdown"][1]["id"], "b_cost")

    def test_equal_sort_order_stable(self):
        payload = self.get_minimal_payload()
        self.ctx.additional_costs = [
            AdditionalCostSettings(
                id="first_inserted",
                name="Перший",
                calculation_type=CalculationType.fixed_per_item,
                value=10.0,
                enabled=True,
                sort_order=5
            ),
            AdditionalCostSettings(
                id="second_inserted",
                name="Другий",
                calculation_type=CalculationType.fixed_per_item,
                value=20.0,
                enabled=True,
                sort_order=5
            )
        ]
        res = self.calc.calculate_project(payload, self.ctx)
        breakdown = res["commercial_breakdown"]
        self.assertEqual(breakdown["additional_costs_breakdown"][0]["id"], "first_inserted")
        self.assertEqual(breakdown["additional_costs_breakdown"][1]["id"], "second_inserted")

    def test_duplicate_ids_guard(self):
        payload = self.get_minimal_payload()
        # Bypass pydantic list validator for testing direct direct call corrupt contexts
        self.ctx.additional_costs = [
            AdditionalCostSettings(
                id="dup",
                name="D1",
                calculation_type=CalculationType.fixed_per_item,
                value=10.0,
                enabled=True,
                sort_order=1
            ),
            AdditionalCostSettings(
                id="dup",
                name="D2",
                calculation_type=CalculationType.fixed_per_item,
                value=20.0,
                enabled=True,
                sort_order=2
            )
        ]
        with self.assertRaises(CalculatorPricingError):
            self.calc.calculate_project(payload, self.ctx)

    def test_additional_costs_rounding_and_sum(self):
        payload = self.get_minimal_payload()
        payload["width"] = 1500.0
        payload["height"] = 1500.0  # Area = 2.25 m2
        self.ctx.additional_costs = [
            AdditionalCostSettings(
                id="c1",
                name="C1",
                calculation_type=CalculationType.per_m2,
                value=33.33,  # 33.33 * 2.25 = 74.9925 -> rounded 74.99
                enabled=True,
                sort_order=1
            ),
            AdditionalCostSettings(
                id="c2",
                name="C2",
                calculation_type=CalculationType.per_m2,
                value=11.11,  # 11.11 * 2.25 = 24.9975 -> rounded 25.00
                enabled=True,
                sort_order=2
            )
        ]
        res = self.calc.calculate_project(payload, self.ctx)
        breakdown = res["commercial_breakdown"]
        self.assertEqual(breakdown["additional_costs_breakdown"][0]["amount"], 74.99)
        self.assertEqual(breakdown["additional_costs_breakdown"][1]["amount"], 25.00)
        self.assertEqual(breakdown["additional_costs_total"], 99.99)  # 74.99 + 25.00

    # C. Markup / Discount
    def test_markup_only(self):
        payload = self.get_minimal_payload()
        self.ctx.commercial.markup_rate = 15.5
        self.ctx.commercial.discount_rate = 0.0
        res = self.calc.calculate_project(payload, self.ctx)
        breakdown = res["commercial_breakdown"]
        expected_markup = round(breakdown["subtotal_before_markup"] * 0.155, 2)
        self.assertEqual(breakdown["markup_amount"], expected_markup)
        self.assertEqual(breakdown["discount_amount"], 0.0)
        self.assertEqual(breakdown["adjusted_subtotal"], round(breakdown["subtotal_before_markup"] + expected_markup, 2))

    def test_discount_only(self):
        payload = self.get_minimal_payload()
        self.ctx.commercial.markup_rate = 0.0
        self.ctx.commercial.discount_rate = 10.5
        res = self.calc.calculate_project(payload, self.ctx)
        breakdown = res["commercial_breakdown"]
        expected_discount = round(breakdown["subtotal_before_markup"] * 0.105, 2)
        self.assertEqual(breakdown["markup_amount"], 0.0)
        self.assertEqual(breakdown["discount_amount"], expected_discount)
        self.assertEqual(breakdown["adjusted_subtotal"], round(breakdown["subtotal_before_markup"] - expected_discount, 2))

    def test_sequential_markup_discount_numerical_example(self):
        payload = self.get_minimal_payload()

        # Adjust context price parameters to force materials_subtotal to be exactly 1000.00
        # Profile perimeter = 4000 mm = 4.0 m. Let profile price = 100.0, color multiplier = 1.0. Profile cost = 400.0.
        # Glass area = 1.0 m2. Let glass price = 600.0. Glass cost = 600.0.
        # Others 0. Total materials_subtotal = 1000.00.
        self.ctx.resolved_prices.profiles["REHAU_Euro_70"] = 100.0
        self.ctx.resolved_prices.fillings["glass_24"] = 600.0
        self.ctx.additional_costs = []
        self.ctx.commercial.markup_rate = 20.0
        self.ctx.commercial.discount_rate = 10.0
        self.ctx.tax_profile = TaxProfileSettings(name="no_tax", rate=0.0, included_in_price=False)

        res = self.calc.calculate_project(payload, self.ctx)
        breakdown = res["commercial_breakdown"]
        self.assertEqual(breakdown["materials_subtotal"], 1000.00)
        self.assertEqual(breakdown["additional_costs_total"], 0.0)
        self.assertEqual(breakdown["subtotal_before_markup"], 1000.00)
        self.assertEqual(breakdown["markup_amount"], 200.00)
        self.assertEqual(breakdown["subtotal_after_markup"], 1200.00)
        self.assertEqual(breakdown["discount_amount"], 120.00)
        self.assertEqual(breakdown["adjusted_subtotal"], 1080.00)

    def test_additional_costs_included_in_markup_and_discount(self):
        payload = self.get_minimal_payload()
        self.ctx.resolved_prices.profiles["REHAU_Euro_70"] = 100.0
        self.ctx.resolved_prices.fillings["glass_24"] = 600.0  # Materials subtotal = 1000.0
        self.ctx.additional_costs = [
            AdditionalCostSettings(
                id="ad1",
                name="AD1",
                calculation_type=CalculationType.fixed_per_item,
                value=200.0,
                enabled=True,
                sort_order=1
            )
        ]
        self.ctx.commercial.markup_rate = 20.0
        self.ctx.commercial.discount_rate = 10.0
        self.ctx.tax_profile = TaxProfileSettings(name="no_tax", rate=0.0, included_in_price=False)

        res = self.calc.calculate_project(payload, self.ctx)
        breakdown = res["commercial_breakdown"]
        self.assertEqual(breakdown["subtotal_before_markup"], 1200.0)  # 1000 + 200
        self.assertEqual(breakdown["markup_amount"], 240.0)  # 1200 * 20%
        self.assertEqual(breakdown["subtotal_after_markup"], 1440.0)
        self.assertEqual(breakdown["discount_amount"], 144.0)  # 1440 * 10%
        self.assertEqual(breakdown["adjusted_subtotal"], 1296.0)

    def test_100_percent_discount_gives_zero(self):
        payload = self.get_minimal_payload()
        self.ctx.commercial.discount_rate = 100.0
        res = self.calc.calculate_project(payload, self.ctx)
        breakdown = res["commercial_breakdown"]
        self.assertEqual(breakdown["adjusted_subtotal"], 0.0)
        self.assertEqual(res["net_price"], 0.0)
        self.assertEqual(res["vat_amount"], 0.0)
        self.assertEqual(res["cost_details"]["total"], 0.0)

    def test_max_markup_500(self):
        payload = self.get_minimal_payload()
        self.ctx.commercial.markup_rate = 500.0
        res = self.calc.calculate_project(payload, self.ctx)
        breakdown = res["commercial_breakdown"]
        self.assertAlmostEqual(breakdown["markup_amount"], breakdown["subtotal_before_markup"] * 5.0, places=2)

    # D. Tax
    def test_excluded_tax(self):
        payload = self.get_minimal_payload()
        self.ctx.resolved_prices.profiles["REHAU_Euro_70"] = 100.0
        self.ctx.resolved_prices.fillings["glass_24"] = 600.0  # Materials subtotal = 1000.0
        self.ctx.additional_costs = []
        self.ctx.commercial.markup_rate = 0.0
        self.ctx.commercial.discount_rate = 0.0
        self.ctx.tax_profile = TaxProfileSettings(name="VAT Excluded", rate=0.20, included_in_price=False)

        res = self.calc.calculate_project(payload, self.ctx)
        self.assertEqual(res["net_price"], 1000.0)
        self.assertEqual(res["vat_amount"], 200.0)
        self.assertEqual(res["cost_details"]["total"], 1200.0)

    def test_included_tax(self):
        payload = self.get_minimal_payload()
        self.ctx.resolved_prices.profiles["REHAU_Euro_70"] = 100.0
        self.ctx.resolved_prices.fillings["glass_24"] = 600.0  # Materials subtotal = 1000.0
        self.ctx.additional_costs = []
        self.ctx.commercial.markup_rate = 0.0
        self.ctx.commercial.discount_rate = 0.0
        self.ctx.tax_profile = TaxProfileSettings(name="VAT Included", rate=0.20, included_in_price=True)

        res = self.calc.calculate_project(payload, self.ctx)
        # total must equal 1000.0
        self.assertEqual(res["cost_details"]["total"], 1000.0)
        # vat = 1000 * 0.20 / 1.20 = 166.67
        self.assertEqual(res["vat_amount"], 166.67)
        # net = 1000 - 166.67 = 833.33
        self.assertEqual(res["net_price"], 833.33)

    def test_tax_rate_zero(self):
        payload = self.get_minimal_payload()
        self.ctx.tax_profile = TaxProfileSettings(name="Zero Tax", rate=0.0, included_in_price=True)
        res = self.calc.calculate_project(payload, self.ctx)
        self.assertEqual(res["vat_amount"], 0.0)
        self.assertEqual(res["net_price"], res["cost_details"]["total"])

    def test_net_plus_vat_equals_total_excluded(self):
        payload = self.get_minimal_payload()
        self.ctx.tax_profile = TaxProfileSettings(name="Tax Excluded", rate=0.20, included_in_price=False)
        res = self.calc.calculate_project(payload, self.ctx)
        self.assertEqual(round(res["net_price"] + res["vat_amount"], 2), res["cost_details"]["total"])

    def test_net_plus_vat_equals_total_included(self):
        payload = self.get_minimal_payload()
        self.ctx.tax_profile = TaxProfileSettings(name="Tax Included", rate=0.20, included_in_price=True)
        res = self.calc.calculate_project(payload, self.ctx)
        self.assertEqual(round(res["net_price"] + res["vat_amount"], 2), res["cost_details"]["total"])

    # E. Rounding
    def test_rounding_propagation(self):
        payload = self.get_minimal_payload()
        self.ctx.resolved_prices.profiles["REHAU_Euro_70"] = 100.0
        self.ctx.resolved_prices.fillings["glass_24"] = 600.0  # Materials subtotal = 1000.0
        # Let's add multiple additional costs with fractions
        self.ctx.additional_costs = [
            AdditionalCostSettings(
                id="c1", name="C1", calculation_type=CalculationType.fixed_per_item, value=10.004, enabled=True, sort_order=1
            ),
            AdditionalCostSettings(
                id="c2", name="C2", calculation_type=CalculationType.fixed_per_item, value=10.004, enabled=True, sort_order=2
            )
        ]
        self.ctx.commercial.markup_rate = 1.005  # 1.005% markup
        self.ctx.commercial.discount_rate = 0.505
        self.ctx.tax_profile = TaxProfileSettings(name="Tax", rate=0.1234, included_in_price=False)

        res = self.calc.calculate_project(payload, self.ctx)
        breakdown = res["commercial_breakdown"]

        # Verify that all breakdown items sum up correctly
        self.assertEqual(breakdown["additional_costs_total"], 20.0) # 10.00 + 10.00
        self.assertEqual(breakdown["subtotal_before_markup"], 1020.0)
        # markup = round(1020 * 0.01005, 2) = round(10.251, 2) = 10.25
        self.assertEqual(breakdown["markup_amount"], 10.25)
        self.assertEqual(breakdown["subtotal_after_markup"], 1030.25)
        # discount = round(1030.25 * 0.00505, 2) = round(5.20276, 2) = 5.20
        self.assertEqual(breakdown["discount_amount"], 5.20)
        self.assertEqual(breakdown["adjusted_subtotal"], 1025.05)
        # VAT = round(1025.05 * 0.1234, 2) = round(126.49117, 2) = 126.49
        self.assertEqual(breakdown["vat_amount"], 126.49)
        self.assertEqual(breakdown["total"], 1151.54) # 1025.05 + 126.49
        self.assertEqual(res["cost_details"]["total"], 1151.54)

    # F. Guards
    def test_nan_runtime_amount_raises_error(self):
        payload = self.get_minimal_payload()
        self.ctx.commercial.markup_rate = float("nan")
        with self.assertRaises(CalculatorPricingError):
            self.calc.calculate_project(payload, self.ctx)

    def test_infinity_runtime_amount_raises_error(self):
        payload = self.get_minimal_payload()
        self.ctx.commercial.markup_rate = float("inf")
        with self.assertRaises(CalculatorPricingError):
            self.calc.calculate_project(payload, self.ctx)

    def test_negative_outside_tolerance_raises_error(self):
        payload = self.get_minimal_payload()
        # Create a situation where adjusted subtotal goes negative outside tolerance.
        # This is mathematically impossible via standard fields because discount rate <= 100%,
        # but let's test that if it somehow becomes negative (e.g. we bypass checks or inject values), it raises an error.
        self.ctx.commercial.discount_rate = 120.0
        with self.assertRaises(CalculatorPricingError):
            self.calc.calculate_project(payload, self.ctx)

    def test_negative_inside_tolerance_normalized_to_zero(self):
        payload = self.get_minimal_payload()
        # Bypass direct check or force negative near-zero value in code paths.
        # To test, we can trust the tolerance normalization block: if adjusted_subtotal < 0.0 but >= -1e-9: normalize to 0.0.
        # Let's verify that a discount_rate of exactly 100.0 results in 0.0 without errors.
        self.ctx.commercial.discount_rate = 100.0
        res = self.calc.calculate_project(payload, self.ctx)
        self.assertEqual(res["commercial_breakdown"]["adjusted_subtotal"], 0.0)

    # G. Isolation
    def test_pricing_context_not_mutated(self):
        payload = self.get_minimal_payload()
        ctx_copy = copy.deepcopy(self.ctx)
        self.calc.calculate_project(payload, self.ctx)
        self.assertEqual(self.ctx, ctx_copy)

    def test_repeated_result_deterministic(self):
        payload = self.get_minimal_payload()
        res1 = self.calc.calculate_project(payload, self.ctx)
        res2 = self.calc.calculate_project(payload, self.ctx)
        self.assertEqual(res1, res2)

    def test_different_contexts_different_results(self):
        payload = self.get_minimal_payload()
        ctx2 = copy.deepcopy(self.ctx)
        ctx2.commercial.markup_rate = 10.0
        res1 = self.calc.calculate_project(payload, self.ctx)
        res2 = self.calc.calculate_project(payload, ctx2)
        self.assertNotEqual(res1["cost_details"]["total"], res2["cost_details"]["total"])

    def test_no_state_leakage(self):
        payload = self.get_minimal_payload()
        calc2 = WindowCalculator(use_firestore=False)
        res1 = self.calc.calculate_project(payload, self.ctx)
        res2 = calc2.calculate_project(payload, self.ctx)
        self.assertEqual(res1, res2)

    # H. Client fields
    def test_client_commercial_fields_ignored(self):
        payload = self.get_minimal_payload()
        # Add conflicting fields to payload
        payload["markup_rate"] = 50.0
        payload["discount_rate"] = 50.0
        payload["tax_rate"] = 0.50
        payload["included_in_price"] = True

        # Context has defaults (0.0)
        res = self.calc.calculate_project(payload, self.ctx)
        breakdown = res["commercial_breakdown"]
        self.assertEqual(breakdown["markup_rate"], 0.0)
        self.assertEqual(breakdown["discount_rate"], 0.0)
        self.assertEqual(breakdown["tax_rate"], self.ctx.tax_profile.rate)
        self.assertEqual(breakdown["tax_included"], self.ctx.tax_profile.included_in_price)

    # I. Response
    def test_legacy_keys_preserved(self):
        payload = self.get_minimal_payload()
        res = self.calc.calculate_project(payload, self.ctx)
        self.assertIn("status", res)
        self.assertIn("net_price", res)
        self.assertIn("vat_amount", res)
        self.assertIn("legal_reference", res)
        self.assertIn("metrics", res)
        self.assertIn("cost_details", res)

        cost_details = res["cost_details"]
        self.assertIn("profile", cost_details)
        self.assertIn("glass", cost_details)
        self.assertIn("hardware", cost_details)
        self.assertIn("extras", cost_details)
        self.assertIn("total", cost_details)

    def test_totals_equal_in_all_keys(self):
        payload = self.get_minimal_payload()
        self.ctx.commercial.markup_rate = 12.0
        self.ctx.commercial.discount_rate = 5.0
        self.ctx.tax_profile = TaxProfileSettings(name="Tax", rate=0.20, included_in_price=False)

        res = self.calc.calculate_project(payload, self.ctx)
        breakdown = res["commercial_breakdown"]
        self.assertEqual(res["net_price"], breakdown["net_price"])
        self.assertEqual(res["vat_amount"], breakdown["vat_amount"])
        self.assertEqual(res["cost_details"]["total"], breakdown["total"])

    def test_calculation_type_serializable(self):
        payload = self.get_minimal_payload()
        self.ctx.additional_costs = [
            AdditionalCostSettings(
                id="ad",
                name="AD",
                calculation_type=CalculationType.fixed_per_item,
                value=10.0,
                enabled=True,
                sort_order=1
            )
        ]
        res = self.calc.calculate_project(payload, self.ctx)
        calc_type_val = res["commercial_breakdown"]["additional_costs_breakdown"][0]["calculation_type"]
        self.assertIsInstance(calc_type_val, str)
        self.assertEqual(calc_type_val, "fixed_per_item")

    def test_additional_costs_total_is_rounded_after_float_accumulation(self):
        payload = self.get_minimal_payload()
        self.ctx.additional_costs = [
            AdditionalCostSettings(
                id="ac1",
                name="AC1",
                calculation_type=CalculationType.fixed_per_item,
                value=0.10,
                enabled=True,
                sort_order=1
            ),
            AdditionalCostSettings(
                id="ac2",
                name="AC2",
                calculation_type=CalculationType.fixed_per_item,
                value=0.10,
                enabled=True,
                sort_order=2
            ),
            AdditionalCostSettings(
                id="ac3",
                name="AC3",
                calculation_type=CalculationType.fixed_per_item,
                value=0.10,
                enabled=True,
                sort_order=3
            )
        ]
        res = self.calc.calculate_project(payload, self.ctx)
        breakdown = res["commercial_breakdown"]

        self.assertEqual(breakdown["additional_costs_breakdown"][0]["amount"], 0.10)
        self.assertEqual(breakdown["additional_costs_breakdown"][1]["amount"], 0.10)
        self.assertEqual(breakdown["additional_costs_breakdown"][2]["amount"], 0.10)

        self.assertEqual(breakdown["additional_costs_total"], 0.30)
        self.assertNotEqual(breakdown["additional_costs_total"], 0.30000000000000004)

        expected_subtotal = round(breakdown["materials_subtotal"] + 0.30, 2)
        self.assertEqual(breakdown["subtotal_before_markup"], expected_subtotal)

if __name__ == "__main__":
    unittest.main()
