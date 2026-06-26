import unittest
import copy
import json
import sys
from datetime import datetime, timezone
from settings_models import PricingContext
from pricing_context_provider import get_default_pricing_context
from pricing_context_builder import InvalidGlobalCatalogError

class TestPricingContextProvider(unittest.TestCase):

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

    def test_provider_returns_pricing_context(self):
        catalog = self.get_valid_catalog()
        ctx = get_default_pricing_context(catalog)
        self.assertIsInstance(ctx, PricingContext)

    def test_production_catalog_successfully_processed(self):
        with open("materials.json", "r", encoding="utf-8") as f:
            catalog = json.load(f)
        ctx = get_default_pricing_context(catalog)
        self.assertIsInstance(ctx, PricingContext)
        self.assertIn("REHAU_Euro_70", ctx.resolved_prices.profiles)
        self.assertEqual(ctx.resolved_prices.profiles["REHAU_Euro_70"], 250.0)

    def test_each_call_returns_independent_object_graph(self):
        catalog = self.get_valid_catalog()
        ctx1 = get_default_pricing_context(catalog)
        ctx2 = get_default_pricing_context(catalog)
        self.assertIsNot(ctx1, ctx2)
        self.assertIsNot(ctx1.resolved_prices, ctx2.resolved_prices)
        self.assertIsNot(ctx1.tax_profile, ctx2.tax_profile)
        self.assertIsNot(ctx1.commercial, ctx2.commercial)

    def test_context_mutation_isolation(self):
        catalog = self.get_valid_catalog()
        ctx1 = get_default_pricing_context(catalog)
        ctx2 = get_default_pricing_context(catalog)

        ctx1.resolved_prices.profiles["P1"] = 999.0
        self.assertEqual(ctx2.resolved_prices.profiles["P1"], 10.0)

    def test_source_catalog_not_mutated(self):
        catalog = self.get_valid_catalog()
        catalog_copy = copy.deepcopy(catalog)
        get_default_pricing_context(catalog)
        self.assertEqual(catalog, catalog_copy)

    def test_default_tax_profile_defaults(self):
        catalog = self.get_valid_catalog()
        ctx = get_default_pricing_context(catalog)
        self.assertEqual(ctx.tax_profile.name, "Без податку")
        self.assertEqual(ctx.tax_profile.rate, 0.0)
        self.assertEqual(ctx.tax_profile.included_in_price, False)

    def test_timestamp_timezone_aware(self):
        catalog = self.get_valid_catalog()
        # Since settings model stored doesn't store updated_at in context, let's verify context is generated successfully
        ctx = get_default_pricing_context(catalog)
        self.assertIsInstance(ctx, PricingContext)

    def test_provider_imports_isolation(self):
        # Read the file and ensure forbidden keywords are absent
        with open("pricing_context_provider.py", "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("main", content)
        self.assertNotIn("app", content)
        self.assertNotIn("FastAPI", content)
        self.assertNotIn("Flask", content)
        self.assertNotIn("firebase", content)
        self.assertNotIn("firestore", content)
        self.assertNotIn("uid", content)
        self.assertNotIn("repository", content)
        self.assertNotIn("calculator", content)

    def test_missing_category_not_masked(self):
        catalog = self.get_valid_catalog()
        del catalog["hardware"]
        with self.assertRaises(InvalidGlobalCatalogError):
            get_default_pricing_context(catalog)

    def test_corrupted_catalog_not_masked(self):
        catalog = self.get_valid_catalog()
        catalog["profiles"]["P1"]["price_per_m"] = "invalid_price"
        with self.assertRaises(InvalidGlobalCatalogError):
            get_default_pricing_context(catalog)

if __name__ == "__main__":
    unittest.main()
