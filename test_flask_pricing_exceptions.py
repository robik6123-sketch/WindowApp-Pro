import unittest
from unittest.mock import patch
from app import app
from calculator import MissingResolvedPriceError, UnknownMaterialError, CalculatorPricingError

class TestFlaskPricingExceptions(unittest.TestCase):

    def setUp(self):
        self.client = app.test_client()
        self.payload = {
            "width": 1000,
            "height": 1000,
            "profile": "REHAU_Euro_70",
            "glass": "glass_24",
            "color": "white",
            "panels": [{"type": "fixed", "proportion": 100.0}]
        }

    @patch("app.calc.calculate_project")
    def test_calculate_missing_resolved_price_error(self, mock_calc):
        mock_calc.side_effect = MissingResolvedPriceError("SECRET internal price id")
        response = self.client.post("/api/calculate", json=self.payload)
        self.assertEqual(response.status_code, 500)
        data = response.get_json()
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["error"], "Внутрішня помилка розрахунку ціни")
        self.assertNotIn("SECRET", response.text)
        self.assertNotIn("internal price id", response.text)

    @patch("app.calc.calculate_project")
    def test_calculate_unknown_material_error(self, mock_calc):
        mock_calc.side_effect = UnknownMaterialError("SECRET unknown profile WDS_X")
        response = self.client.post("/api/calculate", json=self.payload)
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["error"], "Невідомий матеріал або конфігурація")
        self.assertNotIn("SECRET", response.text)
        self.assertNotIn("WDS_X", response.text)

    @patch("app.calc.calculate_project")
    def test_calculate_calculator_pricing_error(self, mock_calc):
        mock_calc.side_effect = CalculatorPricingError("SECRET malformed multiplier")
        response = self.client.post("/api/calculate", json=self.payload)
        self.assertEqual(response.status_code, 500)
        data = response.get_json()
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["error"], "Помилка конфігурації калькулятора")
        self.assertNotIn("SECRET", response.text)

    @patch("app.calc.calculate_project")
    def test_report_missing_resolved_price_error(self, mock_calc):
        mock_calc.side_effect = MissingResolvedPriceError("SECRET internal price id")
        response = self.client.post("/api/report", json=self.payload)
        self.assertEqual(response.status_code, 500)
        data = response.get_json()
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["error"], "Внутрішня помилка розрахунку ціни")
        self.assertNotIn("SECRET", response.text)
        self.assertNotIn("internal price id", response.text)

    @patch("app.calc.calculate_project")
    def test_report_unknown_material_error(self, mock_calc):
        mock_calc.side_effect = UnknownMaterialError("SECRET unknown profile WDS_X")
        response = self.client.post("/api/report", json=self.payload)
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["error"], "Невідомий матеріал або конфігурація")
        self.assertNotIn("SECRET", response.text)
        self.assertNotIn("WDS_X", response.text)

    @patch("app.calc.calculate_project")
    def test_report_calculator_pricing_error(self, mock_calc):
        mock_calc.side_effect = CalculatorPricingError("SECRET malformed multiplier")
        response = self.client.post("/api/report", json=self.payload)
        self.assertEqual(response.status_code, 500)
        data = response.get_json()
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["error"], "Помилка конфігурації калькулятора")
        self.assertNotIn("SECRET", response.text)

if __name__ == "__main__":
    unittest.main()
