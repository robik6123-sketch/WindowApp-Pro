import os
import unittest
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

from fastapi import HTTPException
from fastapi.testclient import TestClient
import main
from main import app, verify_firebase_token

# Immediately stop patchers so that no global mocks are left mutated
cert_patcher.stop()
init_patcher.stop()
client_patcher.stop()
env_patcher.stop()

client = TestClient(app)

class TestOrdersRoute(unittest.TestCase):

    def setUp(self):
        # Clear dependency overrides before each test
        app.dependency_overrides.clear()
        # Save real db client if it exists, otherwise None
        self.real_db = getattr(main.calc, "db", None)
        # Enable USE_FIRESTORE during these tests to force database logic path
        self.real_use_firestore = main.USE_FIRESTORE
        main.USE_FIRESTORE = True

        # Mock settings repository for calculate route tests to prevent 503 database errors
        from user_settings_repository import UserSettingsRepositoryResult
        from settings_models import UserSettingsStored
        from datetime import datetime, timezone
        mock_repo = MagicMock()
        mock_repo.get_user_settings.return_value = UserSettingsRepositoryResult(
            settings=UserSettingsStored(updated_at=datetime.now(timezone.utc)),
            is_default=True
        )
        app.dependency_overrides[main.get_settings_repo] = lambda: mock_repo

    def tearDown(self):
        # Clear overrides and restore original db and USE_FIRESTORE state
        app.dependency_overrides.clear()
        main.USE_FIRESTORE = self.real_use_firestore
        if self.real_db is not None:
            main.calc.db = self.real_db
        elif hasattr(main.calc, "db"):
            delattr(main.calc, "db")

    @patch("auth_dependency.auth.verify_id_token")
    def test_missing_token_401(self, mock_verify):
        """1. Missing token -> 401"""
        # Do not override dependency, make request without header
        response = client.get("/api/orders")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers.get("WWW-Authenticate"), "Bearer")

    @patch("auth_dependency.auth.verify_id_token")
    def test_invalid_token_401(self, mock_verify):
        """2. Invalid token -> 401"""
        # Mock verify_id_token to raise exception
        mock_verify.side_effect = Exception("Invalid token")
        response = client.get("/api/orders", headers={"Authorization": "Bearer invalid_token"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers.get("WWW-Authenticate"), "Bearer")

    def test_valid_user_gets_own_orders(self):
        """3. Valid user receives only their own orders"""
        # Mock verified token user payload
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        # Mock Firestore client
        mock_db = MagicMock()
        main.calc.db = mock_db

        # Mock query results
        mock_doc1 = MagicMock()
        mock_doc1.to_dict.return_value = {
            "order_id": "ORD1",
            "owner_uid": "user_123",
            "timestamp": "2026-06-20T12:00:00"
        }
        mock_doc2 = MagicMock()
        mock_doc2.to_dict.return_value = {
            "order_id": "ORD2",
            "owner_uid": "user_123",
            "timestamp": "2026-06-20T13:00:00"
        }

        # Setup method chaining
        mock_db.collection.return_value.where.return_value.limit.return_value.stream.return_value = [mock_doc1, mock_doc2]

        response = client.get("/api/orders", headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 200)

        orders = response.json()
        self.assertEqual(len(orders), 2)
        # Verify sorting by timestamp descending (ORD2 timestamp is later than ORD1, so ORD2 should be first)
        self.assertEqual(orders[0]["order_id"], "ORD2")
        self.assertEqual(orders[1]["order_id"], "ORD1")

        # Verify Firestore query was filtered exactly by user_123's UID
        mock_db.collection.assert_called_once_with('orders')
        mock_db.collection.return_value.where.assert_called_once_with('owner_uid', '==', 'user_123')

    def test_other_uid_orders_not_returned(self):
        """4. Orders of other UID are not returned (proven by strict query filtering)"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db
        mock_db.collection.return_value.where.return_value.limit.return_value.stream.return_value = []

        response = client.get("/api/orders", headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

        # Verify where was called specifically for user_123
        mock_db.collection.return_value.where.assert_called_once_with('owner_uid', '==', 'user_123')

    def test_email_spoofing_ignored(self):
        """5. Email spoofing in query parameters is ignored"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db
        mock_db.collection.return_value.where.return_value.limit.return_value.stream.return_value = []

        response = client.get("/api/orders?user_email=other_user@example.com", headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 200)

        # Verify where was called specifically for user_123 UID and not the spoofed email
        mock_db.collection.return_value.where.assert_called_once_with('owner_uid', '==', 'user_123')

    def test_uid_spoofing_ignored(self):
        """6. UID spoofing in query parameters is ignored"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db
        mock_db.collection.return_value.where.return_value.limit.return_value.stream.return_value = []

        response = client.get("/api/orders?uid=other_uid_spoof", headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 200)

        # Verify where was called specifically for user_123 UID and not the spoofed UID
        mock_db.collection.return_value.where.assert_called_once_with('owner_uid', '==', 'user_123')

    def test_hardcoded_admin_email_no_extended_access(self):
        """7. Hardcoded admin email no longer grants extended access"""
        # User has admin email but different UID
        user_payload = {"uid": "admin_uid_456", "email": "robik6123@gmail.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db
        mock_db.collection.return_value.where.return_value.limit.return_value.stream.return_value = []

        response = client.get("/api/orders", headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 200)

        # Verify query filters by UID, NOT by email, and does not bypass filtering
        mock_db.collection.return_value.where.assert_called_once_with('owner_uid', '==', 'admin_uid_456')

    def test_no_orders_returns_empty_list(self):
        """8. No orders for user -> 200 with empty list"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db
        mock_db.collection.return_value.where.return_value.limit.return_value.stream.return_value = []

        response = client.get("/api/orders", headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_document_without_owner_uid_not_returned(self):
        """9. Document without owner_uid is not returned to the user"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db

        # Since query filters by owner_uid, a document without owner_uid won't match, so stream returns empty list
        mock_db.collection.return_value.where.return_value.limit.return_value.stream.return_value = []

        response = client.get("/api/orders", headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
        # Since Firestore filter is strict, the query assertion confirms the secure design.
        mock_db.collection.return_value.where.assert_called_once_with('owner_uid', '==', 'user_123')

    def test_firestore_unavailable_returns_503(self):
        """10. Firestore unavailable -> 503 Service Unavailable"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db
        # Simulate Firestore failure
        mock_db.collection.side_effect = Exception("Firestore connection timeout")

        response = client.get("/api/orders", headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "Service Unavailable"})

    def test_response_format_frontend_compatibility(self):
        """11. Successful response format is compatible with frontend and limited to 10 sorted records"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db

        # Create 15 mock orders with timestamps
        mock_docs = []
        for i in range(15):
            mock_doc = MagicMock()
            mock_doc.to_dict.return_value = {
                "order_id": f"ORD{i}",
                "owner_uid": "user_123",
                "timestamp": f"2026-06-20T10:{i:02d}:00"
            }
            mock_docs.append(mock_doc)

        mock_db.collection.return_value.where.return_value.limit.return_value.stream.return_value = mock_docs

        response = client.get("/api/orders", headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 200)
        orders = response.json()

        # Must limit response to 10 items
        self.assertEqual(len(orders), 10)
        # Must be sorted descending (ORD14 is the latest timestamp "10:14", ORD5 is "10:05")
        self.assertEqual(orders[0]["order_id"], "ORD14")
        self.assertEqual(orders[9]["order_id"], "ORD5")

    def test_firestore_query_performed_with_uid_from_token(self):
        """12. Firestore query is strictly executed using the UID from the validated token"""
        user_payload = {"uid": "verified_user_999"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db
        mock_db.collection.return_value.where.return_value.limit.return_value.stream.return_value = []

        response = client.get("/api/orders", headers={"Authorization": "Bearer token123"})
        self.assertEqual(response.status_code, 200)

        # Assert query is specifically for verified_user_999
        mock_db.collection.return_value.where.assert_called_once_with('owner_uid', '==', 'verified_user_999')

    @patch("auth_dependency.auth.verify_id_token")
    def test_create_order_missing_token_401(self, mock_verify):
        """13. create-order: Missing token -> 401"""
        response = client.post("/api/create-order", json={"items": []})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers.get("WWW-Authenticate"), "Bearer")

    @patch("auth_dependency.auth.verify_id_token")
    def test_create_order_invalid_token_401(self, mock_verify):
        """14. create-order: Invalid token -> 401"""
        mock_verify.side_effect = Exception("Invalid token")
        response = client.post("/api/create-order", json={"items": []}, headers={"Authorization": "Bearer invalid_token"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers.get("WWW-Authenticate"), "Bearer")

    def test_create_order_writes_correct_owner_uid(self):
        """15. create-order: Valid user -> 200, writes owner_uid & user_email to Firestore"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db

        cart_data = {"items": [{"input": {"width": 1000.0, "height": 1000.0}}]}
        response = client.post("/api/create-order", json=cart_data, headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        order_id = response.json()["order_id"]

        # Verify Firestore call
        mock_db.collection.assert_called_once_with('orders')
        mock_db.collection.return_value.document.assert_called_once_with(order_id)

        # Verify the saved record
        saved_record = mock_db.collection.return_value.document.return_value.set.call_args[0][0]
        self.assertEqual(saved_record["id"], order_id)
        self.assertEqual(saved_record["owner_uid"], "user_123")
        self.assertEqual(saved_record["user_email"], "user@example.com")
        self.assertEqual(saved_record["cart"]["items"][0]["input"]["width"], 1000.0)
        self.assertIn("timestamp", saved_record)

    def test_create_order_ignores_client_supplied_uid_and_email(self):
        """16. create-order: Ignores client-supplied UID/email in body/query, uses token's values"""
        user_payload = {"uid": "real_uid", "email": "real_email@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db

        cart_data = {
            "items": [{"input": {"width": 1000.0, "height": 1000.0}}],
            "user_email": "fake_email@example.com",
            "owner_uid": "fake_uid",
            "uid": "fake_uid_2"
        }
        # Request with query parameters as well
        response = client.post("/api/create-order?uid=query_uid&user_email=query_email", json=cart_data, headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 200)
        order_id = response.json()["order_id"]

        saved_record = mock_db.collection.return_value.document.return_value.set.call_args[0][0]
        self.assertEqual(saved_record["owner_uid"], "real_uid")
        self.assertEqual(saved_record["user_email"], "real_email@example.com")
        self.assertEqual(saved_record["cart"]["items"][0]["input"]["width"], 1000.0)

    def test_create_order_firestore_error_500(self):
        """17. create-order: Firestore error -> 500 without leaking details"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db
        # Simulate Firestore failure
        mock_db.collection.side_effect = Exception("Secret Firestore network crash details")

        cart_data = {"items": [{"input": {"width": 1000.0, "height": 1000.0}}]}
        response = client.post("/api/create-order", json=cart_data, headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("Secret Firestore network crash details", response.text)
        self.assertEqual(response.json()["detail"], "Internal Server Error")

    @patch("main.generate_cart_pdf")
    def test_generate_quote_owner_receives_pdf(self, mock_generate_pdf):
        """18. generate-quote: Valid owner gets PDF and correct parameters are passed"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db

        mock_doc = MagicMock()
        mock_doc.exists = True
        cart_data = {"items": [], "user_email": "user@example.com"}
        mock_doc.to_dict.return_value = {
            "owner_uid": "user_123",
            "cart": cart_data
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
        mock_generate_pdf.return_value = b"PDF-dummy-content"

        response = client.get("/api/generate-quote/ORD123", headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("content-type"), "application/pdf")
        self.assertEqual(response.content, b"PDF-dummy-content")

        expected_cart_data = cart_data.copy()
        expected_cart_data["order_id"] = "ORD123"
        mock_generate_pdf.assert_called_once_with(expected_cart_data)

    def test_generate_quote_other_user_returns_404(self):
        """19. generate-quote: Non-owner gets 404 Not Found (no 500)"""
        user_payload = {"uid": "attacker_456", "email": "attacker@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db

        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "owner_uid": "user_123"
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        response = client.get("/api/generate-quote/ORD123", headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Замовлення не знайдено")

    def test_generate_quote_missing_order_returns_404(self):
        """20. generate-quote: Missing order -> 404 Not Found (no 500)"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db

        mock_doc = MagicMock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        response = client.get("/api/generate-quote/ORD_MISSING", headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Замовлення не знайдено")

    @patch("auth_dependency.auth.verify_id_token")
    def test_generate_quote_missing_token_401(self, mock_verify):
        """21. generate-quote: Missing token -> 401"""
        response = client.get("/api/generate-quote/ORD123")
        self.assertEqual(response.status_code, 401)

    @patch("auth_dependency.auth.verify_id_token")
    def test_generate_quote_invalid_token_401(self, mock_verify):
        """22. generate-quote: Invalid token -> 401"""
        mock_verify.side_effect = Exception("Invalid token")
        response = client.get("/api/generate-quote/ORD123", headers={"Authorization": "Bearer invalid_token"})
        self.assertEqual(response.status_code, 401)

    def test_generate_quote_firestore_error_500(self):
        """23. generate-quote: Firestore error -> 500 Internal Server Error"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db
        mock_db.collection.side_effect = Exception("DB Network Timeout Details")

        response = client.get("/api/generate-quote/ORD123", headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("DB Network Timeout Details", response.text)
        self.assertEqual(response.json()["detail"], "Internal Server Error")

    @patch("main.generate_cart_pdf")
    def test_generate_quote_pdf_error_500(self, mock_generate_pdf):
        """24. generate-quote: PDF generation error -> 500 without leaking details"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db

        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "owner_uid": "user_123",
            "cart": {"items": []}
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
        mock_generate_pdf.side_effect = Exception("FPDF layout rendering bug details")

        response = client.get("/api/generate-quote/ORD123", headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("FPDF layout rendering bug details", response.text)
        self.assertEqual(response.json()["detail"], "Internal Server Error")

    def test_generate_quote_legacy_order_no_owner_uid_returns_404(self):
        """25. generate-quote: Legacy order without owner_uid -> 404 Not Found"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db

        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "user_email": "user@example.com"
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        response = client.get("/api/generate-quote/ORD123", headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Замовлення не знайдено")

    def test_migrate_endpoint_removed(self):
        """26. migrate: POST /api/migrate endpoint is removed, not registered, and returns 404"""
        from starlette.routing import Route
        # 1. Verify it is not registered in FastAPI routes
        has_post_migrate = False
        for route in main.app.routes:
            if isinstance(route, Route) and route.path == "/api/migrate" and "POST" in route.methods:
                has_post_migrate = True
                break
        self.assertFalse(has_post_migrate)

        # 2. Verify that POST request returns 404
        response = client.post("/api/migrate")
        self.assertEqual(response.status_code, 404)

    @patch("main.calc.calculate_project")
    @patch("auth_dependency.auth.verify_id_token")
    def test_calculate_missing_token_401(self, mock_verify, mock_calc_project):
        """27. calculate: Missing Authorization header -> 401, calculate_project not called"""
        response = client.post("/api/calculate", json={"width": 1000, "height": 1000})
        self.assertEqual(response.status_code, 401)
        mock_calc_project.assert_not_called()

    @patch("main.calc.calculate_project")
    @patch("auth_dependency.auth.verify_id_token")
    def test_calculate_non_bearer_token_401(self, mock_verify, mock_calc_project):
        """28. calculate: Schema is not Bearer -> 401, calculate_project not called"""
        response = client.post("/api/calculate", json={"width": 1000, "height": 1000}, headers={"Authorization": "Basic something"})
        self.assertEqual(response.status_code, 401)
        mock_calc_project.assert_not_called()

    @patch("main.calc.calculate_project")
    @patch("auth_dependency.auth.verify_id_token")
    def test_calculate_invalid_token_401(self, mock_verify, mock_calc_project):
        """29. calculate: Invalid token -> 401, calculate_project not called"""
        mock_verify.side_effect = Exception("Invalid token")
        response = client.post("/api/calculate", json={"width": 1000, "height": 1000}, headers={"Authorization": "Bearer invalid_token"})
        self.assertEqual(response.status_code, 401)
        mock_calc_project.assert_not_called()

    @patch("main.calc.calculate_project")
    @patch("auth_dependency.auth.verify_id_token")
    def test_calculate_expired_token_401(self, mock_verify, mock_calc_project):
        """30. calculate: Expired token -> 401, calculate_project not called"""
        from firebase_admin.exceptions import FirebaseError
        mock_verify.side_effect = FirebaseError(code="auth/id-token-expired", message="Token expired")
        response = client.post("/api/calculate", json={"width": 1000, "height": 1000}, headers={"Authorization": "Bearer expired_token"})
        self.assertEqual(response.status_code, 401)
        mock_calc_project.assert_not_called()

    @patch("main.calc.calculate_project")
    def test_calculate_valid_token_success(self, mock_calc_project):
        """31. calculate: Valid token -> 200, executes calculation exactly once with original order"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        dummy_result = {"price": 1200}
        mock_calc_project.return_value = dummy_result

        order_data = {"width": 1500, "height": 1200}
        response = client.post("/api/calculate", json=order_data, headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), dummy_result)
        mock_calc_project.assert_called_once()
        args, kwargs = mock_calc_project.call_args
        self.assertEqual(args[0], order_data)
        from settings_models import PricingContext
        self.assertIsInstance(args[1], PricingContext)

    @patch("main.calc.calculate_project")
    @patch("auth_dependency.auth.verify_id_token")
    def test_calculate_ignores_client_user_email(self, mock_verify, mock_calc_project):
        """32. calculate: Fake user_email in payload does not bypass auth, returns 401 when token is invalid"""
        mock_verify.side_effect = Exception("Invalid token")

        # Spoofed user_email in the payload, but token is invalid
        order_data = {"width": 1000, "height": 1000, "user_email": "spoof@example.com"}
        response = client.post("/api/calculate", json=order_data, headers={"Authorization": "Bearer invalid_token"})
        self.assertEqual(response.status_code, 401)
        mock_calc_project.assert_not_called()

    @patch("main.calc.calculate_project")
    def test_calculate_valid_payload_variations(self, mock_calc_project):
        """33. calculate: Test variations of valid payloads"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload
        mock_calc_project.return_value = {"status": "success"}

        # 1. Minimal payload
        minimal_payload = {"width": 1200, "height": 1400}
        response = client.post("/api/calculate", json=minimal_payload, headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 200)
        args, kwargs = mock_calc_project.call_args
        self.assertEqual(args[0], minimal_payload)
        from settings_models import PricingContext
        self.assertIsInstance(args[1], PricingContext)

        # 2. Full legitimate payload
        full_payload = {
            "width": 1500,
            "height": 1300,
            "type": "rectangular",
            "material_type": "pvc",
            "profile": "REHAU_Euro_70",
            "glass": "glass_24",
            "color": "white",
            "panels": [
                {"proportion": 50.0, "type": "fixed", "mosquito": False},
                {"proportion": 50.0, "type": "turn_left", "mosquito": True}
            ],
            "sill_length": 1400.0,
            "sill_width": 150.0,
            "window_board": "window_board_plastolit_matte",
            "window_board_length": 1400.0,
            "window_board_depth": 200.0
        }
        response = client.post("/api/calculate", json=full_payload, headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 200)
        expected_full = {
            "width": 1500.0,
            "height": 1300.0,
            "type": "rectangular",
            "material_type": "pvc",
            "profile": "REHAU_Euro_70",
            "glass": "glass_24",
            "color": "white",
            "panels": [
                {"proportion": 50.0, "type": "fixed", "mosquito": False},
                {"proportion": 50.0, "type": "turn_left", "mosquito": True}
            ],
            "sill_length": 1400.0,
            "sill_width": 150.0,
            "window_board": "window_board_plastolit_matte",
            "window_board_length": 1400.0,
            "window_board_depth": 200.0
        }
        args, kwargs = mock_calc_project.call_args
        self.assertEqual(args[0], expected_full)
        self.assertIsInstance(args[1], PricingContext)

        # 3. Arch structure payload
        arch_payload = {
            "width": 1000.0,
            "height": 1500.0,
            "type": "arched",
            "arc_height": 300.0
        }
        response = client.post("/api/calculate", json=arch_payload, headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 200)
        args, kwargs = mock_calc_project.call_args
        self.assertEqual(args[0], arch_payload)
        self.assertIsInstance(args[1], PricingContext)

        # 4. Numeric strings in payload are coerced to float
        numeric_strings_payload = {"width": "1200.5", "height": "1400"}
        response = client.post("/api/calculate", json=numeric_strings_payload, headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 200)
        args, kwargs = mock_calc_project.call_args
        self.assertEqual(args[0], {"width": 1200.5, "height": 1400.0})
        self.assertIsInstance(args[1], PricingContext)

        # 5. Explicit null (None) values
        null_payload = {"width": 1000.0, "height": 1000.0, "arc_height": None}
        response = client.post("/api/calculate", json=null_payload, headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 200)
        args, kwargs = mock_calc_project.call_args
        self.assertEqual(args[0], {"width": 1000.0, "height": 1000.0, "arc_height": None})
        self.assertIsInstance(args[1], PricingContext)

        # 6. Verify calculator receives a plain dict, not a Pydantic object
        called_arg = mock_calc_project.call_args[0][0]
        self.assertIsInstance(called_arg, dict)

    @patch("main.calc.calculate_project")
    def test_calculate_rejects_client_controlled_fields_with_422(self, mock_calc_project):
        """34. calculate: Rejects pricing, commercial, identity, metadata, and unknown fields with 422"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        forbidden_fields = [
            "custom_prices", "tax_profile_id", "markup", "markup_rate",
            "discount", "discount_rate", "tax", "tax_profile",
            "additional_costs", "uid", "owner_uid", "email",
            "user_email", "schema_version", "updated_at",
            "pricing_context", "resolved_prices", "is_default",
            "images", "unexpected_client_field"
        ]

        for field in forbidden_fields:
            with self.subTest(forbidden_field=field):
                payload = {
                    "width": 1000,
                    "height": 1000,
                    field: {} if field in ["custom_prices", "resolved_prices", "pricing_context", "tax_profile", "images"] else "some_val"
                }
                response = client.post("/api/calculate", json=payload, headers={"Authorization": "Bearer valid_token"})
                self.assertEqual(response.status_code, 422, f"Field '{field}' should have been rejected with 422")

        # Unknown nested field in panels
        with self.subTest(nested_field="unknown_nested_field"):
            payload = {
                "width": 1000,
                "height": 1000,
                "panels": [{"proportion": 100, "unknown_field": "error"}]
            }
            response = client.post("/api/calculate", json=payload, headers={"Authorization": "Bearer valid_token"})
            self.assertEqual(response.status_code, 422)

        # Boolean in numeric field width
        with self.subTest(coercion="bool_in_width"):
            payload = {"width": True, "height": 1000}
            response = client.post("/api/calculate", json=payload, headers={"Authorization": "Bearer valid_token"})
            self.assertEqual(response.status_code, 422)

        # NaN / Infinity rejection
        for bad_val in ["NaN", "Infinity", "-Infinity"]:
            with self.subTest(coercion=f"bad_float_{bad_val}"):
                payload = {"width": bad_val, "height": 1000}
                response = client.post("/api/calculate", json=payload, headers={"Authorization": "Bearer valid_token"})
                self.assertEqual(response.status_code, 422)

        mock_calc_project.assert_not_called()

    @patch("main.calc.calculate_project")
    def test_calculate_explicit_null_handling_fix(self, mock_calc_project):
        """35. calculate: Test explicit null and conditional arched validation constraints"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload
        mock_calc_project.return_value = {"status": "success"}

        null_fields = ["sill_length", "sill_width", "window_board_length", "window_board_depth"]

        # 1. Explicit null -> HTTP 422 for sill_length, sill_width, window_board_length, window_board_depth
        for field in null_fields:
            with self.subTest(null_field=field):
                mock_calc_project.reset_mock()
                payload = {"width": 1000, "height": 1000, field: None}
                response = client.post("/api/calculate", json=payload, headers={"Authorization": "Bearer valid_token"})
                self.assertEqual(response.status_code, 422)
                errors = response.json().get("detail", [])
                self.assertTrue(any(field in str(err.get("loc", [])) for err in errors), f"Error should mention {field}")
                mock_calc_project.assert_not_called()

        # 2. Arched + arc_height null -> HTTP 422
        with self.subTest(arched_null_arc_height=True):
            mock_calc_project.reset_mock()
            payload = {"width": 1000, "height": 1000, "type": "arched", "arc_height": None}
            response = client.post("/api/calculate", json=payload, headers={"Authorization": "Bearer valid_token"})
            self.assertEqual(response.status_code, 422)
            errors = response.json().get("detail", [])
            self.assertTrue(any("arc_height" in str(err) or "arched" in str(err) for err in errors))
            mock_calc_project.assert_not_called()

        # 3. Rectangular + arc_height null -> valid request (calculator gets plain dict with arc_height=None)
        with self.subTest(rectangular_null_arc_height=True):
            mock_calc_project.reset_mock()
            payload = {"width": 1000, "height": 1000, "type": "rectangular", "arc_height": None}
            response = client.post("/api/calculate", json=payload, headers={"Authorization": "Bearer valid_token"})
            self.assertEqual(response.status_code, 200)
            mock_calc_project.assert_called_once()
            args, kwargs = mock_calc_project.call_args
            self.assertEqual(args[0], payload)
            from settings_models import PricingContext
            self.assertIsInstance(args[1], PricingContext)

        # 4. Absent sill/window-board numeric fields -> valid, not in dict sent to calculator
        with self.subTest(absent_numeric_fields=True):
            mock_calc_project.reset_mock()
            payload = {"width": 1000, "height": 1000}
            response = client.post("/api/calculate", json=payload, headers={"Authorization": "Bearer valid_token"})
            self.assertEqual(response.status_code, 200)
            called_dict = mock_calc_project.call_args[0][0]
            for field in null_fields:
                self.assertNotIn(field, called_dict)

        # 5. Explicit zero for each numeric field -> valid request, calculator gets 0.0
        with self.subTest(explicit_zero=True):
            mock_calc_project.reset_mock()
            payload = {
                "width": 1000,
                "height": 1000,
                "sill_length": 0,
                "sill_width": 0.0,
                "window_board_length": 0,
                "window_board_depth": 0.0
            }
            response = client.post("/api/calculate", json=payload, headers={"Authorization": "Bearer valid_token"})
            self.assertEqual(response.status_code, 200)
            called_dict = mock_calc_project.call_args[0][0]
            for field in null_fields:
                self.assertEqual(called_dict[field], 0.0)

        # 6. Numeric string for these fields -> coerced to float
        with self.subTest(numeric_string_coercion=True):
            mock_calc_project.reset_mock()
            payload = {
                "width": 1000,
                "height": 1000,
                "sill_length": "1200.5",
                "sill_width": "150",
                "window_board_length": "1400",
                "window_board_depth": "200.5"
            }
            response = client.post("/api/calculate", json=payload, headers={"Authorization": "Bearer valid_token"})
            self.assertEqual(response.status_code, 200)
            called_dict = mock_calc_project.call_args[0][0]
            self.assertEqual(called_dict["sill_length"], 1200.5)
            self.assertEqual(called_dict["sill_width"], 150.0)
            self.assertEqual(called_dict["window_board_length"], 1400.0)
            self.assertEqual(called_dict["window_board_depth"], 200.5)

        # 7. Bool for these fields -> HTTP 422
        for field in null_fields:
            with self.subTest(bool_in_field=field):
                mock_calc_project.reset_mock()
                payload = {"width": 1000, "height": 1000, field: True}
                response = client.post("/api/calculate", json=payload, headers={"Authorization": "Bearer valid_token"})
                self.assertEqual(response.status_code, 422)
                errors = response.json().get("detail", [])
                self.assertTrue(any(field in str(err.get("loc", [])) for err in errors), f"Error should mention {field}")
                mock_calc_project.assert_not_called()

    @patch("main.calc.calculate_project")
    def test_calculate_missing_resolved_price_error_mapping(self, mock_calc_project):
        """calculate: MissingResolvedPriceError -> 500, safe generic message"""
        from calculator import MissingResolvedPriceError
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload
        mock_calc_project.side_effect = MissingResolvedPriceError("SECRET internal price id")

        payload = {"width": 1000, "height": 1000}
        response = client.post("/api/calculate", json=payload, headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "Внутрішня помилка розрахунку ціни")
        self.assertNotIn("SECRET", response.text)
        self.assertNotIn("internal price id", response.text)

    @patch("main.calc.calculate_project")
    def test_calculate_unknown_material_error_mapping(self, mock_calc_project):
        """calculate: UnknownMaterialError -> 400, safe generic message"""
        from calculator import UnknownMaterialError
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload
        mock_calc_project.side_effect = UnknownMaterialError("SECRET unknown profile WDS_X")

        payload = {"width": 1000, "height": 1000}
        response = client.post("/api/calculate", json=payload, headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Невідомий матеріал або конфігурація")
        self.assertNotIn("SECRET", response.text)
        self.assertNotIn("WDS_X", response.text)

    @patch("main.calc.calculate_project")
    def test_calculate_calculator_pricing_error_mapping(self, mock_calc_project):
        """calculate: CalculatorPricingError -> 500, safe generic message"""
        from calculator import CalculatorPricingError
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload
        mock_calc_project.side_effect = CalculatorPricingError("SECRET malformed multiplier")

        payload = {"width": 1000, "height": 1000}
        response = client.post("/api/calculate", json=payload, headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "Помилка конфігурації калькулятора")
        self.assertNotIn("SECRET", response.text)

    @patch("main.calc.calculate_project")
    def test_create_order_ignores_client_result_and_recalculates(self, mock_calc_project):
        """create-order: Client-submitted result is completely ignored and trusted result is stored"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        # Mock settings repo to match expected commercial adjustments
        from user_settings_repository import UserSettingsRepositoryResult
        from settings_models import UserSettingsStored, CommercialSettings, TaxProfileSettings
        from datetime import datetime, timezone
        settings = UserSettingsStored(
            updated_at=datetime.now(timezone.utc),
            commercial=CommercialSettings(markup_rate=10.0, discount_rate=5.0),
            tax_profile=TaxProfileSettings(name="PDV", rate=0.20, included_in_price=False)
        )
        mock_repo = MagicMock()
        mock_repo.get_user_settings.return_value = UserSettingsRepositoryResult(
            settings=settings,
            is_default=False
        )
        app.dependency_overrides[main.get_settings_repo] = lambda: mock_repo

        # Mock calculator return value
        trusted_result = {
            "status": "success",
            "net_price": 3657.50,
            "vat_amount": 731.50,
            "legal_reference": "Платник ПДВ (20%)",
            "metrics": {"area": 1.68, "weight": 45.2, "perimeter": 5.2},
            "cost_details": {"profile": 1800.0, "glass": 1200.0, "hardware": 500.0, "extras": 700.60, "total": 4389.00},
            "commercial_breakdown": {
                "materials_subtotal": 3000.00,
                "additional_costs_total": 500.00,
                "additional_costs_breakdown": [],
                "subtotal_before_markup": 3500.00,
                "markup_rate": 10.0,
                "markup_amount": 350.00,
                "subtotal_after_markup": 3850.00,
                "discount_rate": 5.0,
                "discount_amount": 192.50,
                "adjusted_subtotal": 3657.50,
                "tax_rate": 0.20,
                "tax_included": False,
                "net_price": 3657.50,
                "vat_amount": 731.50,
                "total": 4389.00
            }
        }
        mock_calc_project.return_value = trusted_result

        mock_db = MagicMock()
        main.calc.db = mock_db

        cart_data = {
            "items": [
                {
                    "input": {
                        "width": 1200.0,
                        "height": 1400.0,
                        "profile": "REHAU_Euro_70",
                        "glass": "glass_24",
                        "color": "white"
                    },
                    "result": {
                        "total": 0.01,
                        "cost_details": {"total": 0.01},
                        "commercial_breakdown": {"total": 0.01}
                    }
                }
            ]
        }

        response = client.post("/api/create-order", json=cart_data, headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 200)

        saved_record = mock_db.collection.return_value.document.return_value.set.call_args[0][0]
        # Verify that client-submitted result of 0.01 was ignored and replaced with trusted_result
        self.assertEqual(saved_record["cart"]["items"][0]["result"]["commercial_breakdown"]["total"], 4389.00)
        self.assertEqual(saved_record["grand_total"], 4389.00)
        self.assertEqual(saved_record["grand_net"], 3657.50)
        self.assertEqual(saved_record["grand_vat"], 731.50)
        self.assertEqual(saved_record["calculation_provenance"], "server_calculated")

    def test_create_order_ignores_client_uid_and_email(self):
        """create-order: Client-submitted owner_uid, uid and user_email are ignored, token values are used"""
        user_payload = {"uid": "user_real", "email": "real@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        # Mock settings repository
        from user_settings_repository import UserSettingsRepositoryResult
        from settings_models import UserSettingsStored
        from datetime import datetime, timezone
        mock_repo = MagicMock()
        mock_repo.get_user_settings.return_value = UserSettingsRepositoryResult(
            settings=UserSettingsStored(updated_at=datetime.now(timezone.utc)),
            is_default=True
        )
        app.dependency_overrides[main.get_settings_repo] = lambda: mock_repo

        mock_db = MagicMock()
        main.calc.db = mock_db

        with patch("main.calc.calculate_project") as mock_calc_project:
            mock_calc_project.return_value = {
                "status": "success",
                "net_price": 100.0,
                "vat_amount": 20.0,
                "cost_details": {"total": 120.0},
                "commercial_breakdown": {
                    "materials_subtotal": 100.0,
                    "additional_costs_total": 0.0,
                    "additional_costs_breakdown": [],
                    "net_price": 100.0,
                    "vat_amount": 20.0,
                    "total": 120.0
                }
            }
            cart_data = {
                "items": [
                    {
                        "input": {"width": 1000.0, "height": 1000.0},
                        "result": {}
                    }
                ],
                "owner_uid": "attacker_uid",
                "uid": "attacker_uid2",
                "user_email": "attacker@example.com"
            }
            response = client.post("/api/create-order?uid=query_uid&user_email=query_email", json=cart_data, headers={"Authorization": "Bearer valid_token"})
            self.assertEqual(response.status_code, 200)

            saved_record = mock_db.collection.return_value.document.return_value.set.call_args[0][0]
            self.assertEqual(saved_record["owner_uid"], "user_real")
            self.assertEqual(saved_record["user_email"], "real@example.com")

    def test_create_order_missing_settings_uses_defaults(self):
        """create-order: Missing settings document in DB uses approved default settings"""
        user_payload = {"uid": "user_new", "email": "new@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        from user_settings_repository import UserSettingsRepositoryResult
        from settings_models import UserSettingsStored
        from datetime import datetime, timezone
        mock_repo = MagicMock()
        mock_repo.get_user_settings.return_value = UserSettingsRepositoryResult(
            settings=UserSettingsStored(updated_at=datetime.now(timezone.utc)),
            is_default=True
        )
        app.dependency_overrides[main.get_settings_repo] = lambda: mock_repo

        mock_db = MagicMock()
        main.calc.db = mock_db

        with patch("main.calc.calculate_project") as mock_calc_project:
            mock_calc_project.return_value = {
                "status": "success",
                "net_price": 100.0,
                "vat_amount": 20.0,
                "cost_details": {"total": 120.0},
                "commercial_breakdown": {
                    "materials_subtotal": 100.0,
                    "additional_costs_total": 0.0,
                    "additional_costs_breakdown": [],
                    "net_price": 100.0,
                    "vat_amount": 20.0,
                    "total": 120.0
                }
            }
            cart_data = {"items": [{"input": {"width": 1000.0, "height": 1000.0}}]}
            response = client.post("/api/create-order", json=cart_data, headers={"Authorization": "Bearer valid_token"})
            self.assertEqual(response.status_code, 200)
            mock_repo.get_user_settings.assert_called_once_with("user_new")

    def test_create_order_settings_unreadable_503(self):
        """create-order: Settings repository unreadable -> returns 503"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        from user_settings_repository import UserSettingsNotReadableError
        mock_repo = MagicMock()
        mock_repo.get_user_settings.side_effect = UserSettingsNotReadableError("Firestore read timeout")
        app.dependency_overrides[main.get_settings_repo] = lambda: mock_repo

        cart_data = {"items": [{"input": {"width": 1000.0, "height": 1000.0}}]}
        response = client.post("/api/create-order", json=cart_data, headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "User settings are temporarily unavailable")

    def test_create_order_invalid_stored_settings_500(self):
        """create-order: Invalid stored user settings -> returns 500"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        from user_settings_repository import UserSettingsInvalidDocumentError
        mock_repo = MagicMock()
        mock_repo.get_user_settings.side_effect = UserSettingsInvalidDocumentError("Stored settings schema version mismatch")
        app.dependency_overrides[main.get_settings_repo] = lambda: mock_repo

        cart_data = {"items": [{"input": {"width": 1000.0, "height": 1000.0}}]}
        response = client.post("/api/create-order", json=cart_data, headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "Stored user settings are invalid")

    def test_create_order_invalid_cart_structure_422(self):
        """create-order: Invalid cart structures are rejected with 422 without writing to DB"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db

        invalid_structures = [
            [],
            "not a dict",
            {"not_items": []},
            {"items": "not a list"},
            {"items": []},
            {"items": ["not a dict"]},
            {"items": [{"not_input": {}}]},
            {"items": [{"input": "not a dict"}]}
        ]

        for cart in invalid_structures:
            with self.subTest(cart=cart):
                mock_db.reset_mock()
                response = client.post("/api/create-order", json=cart, headers={"Authorization": "Bearer valid_token"})
                self.assertEqual(response.status_code, 422)
                mock_db.collection.assert_not_called()

    def test_create_order_invalid_item_n_fails_transactionally(self):
        """create-order: If item N parameters are invalid, validation fails with 422 and no write happens"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db

        cart_data = {
            "items": [
                {"input": {"width": 1000, "height": 1000}},
                {"input": {"width": "invalid_width", "height": 1000}}
            ]
        }

        response = client.post("/api/create-order", json=cart_data, headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("Item 1 parameters validation failed", response.json()["detail"])
        mock_db.collection.assert_not_called()

    @patch("main.calc.calculate_project")
    def test_create_order_calculation_failure_prevents_write(self, mock_calc_project):
        """create-order: CalculatorPricingError on item N prevents order creation and returns 500"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        from calculator import CalculatorPricingError
        mock_calc_project.side_effect = CalculatorPricingError("Something went wrong during calculation")

        mock_db = MagicMock()
        main.calc.db = mock_db

        cart_data = {"items": [{"input": {"width": 1000, "height": 1000}}]}
        response = client.post("/api/create-order", json=cart_data, headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "Помилка конфігурації калькулятора")
        mock_db.collection.assert_not_called()

    @patch("main.calc.calculate_project")
    def test_create_order_single_item_with_fixed_per_order_works(self, mock_calc_project):
        """create-order: Single-item order with active fixed_per_order works perfectly"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        from user_settings_repository import UserSettingsRepositoryResult
        from settings_models import UserSettingsStored, AdditionalCostSettings, CalculationType
        from datetime import datetime, timezone
        settings = UserSettingsStored(
            updated_at=datetime.now(timezone.utc),
            additional_costs=[
                AdditionalCostSettings(
                    id="delivery",
                    name="Delivery",
                    calculation_type=CalculationType.fixed_per_order,
                    value=500.0,
                    enabled=True
                )
            ]
        )
        mock_repo = MagicMock()
        mock_repo.get_user_settings.return_value = UserSettingsRepositoryResult(
            settings=settings,
            is_default=False
        )
        app.dependency_overrides[main.get_settings_repo] = lambda: mock_repo

        mock_db = MagicMock()
        main.calc.db = mock_db

        mock_calc_project.return_value = {
            "status": "success",
            "net_price": 100.0,
            "vat_amount": 20.0,
            "cost_details": {"total": 120.0},
            "commercial_breakdown": {
                "materials_subtotal": 100.0,
                "additional_costs_total": 0.0,
                "additional_costs_breakdown": [],
                "net_price": 100.0,
                "vat_amount": 20.0,
                "total": 120.0
            }
        }

        cart_data = {"items": [{"input": {"width": 1000, "height": 1000}}]}
        response = client.post("/api/create-order", json=cart_data, headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 200)
        mock_db.collection.assert_called_once_with('orders')

    def test_create_order_multi_item_with_fixed_per_order_succeeds(self):
        """create-order: Multi-item order with active fixed_per_order succeeds in Stage G"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        from user_settings_repository import UserSettingsRepositoryResult
        from settings_models import UserSettingsStored, AdditionalCostSettings, CalculationType
        from datetime import datetime, timezone
        settings = UserSettingsStored(
            updated_at=datetime.now(timezone.utc),
            additional_costs=[
                AdditionalCostSettings(
                    id="delivery",
                    name="Delivery",
                    calculation_type=CalculationType.fixed_per_order,
                    value=500.0,
                    enabled=True
                )
            ]
        )
        mock_repo = MagicMock()
        mock_repo.get_user_settings.return_value = UserSettingsRepositoryResult(
            settings=settings,
            is_default=False
        )
        app.dependency_overrides[main.get_settings_repo] = lambda: mock_repo

        mock_db = MagicMock()
        main.calc.db = mock_db

        cart_data = {
            "items": [
                {"input": {"width": 1000, "height": 1000}},
                {"input": {"width": 1200, "height": 1200}}
            ]
        }

        with patch("main.calc.calculate_project") as mock_calc_project:
            mock_calc_project.return_value = {
                "status": "success",
                "net_price": 100.0,
                "vat_amount": 20.0,
                "cost_details": {"total": 120.0},
                "commercial_breakdown": {
                    "materials_subtotal": 100.0,
                    "additional_costs_total": 0.0,
                    "additional_costs_breakdown": [],
                    "net_price": 100.0,
                    "vat_amount": 20.0,
                    "total": 120.0
                }
            }
            response = client.post("/api/create-order", json=cart_data, headers={"Authorization": "Bearer valid_token"})
            self.assertEqual(response.status_code, 200)
            mock_db.collection.assert_called_once_with('orders')


    @patch("main.calc.calculate_project")
    def test_create_order_multi_item_without_fixed_per_order_works(self, mock_calc_project):
        """create-order: Multi-item order without active fixed_per_order works perfectly"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        from user_settings_repository import UserSettingsRepositoryResult
        from settings_models import UserSettingsStored, AdditionalCostSettings, CalculationType, TaxProfileSettings
        from datetime import datetime, timezone
        settings = UserSettingsStored(
            updated_at=datetime.now(timezone.utc),
            additional_costs=[
                AdditionalCostSettings(
                    id="delivery",
                    name="Delivery",
                    calculation_type=CalculationType.fixed_per_order,
                    value=500.0,
                    enabled=False
                ),
                AdditionalCostSettings(
                    id="installation",
                    name="Installation",
                    calculation_type=CalculationType.fixed_per_item,
                    value=150.0,
                    enabled=True
                )
            ],
            tax_profile=TaxProfileSettings(name="PDV", rate=0.20, included_in_price=False)
        )
        mock_repo = MagicMock()
        mock_repo.get_user_settings.return_value = UserSettingsRepositoryResult(
            settings=settings,
            is_default=False
        )
        app.dependency_overrides[main.get_settings_repo] = lambda: mock_repo

        mock_db = MagicMock()
        main.calc.db = mock_db

        mock_calc_project.return_value = {
            "status": "success",
            "net_price": 100.0,
            "vat_amount": 20.0,
            "cost_details": {"total": 120.0},
            "commercial_breakdown": {
                "materials_subtotal": 100.0,
                "additional_costs_total": 0.0,
                "additional_costs_breakdown": [],
                "net_price": 100.0,
                "vat_amount": 20.0,
                "total": 120.0
            }
        }

        cart_data = {
            "items": [
                {"input": {"width": 1000, "height": 1000}},
                {"input": {"width": 1200, "height": 1200}}
            ]
        }
        response = client.post("/api/create-order", json=cart_data, headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 200)
        mock_db.collection.assert_called_once_with('orders')

        saved_record = mock_db.collection.return_value.document.return_value.set.call_args[0][0]
        self.assertEqual(saved_record["grand_net"], 200.0)
        self.assertEqual(saved_record["grand_vat"], 40.0)
        self.assertEqual(saved_record["grand_total"], 240.0)

    @patch("main.calc.calculate_project")
    def test_create_order_pricing_context_not_mutated_and_deterministic(self, mock_calc_project):
        """create-order: PricingContext remains unmutated between multiple calls and repeated calculations are deterministic"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        from user_settings_repository import UserSettingsRepositoryResult
        from settings_models import UserSettingsStored, CommercialSettings, TaxProfileSettings
        from datetime import datetime, timezone
        settings = UserSettingsStored(
            updated_at=datetime.now(timezone.utc),
            commercial=CommercialSettings(markup_rate=10.0, discount_rate=5.0),
            tax_profile=TaxProfileSettings(name="PDV", rate=0.20, included_in_price=False)
        )
        mock_repo = MagicMock()
        mock_repo.get_user_settings.return_value = UserSettingsRepositoryResult(
            settings=settings,
            is_default=False
        )
        app.dependency_overrides[main.get_settings_repo] = lambda: mock_repo

        mock_db = MagicMock()
        main.calc.db = mock_db

        mock_calc_project.return_value = {
            "status": "success",
            "net_price": 100.0,
            "vat_amount": 20.0,
            "cost_details": {"total": 120.0},
            "commercial_breakdown": {
                "materials_subtotal": 100.0,
                "additional_costs_total": 0.0,
                "additional_costs_breakdown": [],
                "net_price": 100.0,
                "vat_amount": 20.0,
                "total": 120.0
            }
        }

        cart_data = {
            "items": [
                {"input": {"width": 1000, "height": 1000}},
                {"input": {"width": 1200, "height": 1200}}
            ]
        }
        response = client.post("/api/create-order", json=cart_data, headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 200)

        self.assertEqual(mock_calc_project.call_count, 2)
        ctx1 = mock_calc_project.call_args_list[0][0][1]
        ctx2 = mock_calc_project.call_args_list[1][0][1]

        self.assertEqual(ctx1.commercial.markup_rate, 10.0)
        self.assertEqual(ctx1.commercial.discount_rate, 5.0)
        self.assertEqual(ctx1.tax_profile.rate, 0.20)
        self.assertEqual(ctx2.commercial.markup_rate, 10.0)
        self.assertEqual(ctx2.commercial.discount_rate, 5.0)
        self.assertEqual(ctx2.tax_profile.rate, 0.20)

    @patch("main.calc.calculate_project")
    def test_create_order_persisted_input_retains_images_compatibility(self, mock_calc_project):
        """create-order: Optional client images are validated and retained in persisted input for PDF compatibility"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db

        mock_calc_project.return_value = {
            "status": "success",
            "net_price": 100.0,
            "vat_amount": 20.0,
            "cost_details": {"total": 120.0},
            "commercial_breakdown": {
                "materials_subtotal": 100.0,
                "additional_costs_total": 0.0,
                "additional_costs_breakdown": [],
                "net_price": 100.0,
                "vat_amount": 20.0,
                "total": 120.0
            }
        }

        images_payload = {
            "front": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
            "outside": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
            "side": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        }
        cart_data = {
            "items": [
                {
                    "input": {
                        "width": 1000.0,
                        "height": 1000.0,
                        "images": images_payload
                    }
                }
            ]
        }

        response = client.post("/api/create-order", json=cart_data, headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 200)

        saved_record = mock_db.collection.return_value.document.return_value.set.call_args[0][0]
        self.assertEqual(saved_record["cart"]["items"][0]["input"]["images"], images_payload)

        called_dict = mock_calc_project.call_args[0][0]
        self.assertNotIn("images", called_dict)

    def test_create_order_invalid_image_contract_rejected_with_422(self):
        """create-order: Invalid image contracts are rejected with 422"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db

        invalid_images = [
            "not a dict",
            {"unexpected_key": "data:image/png;base64,iVBOR..."},
            {"front": 12345},
            {"front": "invalid_base64_format"}
        ]

        for img in invalid_images:
            with self.subTest(img=img):
                mock_db.reset_mock()
                cart_data = {
                    "items": [
                        {
                            "input": {
                                "width": 1000.0,
                                "height": 1000.0,
                                "images": img
                            }
                        }
                    ]
                }
                response = client.post("/api/create-order", json=cart_data, headers={"Authorization": "Bearer valid_token"})
                self.assertEqual(response.status_code, 422)
                mock_db.collection.assert_not_called()

    @patch("main.generate_cart_pdf")
    def test_generate_quote_legacy_order_without_provenance_works(self, mock_generate_pdf):
        """generate-quote: Legacy order without calculation_provenance is rendered as-is by PDF generator"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db

        mock_doc = MagicMock()
        mock_doc.exists = True
        cart_data = {
            "items": [
                {
                    "input": {"width": 1000.0, "height": 1000.0},
                    "result": {"cost_details": {"total": 5000.00}}
                }
            ]
        }
        mock_doc.to_dict.return_value = {
            "owner_uid": "user_123",
            "cart": cart_data
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
        mock_generate_pdf.return_value = b"PDF-legacy-content"

        response = client.get("/api/generate-quote/ORD_LEGACY", headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"PDF-legacy-content")

        expected_cart_data = cart_data.copy()
        expected_cart_data["order_id"] = "ORD_LEGACY"
        mock_generate_pdf.assert_called_once_with(expected_cart_data)

    def test_create_order_database_write_failure_500(self):
        """create-order: Firestore database write failure -> returns 500 without leaking details"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db
        mock_db.collection.return_value.document.return_value.set.side_effect = Exception("Firestore database write error details")

        with patch("main.calc.calculate_project") as mock_calc_project:
            mock_calc_project.return_value = {
                "status": "success",
                "net_price": 100.0,
                "vat_amount": 20.0,
                "cost_details": {"total": 120.0},
                "commercial_breakdown": {
                    "materials_subtotal": 100.0,
                    "additional_costs_total": 0.0,
                    "additional_costs_breakdown": [],
                    "net_price": 100.0,
                    "vat_amount": 20.0,
                    "total": 120.0
                }
            }
            cart_data = {"items": [{"input": {"width": 1000, "height": 1000}}]}
            response = client.post("/api/create-order", json=cart_data, headers={"Authorization": "Bearer valid_token"})
            self.assertEqual(response.status_code, 500)
            self.assertNotIn("Firestore database write error details", response.text)
            self.assertEqual(response.json()["detail"], "Internal Server Error")

    def test_create_order_dimension_limits(self):
        """create-order: dimension validation for width and height limits"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db

        invalid_cases = [
            {"width": 0, "height": 1000},
            {"width": -1, "height": 1000},
            {"width": 4001, "height": 1000},
            {"width": 1000, "height": 0},
            {"width": 1000, "height": -1},
            {"width": 1000, "height": 3001},
        ]

        for case in invalid_cases:
            with self.subTest(case=case):
                mock_db.reset_mock()
                cart_data = {"items": [{"input": case}]}
                response = client.post("/api/create-order", json=cart_data, headers={"Authorization": "Bearer valid_token"})
                self.assertEqual(response.status_code, 422)
                self.assertIn("Габарити перевищують інженерні норми", response.json()["detail"])
                mock_db.collection.assert_not_called()

    def test_create_order_image_validation(self):
        """create-order: image validation contracts and base64 parsing"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db

        # 1. Valid cases (should be accepted)
        valid_cases = [
            {"front": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="},
            {"outside": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="},
            {"side": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="},
        ]
        for idx, images in enumerate(valid_cases):
            with self.subTest(valid_images=images):
                mock_db.reset_mock()
                cart_data = {
                    "items": [{
                        "input": {
                            "width": 1000,
                            "height": 1000,
                            "images": images
                        }
                    }]
                }
                response = client.post("/api/create-order", json=cart_data, headers={"Authorization": "Bearer valid_token"})
                self.assertEqual(response.status_code, 200, response.text)
                mock_db.collection.assert_called_once_with('orders')

        # 2. Invalid cases (should return 422 and perform no write)
        invalid_cases = [
            {"front": "data:image/png;base64,!!!NOT_BASE64!!!"},
            {"front": "data:image/png;base64,"},
            {"front": "httpjunk"},
            {"front": "https://example.com/x.png"},
            {"front": "data:image/jpeg;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="}, # unsupported MIME type
            {"back": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="}, # unexpected image key
            {"front": 123}, # non-string value
        ]
        for idx, images in enumerate(invalid_cases):
            with self.subTest(invalid_images=images):
                mock_db.reset_mock()
                cart_data = {
                    "items": [{
                        "input": {
                            "width": 1000,
                            "height": 1000,
                            "images": images
                        }
                    }]
                }
                response = client.post("/api/create-order", json=cart_data, headers={"Authorization": "Bearer valid_token"})
                self.assertEqual(response.status_code, 422)
                mock_db.collection.assert_not_called()

    def test_create_order_multi_item_atomicity_malformed_image(self):
        """create-order: multi-item order where item 2 has malformed image results in 422 and no write"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db

        cart_data = {
            "items": [
                {
                    "input": {
                        "width": 1000,
                        "height": 1000,
                        "images": {"front": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="}
                    }
                },
                {
                    "input": {
                        "width": 1000,
                        "height": 1000,
                        "images": {"front": "httpjunk"}
                    }
                }
            ]
        }
        response = client.post("/api/create-order", json=cart_data, headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 422)
        mock_db.collection.assert_not_called()

    def test_create_order_image_exceeds_individual_limit(self):
        """create-order: Single image exceeding individual 150 KB limit returns 422 and prevents write"""
        import base64
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db

        # Create base64 payload of 150 KB + 1 byte (153601 bytes)
        large_bytes = b"A" * (150 * 1024 + 1)
        large_b64 = base64.b64encode(large_bytes).decode("utf-8")
        oversized_image = f"data:image/png;base64,{large_b64}"

        cart_data = {
            "items": [
                {
                    "input": {
                        "width": 1000,
                        "height": 1000,
                        "images": {"front": oversized_image}
                    }
                }
            ]
        }

        response = client.post("/api/create-order", json=cart_data, headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("Item 0 image front exceeds allowed size of 150 KB", response.json()["detail"])
        mock_db.collection.assert_not_called()

    def test_create_order_images_exceed_order_limit(self):
        """create-order: Total images size exceeding cumulative 600 KB limit returns 422 and prevents write"""
        import base64
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db

        # 5 images of 130 KB each (130 * 1024 bytes) -> Total 650 KB (exceeds 600 KB cumulative limit)
        image_bytes = b"A" * (130 * 1024)
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        valid_b64_image = f"data:image/png;base64,{image_b64}"

        cart_data = {
            "items": [
                {
                    "input": {
                        "width": 1000,
                        "height": 1000,
                        "images": {"front": valid_b64_image}
                    }
                }
                for _ in range(5)
            ]
        }

        response = client.post("/api/create-order", json=cart_data, headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("Order total images size exceeds limit of 600 KB", response.json()["detail"])
        mock_db.collection.assert_not_called()

    @patch("main.calc.calculate_project")
    def test_create_order_multi_item_atomicity_calculation_failure(self, mock_calc_project):
        """create-order: multi-item order where item 2 calculation raises exception results in no write"""
        from calculator import CalculatorPricingError
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db

        def side_effect(payload, pricing_context):
            if payload.get("width") == 1001:
                return {
                    "status": "success",
                    "net_price": 100.0,
                    "vat_amount": 20.0,
                    "cost_details": {"total": 120.0},
                    "commercial_breakdown": {
                        "materials_subtotal": 100.0,
                        "additional_costs_total": 0.0,
                        "additional_costs_breakdown": [],
                        "net_price": 100.0,
                        "vat_amount": 20.0,
                        "total": 120.0
                    }
                }
            else:
                raise CalculatorPricingError("Simulated calculator failure")

        mock_calc_project.side_effect = side_effect

        cart_data = {
            "items": [
                {"input": {"width": 1001, "height": 1000}},
                {"input": {"width": 1002, "height": 1000}}
            ]
        }
        response = client.post("/api/create-order", json=cart_data, headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 500)
        mock_db.collection.assert_not_called()

    def test_list_orders_ignores_client_uid_email_params(self):
        """test_list_orders_ignores_client_uid_email_params: List orders endpoint ignores client parameters for uid/email"""
        user_payload = {"uid": "real_uid", "email": "real@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db
        mock_db.collection.return_value.where.return_value.limit.return_value.stream.return_value = []

        response = client.get("/api/orders?uid=attacker_uid&email=attacker@example.com&owner_uid=attacker_uid_2", headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 200)
        # Verify query filters specifically by real_uid
        mock_db.collection.return_value.where.assert_called_once_with('owner_uid', '==', 'real_uid')

    def test_list_orders_sorting_robust_to_missing_timestamp(self):
        """test_list_orders_sorting_robust_to_missing_timestamp: list orders handles missing timestamp robustly"""
        user_payload = {"uid": "user_123"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db

        from datetime import datetime
        mock_doc1 = MagicMock()
        mock_doc1.to_dict.return_value = {"order_id": "ORD1", "owner_uid": "user_123"} # missing timestamp
        mock_doc2 = MagicMock()
        mock_doc2.to_dict.return_value = {"order_id": "ORD2", "owner_uid": "user_123", "timestamp": datetime(2026, 6, 20, 12, 0, 0)}
        mock_doc3 = MagicMock()
        mock_doc3.to_dict.return_value = {"order_id": "ORD3", "owner_uid": "user_123", "timestamp": datetime(2026, 6, 20, 15, 0, 0)}

        mock_db.collection.return_value.where.return_value.limit.return_value.stream.return_value = [mock_doc1, mock_doc2, mock_doc3]

        response = client.get("/api/orders", headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 200)
        orders = response.json()
        self.assertEqual(len(orders), 3)
        # ORD3 (latest) should be first, then ORD2, then ORD1 (missing timestamp goes to the end)
        self.assertEqual(orders[0]["order_id"], "ORD3")
        self.assertEqual(orders[1]["order_id"], "ORD2")
        self.assertEqual(orders[2]["order_id"], "ORD1")

    def test_list_orders_sorting_robust_to_invalid_timestamp(self):
        """test_list_orders_sorting_robust_to_invalid_timestamp: list orders handles invalid timestamp format robustly"""
        user_payload = {"uid": "user_123"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db

        mock_doc1 = MagicMock()
        mock_doc1.to_dict.return_value = {"order_id": "ORD1", "owner_uid": "user_123", "timestamp": "invalid_timestamp_str"}
        mock_doc2 = MagicMock()
        mock_doc2.to_dict.return_value = {"order_id": "ORD2", "owner_uid": "user_123", "timestamp": "2026-06-20T12:00:00"}
        mock_doc3 = MagicMock()
        mock_doc3.to_dict.return_value = {"order_id": "ORD3", "owner_uid": "user_123", "timestamp": 123456789} # invalid type

        mock_db.collection.return_value.where.return_value.limit.return_value.stream.return_value = [mock_doc1, mock_doc2, mock_doc3]

        response = client.get("/api/orders", headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 200)
        orders = response.json()
        self.assertEqual(len(orders), 3)
        # ORD2 (valid) should be first, then ORD1 and ORD3 (invalid types/formats go to the end)
        self.assertEqual(orders[0]["order_id"], "ORD2")
        self.assertIn(orders[1]["order_id"], ["ORD1", "ORD3"])
        self.assertIn(orders[2]["order_id"], ["ORD1", "ORD3"])

    def test_get_owned_order_or_404_success(self):
        """test_get_owned_order_or_404_success: helper successfully returns data"""
        from main import get_owned_order_or_404
        mock_db = MagicMock()
        main.calc.db = mock_db

        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"owner_uid": "user_123", "data": "yes"}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        result = get_owned_order_or_404("ORD123", "user_123")
        self.assertEqual(result["data"], "yes")

    def test_get_owned_order_or_404_missing_returns_404(self):
        """test_get_owned_order_or_404_missing_returns_404: helper raises 404 if order does not exist"""
        from main import get_owned_order_or_404
        mock_db = MagicMock()
        main.calc.db = mock_db

        mock_doc = MagicMock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with self.assertRaises(HTTPException) as exc:
            get_owned_order_or_404("ORD123", "user_123")
        self.assertEqual(exc.exception.status_code, 404)
        self.assertEqual(exc.exception.detail, "Замовлення не знайдено")

    def test_get_owned_order_or_404_wrong_owner_returns_404(self):
        """test_get_owned_order_or_404_wrong_owner_returns_404: helper raises 404 if user is not the owner"""
        from main import get_owned_order_or_404
        mock_db = MagicMock()
        main.calc.db = mock_db

        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"owner_uid": "another_user"}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with self.assertRaises(HTTPException) as exc:
            get_owned_order_or_404("ORD123", "user_123")
        self.assertEqual(exc.exception.status_code, 404)
        self.assertEqual(exc.exception.detail, "Замовлення не знайдено")

    def test_get_owned_order_or_404_no_owner_uid_returns_404(self):
        """test_get_owned_order_or_404_no_owner_uid_returns_404: helper raises 404 if owner_uid is missing"""
        from main import get_owned_order_or_404
        mock_db = MagicMock()
        main.calc.db = mock_db

        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"something": "else"}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with self.assertRaises(HTTPException) as exc:
            get_owned_order_or_404("ORD123", "user_123")
        self.assertEqual(exc.exception.status_code, 404)
        self.assertEqual(exc.exception.detail, "Замовлення не знайдено")

    def test_get_owned_order_or_404_db_error_safe(self):
        """test_get_owned_order_or_404_db_error_safe: helper maps database errors to 500"""
        from main import get_owned_order_or_404
        mock_db = MagicMock()
        main.calc.db = mock_db
        mock_db.collection.side_effect = Exception("Firestore socket timeout error")

        with self.assertRaises(HTTPException) as exc:
            get_owned_order_or_404("ORD123", "user_123")
        self.assertEqual(exc.exception.status_code, 500)
        self.assertEqual(exc.exception.detail, "Internal Server Error")

    def test_get_owned_order_or_404_non_string_owner_uid_returns_404(self):
        """test_get_owned_order_or_404_non_string_owner_uid_returns_404: helper raises 404 if owner_uid is not a string"""
        from main import get_owned_order_or_404
        mock_db = MagicMock()
        main.calc.db = mock_db

        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"owner_uid": 12345} # non-string
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with self.assertRaises(HTTPException) as exc:
            get_owned_order_or_404("ORD123", "user_123")
        self.assertEqual(exc.exception.status_code, 404)
        self.assertEqual(exc.exception.detail, "Замовлення не знайдено")

    def test_get_owned_order_or_404_padded_owner_uid_returns_404(self):
        """test_get_owned_order_or_404_padded_owner_uid_returns_404: helper raises 404 if owner_uid has leading/trailing spaces"""
        from main import get_owned_order_or_404
        mock_db = MagicMock()
        main.calc.db = mock_db

        # Test case 1: leading space
        mock_doc1 = MagicMock()
        mock_doc1.exists = True
        mock_doc1.to_dict.return_value = {"owner_uid": " user_123"}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc1

        with self.assertRaises(HTTPException) as exc:
            get_owned_order_or_404("ORD123", "user_123")
        self.assertEqual(exc.exception.status_code, 404)
        self.assertEqual(exc.exception.detail, "Замовлення не знайдено")

        # Test case 2: trailing space
        mock_doc2 = MagicMock()
        mock_doc2.exists = True
        mock_doc2.to_dict.return_value = {"owner_uid": "user_123 "}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc2

        with self.assertRaises(HTTPException) as exc:
            get_owned_order_or_404("ORD123", "user_123")
        self.assertEqual(exc.exception.status_code, 404)
        self.assertEqual(exc.exception.detail, "Замовлення не знайдено")

if __name__ == "__main__":
    unittest.main()
