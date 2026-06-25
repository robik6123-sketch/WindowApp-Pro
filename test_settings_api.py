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
    UserSettingsInvalidDocumentError,
    UserSettingsWriteError
)
from settings_models import UserSettingsStored, UserSettingsResponse, UserSettingsUpdate

cert_patcher.stop()
init_patcher.stop()
client_patcher.stop()
env_patcher.stop()

client = TestClient(app)

class FakeUserSettingsRepository:
    def __init__(self):
        self.get_calls = []
        self.save_calls = []
        self.get_result = None
        self.put_result = None
        self.get_exception = None
        self.put_exception = None

    def get_user_settings(self, uid):
        self.get_calls.append(uid)
        if self.get_exception is not None:
            raise self.get_exception
        return self.get_result

    def save_user_settings(self, uid, settings):
        self.save_calls.append((uid, settings))
        if self.put_exception is not None:
            raise self.put_exception
        return self.put_result

class TestSettingsApi(unittest.TestCase):

    def setUp(self):
        # Clear dependency overrides before each test
        app.dependency_overrides.clear()
        self.fake_repo = FakeUserSettingsRepository()
        app.dependency_overrides[get_settings_repo] = lambda: self.fake_repo
        app.dependency_overrides[verify_firebase_token] = lambda: {"uid": "test_user_123", "email": "test@example.com"}
        self.uid = "test_user_123"

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_get_settings_passes_verified_uid_to_repo(self):
        self.fake_repo.get_result = UserSettingsRepositoryResult(
            settings=UserSettingsStored(updated_at=datetime.now(timezone.utc)),
            is_default=False
        )
        res = client.get("/api/settings")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.fake_repo.get_calls, [self.uid])

    def test_put_settings_passes_verified_uid_to_repo(self):
        self.fake_repo.put_result = UserSettingsStored(updated_at=datetime.now(timezone.utc))
        res = client.put("/api/settings", json={})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(self.fake_repo.save_calls), 1)
        self.assertEqual(self.fake_repo.save_calls[0][0], self.uid)
        self.assertIsInstance(self.fake_repo.save_calls[0][1], UserSettingsUpdate)

    def test_email_from_auth_context_ignored_as_uid(self):
        app.dependency_overrides[verify_firebase_token] = lambda: {
            "uid": "real_uid_123",
            "email": "attacker_uid@example.com"
        }
        self.fake_repo.get_result = UserSettingsRepositoryResult(
            settings=UserSettingsStored(updated_at=datetime.now(timezone.utc)),
            is_default=False
        )
        res = client.get("/api/settings")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.fake_repo.get_calls, ["real_uid_123"])

    def test_system_fields_in_put_body_raise_validation_error(self):
        extra_fields = [
            "schema_version",
            "updated_at",
            "uid",
            "owner_uid",
            "user_email",
            "email",
            "is_default"
        ]
        for field in extra_fields:
            with self.subTest(field=field):
                res = client.put("/api/settings", json={field: "some_value"})
                self.assertEqual(res.status_code, 422)
                self.assertEqual(self.fake_repo.save_calls, [])

    def test_missing_uid_in_auth_context_raises_500(self):
        app.dependency_overrides[verify_firebase_token] = lambda: {"email": "test@example.com"}
        res = client.get("/api/settings")
        self.assertEqual(res.status_code, 500)
        self.assertEqual(self.fake_repo.get_calls, [])
        self.assertEqual(res.json()["detail"], "Authentication context is invalid")

    def test_invalid_uid_type_in_auth_context_raises_500(self):
        app.dependency_overrides[verify_firebase_token] = lambda: {"uid": "   "}
        res = client.get("/api/settings")
        self.assertEqual(res.status_code, 500)
        self.assertEqual(self.fake_repo.get_calls, [])
        self.assertEqual(res.json()["detail"], "Authentication context is invalid")

        app.dependency_overrides[verify_firebase_token] = lambda: {"uid": 123}
        res2 = client.get("/api/settings")
        self.assertEqual(res2.status_code, 500)
        self.assertEqual(self.fake_repo.get_calls, [])
        self.assertEqual(res2.json()["detail"], "Authentication context is invalid")

    def test_missing_authorization_header_returns_401(self):
        if verify_firebase_token in app.dependency_overrides:
            del app.dependency_overrides[verify_firebase_token]

        def assert_not_called_repo():
            raise AssertionError("Repository dependency was called when auth failed!")
        app.dependency_overrides[get_settings_repo] = assert_not_called_repo

        res = client.get("/api/settings")
        self.assertEqual(res.status_code, 401)

    def test_invalid_auth_token_returns_401(self):
        if verify_firebase_token in app.dependency_overrides:
            del app.dependency_overrides[verify_firebase_token]

        def assert_not_called_repo():
            raise AssertionError("Repository dependency was called when auth failed!")
        app.dependency_overrides[get_settings_repo] = assert_not_called_repo

        res = client.get("/api/settings", headers={"Authorization": "Bearer invalid_token"})
        self.assertEqual(res.status_code, 401)

    def test_get_read_error_returns_503(self):
        self.fake_repo.get_exception = UserSettingsNotReadableError("Read timeout")
        res = client.get("/api/settings")
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()["detail"], "User settings are temporarily unavailable")

    def test_get_corrupted_document_returns_500(self):
        self.fake_repo.get_exception = UserSettingsInvalidDocumentError("Missing schema version")
        res = client.get("/api/settings")
        self.assertEqual(res.status_code, 500)
        self.assertEqual(res.json()["detail"], "Stored user settings are invalid")

    def test_put_write_error_returns_503(self):
        self.fake_repo.put_exception = UserSettingsWriteError("Write failed")
        res = client.put("/api/settings", json={})
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()["detail"], "Unable to save user settings")

    def test_db_repo_unavailable_returns_503(self):
        if get_settings_repo in app.dependency_overrides:
            del app.dependency_overrides[get_settings_repo]

        real_use_firestore = main.USE_FIRESTORE
        real_db = getattr(main.calc, "db", None)

        try:
            main.USE_FIRESTORE = False
            res1 = client.get("/api/settings")
            self.assertEqual(res1.status_code, 503)
            self.assertEqual(res1.json()["detail"], "User settings are temporarily unavailable")

            main.USE_FIRESTORE = True
            main.calc.db = None
            res2 = client.get("/api/settings")
            self.assertEqual(res2.status_code, 503)
            self.assertEqual(res2.json()["detail"], "User settings are temporarily unavailable")
        finally:
            main.USE_FIRESTORE = real_use_firestore
            main.calc.db = real_db

    def test_exception_details_not_exposed_in_response(self):
        self.fake_repo.get_exception = UserSettingsNotReadableError("SECRET_KEY failed connection to Firestore at port 123")
        res = client.get("/api/settings")
        self.assertEqual(res.status_code, 503)
        self.assertNotIn("SECRET_KEY", res.text)
        self.assertEqual(res.json()["detail"], "User settings are temporarily unavailable")

    def test_get_settings_does_not_trigger_save(self):
        self.fake_repo.get_result = UserSettingsRepositoryResult(
            settings=UserSettingsStored(updated_at=datetime.now(timezone.utc)),
            is_default=False
        )
        res = client.get("/api/settings")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.fake_repo.save_calls, [])

    def test_put_settings_triggers_save_exactly_once(self):
        self.fake_repo.put_result = UserSettingsStored(updated_at=datetime.now(timezone.utc))
        res = client.put("/api/settings", json={})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(self.fake_repo.save_calls), 1)

    def test_put_empty_json_normalizes_to_defaults(self):
        self.fake_repo.put_result = UserSettingsStored(updated_at=datetime.now(timezone.utc))
        res = client.put("/api/settings", json={})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(self.fake_repo.save_calls), 1)
        settings_passed = self.fake_repo.save_calls[0][1]
        self.assertEqual(settings_passed.currency, "UAH")
        self.assertEqual(settings_passed.commercial.markup_rate, 0.0)
        self.assertEqual(settings_passed.tax_profile.name, "Без податку")

    def test_get_settings_response_matches_schema(self):
        now = datetime.now(timezone.utc)
        self.fake_repo.get_result = UserSettingsRepositoryResult(
            settings=UserSettingsStored(
                currency="UAH",
                commercial={"markup_rate": 12.5, "discount_rate": 2.0},
                updated_at=now
            ),
            is_default=False
        )
        res = client.get("/api/settings")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["currency"], "UAH")
        self.assertEqual(data["commercial"]["markup_rate"], 12.5)
        self.assertEqual(data["is_default"], False)

    def test_is_default_true_only_for_missing_get(self):
        self.fake_repo.get_result = UserSettingsRepositoryResult(
            settings=UserSettingsStored(updated_at=datetime.now(timezone.utc)),
            is_default=True
        )
        res = client.get("/api/settings")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["is_default"])

    def test_is_default_false_after_put(self):
        self.fake_repo.put_result = UserSettingsStored(updated_at=datetime.now(timezone.utc))
        res = client.put("/api/settings", json={})
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()["is_default"])

    def test_boolean_in_numeric_field_returns_422(self):
        payloads = [
            {"pricing": {"profiles": {"REHAU": True}}},
            {"commercial": {"markup_rate": True}},
            {"tax_profile": {"rate": False}}
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                res = client.put("/api/settings", json=payload)
                self.assertEqual(res.status_code, 422)
                self.assertEqual(self.fake_repo.save_calls, [])

    def test_existing_get_returns_200_and_is_default_false(self):
        self.fake_repo.get_result = UserSettingsRepositoryResult(
            settings=UserSettingsStored(updated_at=datetime.now(timezone.utc)),
            is_default=False
        )
        res = client.get("/api/settings")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()["is_default"])

    def test_missing_get_returns_200_and_is_default_true(self):
        self.fake_repo.get_result = UserSettingsRepositoryResult(
            settings=UserSettingsStored(updated_at=datetime.now(timezone.utc)),
            is_default=True
        )
        res = client.get("/api/settings")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["is_default"])

    def test_valid_put_returns_200(self):
        self.fake_repo.put_result = UserSettingsStored(updated_at=datetime.now(timezone.utc))
        res = client.put("/api/settings", json={"currency": "UAH"})
        self.assertEqual(res.status_code, 200)

    def test_put_sends_user_settings_update_type_not_raw_dict(self):
        self.fake_repo.put_result = UserSettingsStored(updated_at=datetime.now(timezone.utc))
        res = client.put("/api/settings", json={"currency": "UAH"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(self.fake_repo.save_calls), 1)
        uid, settings = self.fake_repo.save_calls[0]
        self.assertIsInstance(settings, UserSettingsUpdate)

    def test_put_with_zero_price_returns_zero_price(self):
        now = datetime.now(timezone.utc)
        self.fake_repo.put_result = UserSettingsStored(
            pricing={"profiles": {"REHAU": 0.0}},
            updated_at=now
        )
        res = client.put("/api/settings", json={"pricing": {"profiles": {"REHAU": 0.0}}})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["pricing"]["profiles"]["REHAU"], 0.0)

    def test_put_with_included_in_price_false_returns_bool(self):
        now = datetime.now(timezone.utc)
        self.fake_repo.put_result = UserSettingsStored(
            tax_profile={"included_in_price": False},
            updated_at=now
        )
        res = client.put("/api/settings", json={"tax_profile": {"included_in_price": False}})
        self.assertEqual(res.status_code, 200)
        val = res.json()["tax_profile"]["included_in_price"]
        self.assertEqual(val, False)
        self.assertNotIsInstance(val, float)

    def test_response_does_not_contain_uid_owner_uid_email(self):
        self.fake_repo.get_result = UserSettingsRepositoryResult(
            settings=UserSettingsStored(updated_at=datetime.now(timezone.utc)),
            is_default=False
        )
        res = client.get("/api/settings")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertNotIn("uid", data)
        self.assertNotIn("owner_uid", data)
        self.assertNotIn("email", data)

    def test_get_repository_result_is_not_returned_directly_as_dataclass(self):
        self.fake_repo.get_result = UserSettingsRepositoryResult(
            settings=UserSettingsStored(updated_at=datetime.now(timezone.utc)),
            is_default=False
        )
        res = client.get("/api/settings")
        self.assertEqual(res.status_code, 200)
        self.assertIsInstance(res.json(), dict)

    def test_get_invalid_uid_error_mapping(self):
        self.fake_repo.get_exception = InvalidUIDError("UID cannot contain slashes")
        res = client.get("/api/settings")
        self.assertEqual(res.status_code, 500)
        self.assertEqual(res.json()["detail"], "Authentication context is invalid")
        self.assertNotIn("UID cannot contain slashes", res.text)

    def test_put_invalid_uid_error_mapping(self):
        self.fake_repo.put_exception = InvalidUIDError("UID cannot exceed 128 characters")
        res = client.put("/api/settings", json={})
        self.assertEqual(res.status_code, 500)
        self.assertEqual(res.json()["detail"], "Authentication context is invalid")
        self.assertNotIn("UID cannot exceed 128 characters", res.text)

    def test_put_invalid_stored_model_mapping(self):
        self.fake_repo.put_exception = UserSettingsInvalidDocumentError("Internal model construction failed")
        res = client.put("/api/settings", json={})
        self.assertEqual(res.status_code, 500)
        self.assertEqual(res.json()["detail"], "Stored user settings are invalid")
        self.assertNotIn("Internal model construction failed", res.text)

    def test_put_write_exception_details_not_exposed(self):
        self.fake_repo.put_exception = UserSettingsWriteError("SECRET Firestore project and credentials details")
        res = client.put("/api/settings", json={})
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()["detail"], "Unable to save user settings")
        self.assertNotIn("SECRET", res.text)

if __name__ == "__main__":
    unittest.main()
