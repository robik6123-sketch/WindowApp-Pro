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

if __name__ == "__main__":
    unittest.main()
