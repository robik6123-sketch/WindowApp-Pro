import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

# Prevent Firebase/Firestore initialization and reading service-account.json during main.py import
env_patcher = patch.dict(os.environ, {"USE_FIRESTORE": "false", "GOOGLE_APPLICATION_CREDENTIALS_JSON": ""})
cert_patcher = patch("firebase_admin.credentials.Certificate")
init_patcher = patch("firebase_admin.initialize_app")
client_patcher = patch("firebase_admin.firestore.client")

env_patcher.start()
cert_patcher.start()
init_patcher.start()
client_patcher.start()

from fastapi.testclient import TestClient
import main
from main import app, verify_firebase_token, get_settings_repo
from user_settings_repository import (
    UserSettingsRepositoryResult,
    InvalidUIDError,
    UserSettingsNotReadableError,
    UserSettingsInvalidDocumentError
)
from pricing_context_builder import (
    PricingContextBuilderError,
    InvalidGlobalCatalogError,
    UnknownMaterialOverrideError,
    PricingContextValidationError
)
from calculator import (
    CalculatorPricingError,
    UnknownMaterialError,
    MissingResolvedPriceError
)
from settings_models import UserSettingsStored

cert_patcher.stop()
init_patcher.stop()
client_patcher.stop()
env_patcher.stop()

client = TestClient(app)

class FakeUserSettingsRepository:
    def __init__(self):
        self.get_calls = []
        self.get_result = None
        self.get_exception = None

    def get_user_settings(self, uid):
        self.get_calls.append(uid)
        if self.get_exception is not None:
            raise self.get_exception
        return self.get_result

