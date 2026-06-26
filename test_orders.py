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

        cart_data = {"items": [{"item_id": 1}]}
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
        self.assertEqual(saved_record["cart"], cart_data)
        self.assertIn("timestamp", saved_record)

    def test_create_order_ignores_client_supplied_uid_and_email(self):
        """16. create-order: Ignores client-supplied UID/email in body/query, uses token's values"""
        user_payload = {"uid": "real_uid", "email": "real_email@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db

        cart_data = {
            "items": [],
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
        # Ensure the cart dict structure itself was not modified
        self.assertEqual(saved_record["cart"], cart_data)

    def test_create_order_firestore_error_500(self):
        """17. create-order: Firestore error -> 500 without leaking details"""
        user_payload = {"uid": "user_123", "email": "user@example.com"}
        app.dependency_overrides[verify_firebase_token] = lambda: user_payload

        mock_db = MagicMock()
        main.calc.db = mock_db
        # Simulate Firestore failure
        mock_db.collection.side_effect = Exception("Secret Firestore network crash details")

        response = client.post("/api/create-order", json={"items": []}, headers={"Authorization": "Bearer valid_token"})
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

    def test_generate_quote_other_user_403(self):
        """19. generate-quote: Non-owner gets 403 Forbidden (no 500)"""
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
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Forbidden")

    def test_generate_quote_missing_order_404(self):
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

    def test_generate_quote_legacy_order_no_owner_uid_403(self):
        """25. generate-quote: Legacy order without owner_uid -> 403 Forbidden"""
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
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Forbidden")

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

if __name__ == "__main__":
    unittest.main()
