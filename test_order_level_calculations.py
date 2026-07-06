import os
import unittest
import math
from unittest.mock import patch, MagicMock

# Prevent Firebase/Firestore initialization during import
env_patcher = patch.dict(os.environ, {"USE_FIRESTORE": "false", "GOOGLE_APPLICATION_CREDENTIALS_JSON": ""})
cert_patcher = patch("firebase_admin.credentials.Certificate")
init_patcher = patch("firebase_admin.initialize_app")
client_patcher = patch("firebase_admin.firestore.client")

env_patcher.start()
cert_patcher.start()
init_patcher.start()
client_patcher.start()

import main
from main import app, verify_firebase_token, calculate_order_commercials
from calculator import apply_commercial_adjustments, WindowCalculator
from settings_models import PricingContext, AdditionalCostSettings, CommercialSettings, TaxProfileSettings, CalculationType, ResolvedMaterialPrices
from pdf_generator import generate_cart_pdf

# Immediately stop patchers
cert_patcher.stop()
init_patcher.stop()
client_patcher.stop()
env_patcher.stop()

from fastapi.testclient import TestClient
client = TestClient(app)

class TestOrderLevelCalculations(unittest.TestCase):

    def setUp(self):
        app.dependency_overrides.clear()
        self.real_use_firestore = main.USE_FIRESTORE
        main.USE_FIRESTORE = True

    def tearDown(self):
        app.dependency_overrides.clear()
        main.USE_FIRESTORE = self.real_use_firestore

    def get_test_context(self, with_delivery=True, tax_included=False, rate=0.20):
        costs = [
            AdditionalCostSettings(
                id="installation",
                name="Монтаж",
                calculation_type=CalculationType.fixed_per_item,
                value=500.0,
                enabled=True,
                sort_order=1
            )
        ]
        if with_delivery:
            costs.append(
                AdditionalCostSettings(
                    id="delivery",
                    name="Доставка",
                    calculation_type=CalculationType.fixed_per_order,
                    value=100.0,
                    enabled=True,
                    sort_order=2
                )
            )

        resolved = ResolvedMaterialPrices(
            profiles={"REHAU_Euro_70": 250.0},
            fillings={"glass_24": 400.0},
            hardware={"pvc_window_hardware": 1200.0},
            extras={"window_board_plastolit_matte": 350.0}
        )

        return PricingContext(
            commercial=CommercialSettings(markup_rate=10.0, discount_rate=5.0),
            tax_profile=TaxProfileSettings(name="ПДВ", rate=rate, included_in_price=tax_included),
            additional_costs=costs,
            resolved_prices=resolved
        )

    def test_shared_commercial_helper_preserves_outputs(self):
        """1. Verify apply_commercial_adjustments returns identical structure to calculate_project's commercial breakdown"""
        ctx = self.get_test_context(with_delivery=False)
        calc_engine = WindowCalculator(use_firestore=False)

        # Run a calculation on a dummy frame
        payload = {"width": 1000, "height": 1000}
        res = calc_engine.calculate_project(payload, ctx)
        cbd = res["commercial_breakdown"]

        # Now run helper manually
        adj = apply_commercial_adjustments(
            materials_subtotal=cbd["materials_subtotal"],
            additional_costs_total=cbd["additional_costs_total"],
            additional_costs_breakdown=cbd["additional_costs_breakdown"],
            commercial_settings=ctx.commercial,
            tax_profile=ctx.tax_profile
        )

        for key in cbd:
            self.assertEqual(cbd[key], adj[key], f"Mismatch for key {key}")

    def test_pricing_context_not_mutated(self):
        """2. Verify that original PricingContext remains unmodified during order calculations"""
        ctx = self.get_test_context(with_delivery=True)
        self.assertTrue(any(c.calculation_type == CalculationType.fixed_per_order for c in ctx.additional_costs))

        # Simulate route behavior (context cloning/filtering)
        item_only_costs = [
            cost.model_copy() for cost in ctx.additional_costs
            if cost.calculation_type != CalculationType.fixed_per_order
        ]
        item_ctx = ctx.model_copy(update={"additional_costs": item_only_costs})

        self.assertFalse(any(c.calculation_type == CalculationType.fixed_per_order for c in item_ctx.additional_costs))
        # Ensure original ctx still contains fixed_per_order
        self.assertTrue(any(c.calculation_type == CalculationType.fixed_per_order for c in ctx.additional_costs))

    def test_rounding_mismatch_resolved(self):
        """3. Prove that True Order-Level calculation avoids item-level rounding drifts"""
        # Scenario: 3 items, materials subtotal = 100.0 each, local costs = 50.0 each, delivery (fixed_per_order) = 100.0
        # Markup = 10%, Discount = 5%, Tax = 20% (not included)
        ctx = self.get_test_context(with_delivery=True, tax_included=False, rate=0.20)

        # Item-level breakdown with delivery filtered out:
        # materials = 100.0, local costs = 50.0. Total subtotal = 150.0
        # Markup = 150 * 10% = 15.0. Subtotal after markup = 165.0
        # Discount = 165 * 5% = 8.25. Adjusted subtotal = 165.0 - 8.25 = 156.75
        # Tax = 156.75 * 20% = 31.35. Total per item = 156.75 + 31.35 = 188.10
        # Sum of 3 items = 188.10 * 3 = 564.30.

        # True order level calculation:
        # Sum of materials = 300.0, local costs = 150.0, delivery = 100.0. Total subtotal before adjustments = 550.0
        # Markup = 550 * 10% = 55.0. Subtotal after markup = 605.0
        # Discount = 605 * 5% = 30.25. Adjusted subtotal = 574.75
        # Tax = 574.75 * 20% = 114.95. Grand Total = 574.75 + 114.95 = 689.70

        items_breakdowns = []
        for _ in range(3):
            # item result without delivery
            items_breakdowns.append({
                "materials_subtotal": 100.0,
                "additional_costs_total": 50.0,
                "additional_costs_breakdown": [{"id": "installation", "name": "Монтаж", "calculation_type": "fixed_per_item", "value": 500.0, "amount": 50.0}]
            })

        order_cb = calculate_order_commercials(items_breakdowns, ctx)
        self.assertEqual(order_cb["subtotal_before_markup"], 550.0)
        self.assertEqual(order_cb["markup_amount"], 55.0)
        self.assertEqual(order_cb["subtotal_after_markup"], 605.0)
        self.assertEqual(order_cb["discount_amount"], 30.25)
        self.assertEqual(order_cb["adjusted_subtotal"], 574.75)
        self.assertEqual(order_cb["vat_amount"], 114.95)
        self.assertEqual(order_cb["total"], 689.70)

    def test_tax_included_invariants(self):
        """4. Verify tax_included=True/False invariants on order level adjustments"""
        ctx_tax_excluded = self.get_test_context(with_delivery=True, tax_included=False, rate=0.20)
        ctx_tax_included = self.get_test_context(with_delivery=True, tax_included=True, rate=0.20)

        ib = [{"materials_subtotal": 100.0, "additional_costs_total": 50.0, "additional_costs_breakdown": []}]

        res_excluded = calculate_order_commercials(ib, ctx_tax_excluded)
        # adjusted subtotal = 150 + 100 = 250 -> markup 10% = 25 -> 275 -> discount 5% = 13.75 -> 261.25
        # Tax = 261.25 * 0.2 = 52.25. Total = 261.25 + 52.25 = 313.50
        self.assertEqual(res_excluded["adjusted_subtotal"], 261.25)
        self.assertEqual(res_excluded["net_price"], 261.25)
        self.assertEqual(res_excluded["vat_amount"], 52.25)
        self.assertEqual(res_excluded["total"], 313.50)

        res_included = calculate_order_commercials(ib, ctx_tax_included)
        # adjusted subtotal = 261.25. Total = 261.25. VAT = 261.25 * 0.2 / 1.2 = 43.54. Net = 261.25 - 43.54 = 217.71
        self.assertEqual(res_included["total"], 261.25)
        self.assertEqual(res_included["vat_amount"], 43.54)
        self.assertEqual(res_included["net_price"], 217.71)

    def test_create_order_multi_item_success(self):
        """5. Verify multi-item order with fixed_per_order delivery succeeds and persists correct schema"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        # Mock settings repo to return our test context
        mock_repo = MagicMock()
        from user_settings_repository import UserSettingsRepositoryResult
        from settings_models import UserSettingsStored
        from datetime import datetime, timezone

        ctx = self.get_test_context(with_delivery=True)

        # Extract stored format of context
        mock_repo.get_user_settings.return_value = UserSettingsRepositoryResult(
            settings=UserSettingsStored(
                updated_at=datetime.now(timezone.utc),
                additional_costs=ctx.additional_costs,
                commercial=ctx.commercial,
                tax_profile=ctx.tax_profile
            ),
            is_default=False
        )
        app.dependency_overrides[main.get_settings_repo] = lambda: mock_repo

        mock_db = MagicMock()
        main.calc.db = mock_db

        cart_data = {
            "items": [
                {"input": {"width": 1000.0, "height": 1000.0}},
                {"input": {"width": 1200.0, "height": 1200.0}}
            ]
        }

        response = client.post("/api/create-order", json=cart_data, headers={"Authorization": "Bearer token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")

        saved_record = mock_db.collection.return_value.document.return_value.set.call_args[0][0]

        self.assertIn("order_commercial_breakdown", saved_record)
        order_cb = saved_record["order_commercial_breakdown"]

        # Verify grand totals
        self.assertEqual(saved_record["grand_total"], order_cb["total"])
        self.assertEqual(saved_record["grand_net"], order_cb["net_price"])
        self.assertEqual(saved_record["grand_vat"], order_cb["vat_amount"])

        # Verify context filtering: delivery is ONLY in order breakdown
        self.assertEqual(len(order_cb["order_level_additional_costs_breakdown"]), 1)
        self.assertEqual(order_cb["order_level_additional_costs_breakdown"][0]["id"], "delivery")

        # Verify item breakdown does NOT contain delivery
        for item in saved_record["cart"]["items"]:
            item_costs = item["result"]["commercial_breakdown"]["additional_costs_breakdown"]
            self.assertFalse(any(c["id"] == "delivery" for c in item_costs))

    @patch("pdf_generator.CartQuotePDF.cell")
    def test_pdf_uses_order_breakdown_and_legacy_fallback(self, mock_cell):
        """6. Verify generate_cart_pdf works for new orders and has legacy fallback"""
        # 1. New Order
        cart_data_new = {
            "order_id": "NEW123",
            "items": [
                {
                    "input": {"width": 1000.0, "height": 1000.0},
                    "result": {
                        "metrics": {"area": 1.0, "weight": 20.0, "perimeter": 4.0},
                        "cost_details": {"total": 5000.0}
                    }
                }
            ],
            "order_commercial_breakdown": {
                "total": 6000.0,
                "order_level_additional_costs_breakdown": [
                    {"id": "delivery", "name": "Доставка", "amount": 100.0}
                ]
            }
        }

        generate_cart_pdf(cart_data_new)
        called_args = [str(call) for call in mock_cell.call_args_list]

        self.assertTrue(any("Доставка: 100.00 грн" in s for s in called_args), "Delivery row not rendered")
        self.assertTrue(any("ЗАГАЛЬНА СУМА ДО СПЛАТИ: 6,000.00 грн" in s for s in called_args), "Order commercial breakdown total not used")

        # 2. Legacy Fallback (No order_commercial_breakdown)
        mock_cell.reset_mock()
        cart_data_legacy = {
            "order_id": "LEG123",
            "items": [
                {
                    "input": {"width": 1000.0, "height": 1000.0},
                    "result": {
                        "metrics": {"area": 1.0, "weight": 20.0, "perimeter": 4.0},
                        "cost_details": {"total": 5000.0}
                    }
                }
            ]
        }
        generate_cart_pdf(cart_data_legacy)
        called_args_legacy = [str(call) for call in mock_cell.call_args_list]
        self.assertTrue(any("ЗАГАЛЬНА СУМА ДО СПЛАТИ: 5,000.00 грн" in s for s in called_args_legacy), "Legacy fallback total not used")

    def test_frontend_history_uses_grand_total_documentation(self):
        """7. Verify frontend compatibility with grand_total"""
        with open("static/script.js", "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("o.grand_total !== undefined", content)
        self.assertIn("o.cart.items.forEach", content)

    @patch("main.calc.calculate_project")
    def test_create_order_multi_item_without_fixed_per_order_rounding_mismatch(self, mock_calc_project):
        """8. Multi-item order without active fixed_per_order uses order-level helper, avoiding sum of rounded items"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_repo = MagicMock()
        from user_settings_repository import UserSettingsRepositoryResult
        from settings_models import UserSettingsStored
        from datetime import datetime, timezone

        ctx = self.get_test_context(with_delivery=False)
        mock_repo.get_user_settings.return_value = UserSettingsRepositoryResult(
            settings=UserSettingsStored(
                updated_at=datetime.now(timezone.utc),
                additional_costs=ctx.additional_costs,
                commercial=ctx.commercial,
                tax_profile=ctx.tax_profile
            ),
            is_default=False
        )
        app.dependency_overrides[main.get_settings_repo] = lambda: mock_repo

        mock_db = MagicMock()
        main.calc.db = mock_db

        # Mock calculator return value with values that trigger rounding mismatches when summed
        mock_calc_project.return_value = {
            "status": "success",
            "net_price": 104.73,
            "vat_amount": 20.95,
            "cost_details": {"total": 125.68},
            "commercial_breakdown": {
                "materials_subtotal": 100.22,
                "additional_costs_total": 0.0,
                "additional_costs_breakdown": [],
                "subtotal_before_markup": 100.22,
                "markup_rate": 10.0,
                "markup_amount": 10.02,
                "subtotal_after_markup": 110.24,
                "discount_rate": 5.0,
                "discount_amount": 5.51,
                "adjusted_subtotal": 104.73,
                "tax_rate": 0.20,
                "tax_included": False,
                "net_price": 104.73,
                "vat_amount": 20.95,
                "total": 125.68
            }
        }

        cart_data = {
            "items": [
                {"input": {"width": 1000.0, "height": 1000.0}},
                {"input": {"width": 1000.0, "height": 1000.0}},
                {"input": {"width": 1000.0, "height": 1000.0}}
            ]
        }

        response = client.post("/api/create-order", json=cart_data, headers={"Authorization": "Bearer token"})
        self.assertEqual(response.status_code, 200)

        saved_record = mock_db.collection.return_value.document.return_value.set.call_args[0][0]

        # Verify order commercial breakdown is calculated correctly at the order level
        order_cb = saved_record["order_commercial_breakdown"]
        self.assertEqual(saved_record["grand_total"], 377.03)  # True order level total: 100.22*3 = 300.66 * 1.1 = 330.73 * 0.95 = 314.19 * 1.2 = 377.028 -> 377.03
        self.assertEqual(saved_record["grand_total"], order_cb["total"])

        # Sum of items would be 125.68 * 3 = 377.04. The true order-level is 377.03, prove they differ
        sum_of_items = sum(item["result"]["commercial_breakdown"]["total"] for item in saved_record["cart"]["items"])
        self.assertEqual(sum_of_items, 377.04)
        self.assertNotEqual(saved_record["grand_total"], sum_of_items)

    @patch("main.calc.calculate_project")
    def test_create_order_incomplete_item_breakdown_fails_closed(self, mock_calc_project):
        """9. Incomplete item breakdown from calculator fails closed and prevents Firestore write"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_repo = MagicMock()
        from user_settings_repository import UserSettingsRepositoryResult
        from settings_models import UserSettingsStored
        from datetime import datetime, timezone

        ctx = self.get_test_context(with_delivery=False)
        mock_repo.get_user_settings.return_value = UserSettingsRepositoryResult(
            settings=UserSettingsStored(
                updated_at=datetime.now(timezone.utc),
                additional_costs=ctx.additional_costs,
                commercial=ctx.commercial,
                tax_profile=ctx.tax_profile
            ),
            is_default=False
        )
        app.dependency_overrides[main.get_settings_repo] = lambda: mock_repo

        mock_db = MagicMock()
        main.calc.db = mock_db

        # Calculator returns a breakdown missing materials_subtotal
        mock_calc_project.return_value = {
            "status": "success",
            "net_price": 100.0,
            "vat_amount": 20.0,
            "cost_details": {"total": 120.0},
            "commercial_breakdown": {
                "additional_costs_total": 0.0,
                "additional_costs_breakdown": [],
                "net_price": 100.0,
                "vat_amount": 20.0,
                "total": 120.0
            }
        }

        cart_data = {"items": [{"input": {"width": 1000.0, "height": 1000.0}}]}
        response = client.post("/api/create-order", json=cart_data, headers={"Authorization": "Bearer token"})

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "Помилка конфігурації калькулятора")
        mock_db.collection.assert_not_called()