class TestCalculatePricingIntegration(unittest.TestCase):

    def setUp(self):
        app.dependency_overrides.clear()
        self.fake_repo = FakeUserSettingsRepository()
        app.dependency_overrides[get_settings_repo] = lambda: self.fake_repo
        app.dependency_overrides[verify_firebase_token] = lambda: {"uid": "test_user_123", "email": "test@example.com"}
        self.uid = "test_user_123"

    def tearDown(self):
        app.dependency_overrides.clear()

    @patch("main.calc.calculate_project")
    @patch("main.build_pricing_context")
    def test_successful_orchestration(self, mock_build, mock_calculate):
        self.fake_repo.get_result = UserSettingsRepositoryResult(
            settings=UserSettingsStored(updated_at=datetime.now(timezone.utc)),
            is_default=False
        )
        mock_build.return_value = MagicMock()
        mock_calculate.return_value = {"status": "success", "price": 100.0}

        order_payload = {"width": 1000, "height": 1000}
        res = client.post("/api/calculate", json=order_payload)

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"status": "success", "price": 100.0})

        # Verify repository was called exactly once with the verified UID
        self.assertEqual(self.fake_repo.get_calls, [self.uid])

        # Verify builder was called with global materials and repository settings
        mock_build.assert_called_once_with(main.calc.materials, self.fake_repo.get_result.settings)

        # Verify calculator was called with order dict and the context returned by the builder
        mock_calculate.assert_called_once_with(order_payload, mock_build.return_value)

    def test_default_provider_removal(self):
        # 1. Verify get_default_pricing_context is not imported or present in main
        self.assertNotIn("get_default_pricing_context", main.__dict__)

        # 2. Verify that app.py and test_poc.py still contain default-provider calls
        with open("app.py", "r", encoding="utf-8") as f:
            app_content = f.read()
        self.assertIn("get_default_pricing_context", app_content)

        with open("test_poc.py", "r", encoding="utf-8") as f:
            poc_content = f.read()
        self.assertIn("get_default_pricing_context", poc_content)

    def test_profile_override_affects_result(self):
        self.fake_repo.get_result = UserSettingsRepositoryResult(
            settings=UserSettingsStored(
                pricing={"profiles": {"REHAU_Euro_70": 999.0}},
                updated_at=datetime.now(timezone.utc)
            ),
            is_default=False
        )
        order_payload = {"width": 1000, "height": 1000, "profile": "REHAU_Euro_70"}
        res = client.post("/api/calculate", json=order_payload)
        self.assertEqual(res.status_code, 200)
        price_with_override = res.json()["cost_details"]["profile"]

        self.fake_repo.get_result = UserSettingsRepositoryResult(
            settings=UserSettingsStored(updated_at=datetime.now(timezone.utc)),
            is_default=True
        )
        res2 = client.post("/api/calculate", json=order_payload)
        self.assertEqual(res2.status_code, 200)
        price_default = res2.json()["cost_details"]["profile"]

        self.assertNotEqual(price_with_override, price_default)
        self.assertEqual(price_with_override, 999.0 * 4.0)

    def test_filling_override_affects_result(self):
        self.fake_repo.get_result = UserSettingsRepositoryResult(
            settings=UserSettingsStored(
                pricing={"fillings": {"glass_24": 777.0}},
                updated_at=datetime.now(timezone.utc)
            ),
            is_default=False
        )
        order_payload = {"width": 1000, "height": 1000, "glass": "glass_24"}
        res = client.post("/api/calculate", json=order_payload)
        self.assertEqual(res.status_code, 200)
        price_with_override = res.json()["cost_details"]["glass"]

        self.fake_repo.get_result = UserSettingsRepositoryResult(
            settings=UserSettingsStored(updated_at=datetime.now(timezone.utc)),
            is_default=True
        )
        res2 = client.post("/api/calculate", json=order_payload)
        self.assertEqual(res2.status_code, 200)
        price_default = res2.json()["cost_details"]["glass"]

        self.assertNotEqual(price_with_override, price_default)
        self.assertEqual(price_with_override, 777.0)

    def test_hardware_override_affects_result(self):
        self.fake_repo.get_result = UserSettingsRepositoryResult(
            settings=UserSettingsStored(
                pricing={"hardware": {"tilt_turn": 500.0}},
                updated_at=datetime.now(timezone.utc)
            ),
            is_default=False
        )
        order_payload = {"width": 1000, "height": 1000, "panels": [{"type": "tilt_turn", "proportion": 100}]}
        res = client.post("/api/calculate", json=order_payload)
        self.assertEqual(res.status_code, 200)
        price_with_override = res.json()["cost_details"]["hardware"]

        self.fake_repo.get_result = UserSettingsRepositoryResult(
            settings=UserSettingsStored(updated_at=datetime.now(timezone.utc)),
            is_default=True
        )
        res2 = client.post("/api/calculate", json=order_payload)
        self.assertEqual(res2.status_code, 200)
        price_default = res2.json()["cost_details"]["hardware"]

        self.assertNotEqual(price_with_override, price_default)
        self.assertEqual(price_with_override, 500.0)

    def test_extra_override_affects_result(self):
        self.fake_repo.get_result = UserSettingsRepositoryResult(
            settings=UserSettingsStored(
                pricing={"extras": {"sill": 300.0}},
                updated_at=datetime.now(timezone.utc)
            ),
            is_default=False
        )
        order_payload = {"width": 1000, "height": 1000, "sill_length": 1000, "sill_width": 200}
        res = client.post("/api/calculate", json=order_payload)
        self.assertEqual(res.status_code, 200)
        price_with_override = res.json()["cost_details"]["extras"]

        self.fake_repo.get_result = UserSettingsRepositoryResult(
            settings=UserSettingsStored(updated_at=datetime.now(timezone.utc)),
            is_default=True
        )
        res2 = client.post("/api/calculate", json=order_payload)
        self.assertEqual(res2.status_code, 200)
        price_default = res2.json()["cost_details"]["extras"]

        self.assertNotEqual(price_with_override, price_default)
        self.assertEqual(price_with_override, 60.0)

    def test_zero_override_price_preserved(self):
        self.fake_repo.get_result = UserSettingsRepositoryResult(
            settings=UserSettingsStored(
                pricing={"profiles": {"REHAU_Euro_70": 0.0}},
                updated_at=datetime.now(timezone.utc)
            ),
            is_default=False
        )
        order_payload = {"width": 1000, "height": 1000, "profile": "REHAU_Euro_70"}
        res = client.post("/api/calculate", json=order_payload)
        self.assertEqual(res.status_code, 200)
        price_with_override = res.json()["cost_details"]["profile"]
        self.assertEqual(price_with_override, 0.0)

    @patch("main.calc.calculate_project")
    def test_missing_document_handling(self, mock_calculate):
        self.fake_repo.get_result = UserSettingsRepositoryResult(
            settings=UserSettingsStored(updated_at=datetime.now(timezone.utc)),
            is_default=True
        )
        mock_calculate.return_value = {"price": 100.0}

        res = client.post("/api/calculate", json={"width": 1000, "height": 1000})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.fake_repo.get_calls, [self.uid])

    @patch("main.calc.calculate_project")
    @patch("main.build_pricing_context")
    def test_uid_isolation(self, mock_build, mock_calculate):
        app.dependency_overrides[verify_firebase_token] = lambda: {"uid": "user-A"}
        self.fake_repo.get_result = UserSettingsRepositoryResult(
            settings=UserSettingsStored(updated_at=datetime.now(timezone.utc)),
            is_default=False
        )
        client.post("/api/calculate", json={"width": 1000, "height": 1000})
        self.assertEqual(self.fake_repo.get_calls[-1], "user-A")

        app.dependency_overrides[verify_firebase_token] = lambda: {"uid": "user-B"}
        client.post("/api/calculate", json={"width": 1000, "height": 1000})
        self.assertEqual(self.fake_repo.get_calls[-1], "user-B")

    def test_payload_uid_spoofing_rejected(self):
        payloads = [
            {"width": 1000, "height": 1000, "uid": "attacker"},
            {"width": 1000, "height": 1000, "owner_uid": "attacker"},
            {"width": 1000, "height": 1000, "email": "attacker@example.com"},
            {"width": 1000, "height": 1000, "user_email": "attacker@example.com"},
        ]
        for p in payloads:
            with self.subTest(payload=p):
                self.fake_repo.get_calls = []
                res = client.post("/api/calculate", json=p)
                self.assertEqual(res.status_code, 422)
                self.assertEqual(self.fake_repo.get_calls, [])

    def test_query_spoofing_ignored(self):
        self.fake_repo.get_result = UserSettingsRepositoryResult(
            settings=UserSettingsStored(updated_at=datetime.now(timezone.utc)),
            is_default=True
        )
        res = client.post("/api/calculate?uid=user-B&user_email=other@example.com", json={"width": 1000, "height": 1000})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.fake_repo.get_calls, [self.uid])

    @patch("main.calc.calculate_project")
    def test_invalid_auth_valid_body_bypasses_repo(self, mock_calculate):
        if verify_firebase_token in app.dependency_overrides:
            del app.dependency_overrides[verify_firebase_token]
        res = client.post("/api/calculate", json={"width": 1000, "height": 1000})
        self.assertEqual(res.status_code, 401)
        self.assertEqual(self.fake_repo.get_calls, [])
        mock_calculate.assert_not_called()

    @patch("main.calc.calculate_project")
    def test_valid_auth_invalid_body_bypasses_repo(self, mock_calculate):
        res = client.post("/api/calculate", json={"width": -500})
        self.assertEqual(res.status_code, 422)
        self.assertEqual(self.fake_repo.get_calls, [])
        mock_calculate.assert_not_called()

    @patch("main.calc.calculate_project")
    def test_invalid_auth_and_invalid_body_compound_behavior(self, mock_calculate):
        if verify_firebase_token in app.dependency_overrides:
            del app.dependency_overrides[verify_firebase_token]
        res = client.post("/api/calculate", json={"width": -500})
        self.assertEqual(res.status_code, 401)
        self.assertEqual(self.fake_repo.get_calls, [])
        mock_calculate.assert_not_called()

    def test_repository_invalid_uid_error_mapping(self):
        self.fake_repo.get_exception = InvalidUIDError("SECRET_UID_DETAILS")
        res = client.post("/api/calculate", json={"width": 1000, "height": 1000})
        self.assertEqual(res.status_code, 500)
        self.assertEqual(res.json()["detail"], "Authentication context is invalid")
        self.assertNotIn("SECRET_UID_DETAILS", res.text)

    def test_repository_not_readable_error_mapping(self):
        self.fake_repo.get_exception = UserSettingsNotReadableError("SECRET_FIRESTORE_PATH")
        res = client.post("/api/calculate", json={"width": 1000, "height": 1000})
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()["detail"], "User settings are temporarily unavailable")
        self.assertNotIn("SECRET_FIRESTORE_PATH", res.text)

    def test_repository_invalid_document_error_mapping(self):
        self.fake_repo.get_exception = UserSettingsInvalidDocumentError("SECRET_FIELD_NAME")
        res = client.post("/api/calculate", json={"width": 1000, "height": 1000})
        self.assertEqual(res.status_code, 500)
        self.assertEqual(res.json()["detail"], "Stored user settings are invalid")
        self.assertNotIn("SECRET_FIELD_NAME", res.text)

    @patch("main.build_pricing_context")
    def test_builder_errors_mapping(self, mock_build):
        self.fake_repo.get_result = UserSettingsRepositoryResult(
            settings=UserSettingsStored(updated_at=datetime.now(timezone.utc)),
            is_default=False
        )

        exceptions = [
            (InvalidGlobalCatalogError("SECRET_CATALOG"), 500, "Внутрішня помилка конфігурації"),
            (UnknownMaterialOverrideError("SECRET_MATERIAL"), 500, "Внутрішня помилка конфігурації"),
            (PricingContextValidationError("SECRET_VALIDATION"), 500, "Внутрішня помилка розрахунку ціни"),
            (PricingContextBuilderError("SECRET_BASE"), 500, "Внутрішня помилка розрахунку ціни")
        ]

        for exc, expected_status, expected_detail in exceptions:
            with self.subTest(exc=exc):
                mock_build.side_effect = exc
                res = client.post("/api/calculate", json={"width": 1000, "height": 1000})
                self.assertEqual(res.status_code, expected_status)
                self.assertEqual(res.json()["detail"], expected_detail)
                self.assertNotIn("SECRET_", res.text)

    @patch("main.calc.calculate_project")
    def test_calculator_errors_mapping(self, mock_calculate):
        self.fake_repo.get_result = UserSettingsRepositoryResult(
            settings=UserSettingsStored(updated_at=datetime.now(timezone.utc)),
            is_default=False
        )

        exceptions = [
            (MissingResolvedPriceError("SECRET_PRICE"), 500, "Внутрішня помилка розрахунку ціни"),
            (UnknownMaterialError("SECRET_MATERIAL"), 400, "Невідомий матеріал або конфігурація"),
            (CalculatorPricingError("SECRET_CONFIG"), 500, "Помилка конфігурації калькулятора")
        ]

        for exc, expected_status, expected_detail in exceptions:
            with self.subTest(exc=exc):
                mock_calculate.side_effect = exc
                res = client.post("/api/calculate", json={"width": 1000, "height": 1000})
                self.assertEqual(res.status_code, expected_status)
                self.assertEqual(res.json()["detail"], expected_detail)
                self.assertNotIn("SECRET_", res.text)

if __name__ == "__main__":
    unittest.main()
