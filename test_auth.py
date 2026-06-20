import os
import unittest
from unittest.mock import patch, MagicMock

# 1. Start module-level patchers to isolate Firebase/Firestore during import of main.py
env_patcher = patch.dict(os.environ, {"USE_FIRESTORE": "false", "GOOGLE_APPLICATION_CREDENTIALS_JSON": ""})
cert_patcher = patch("firebase_admin.credentials.Certificate")
init_patcher = patch("firebase_admin.initialize_app")
client_patcher = patch("firebase_admin.firestore.client")

# Start all patchers
env_patcher.start()
cert_patcher.start()
init_patcher.start()
client_patcher.start()

# 2. Safely import FastAPI components and the dependency to test
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from main import verify_firebase_token

# Mount test endpoint on a clean app
app = FastAPI()

@app.get("/test-auth")
async def test_endpoint(user: dict = Depends(verify_firebase_token)):
    return {"user": user}

client = TestClient(app)

# 3. Define module teardown to restore any mutated global/module states
def tearDownModule():
    cert_patcher.stop()
    init_patcher.stop()
    client_patcher.stop()
    env_patcher.stop()

class TestVerifyFirebaseToken(unittest.TestCase):

    def test_missing_header(self):
        """1. Missing Authorization header should raise 401 with WWW-Authenticate header"""
        response = client.get("/test-auth")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Unauthorized"})
        self.assertEqual(response.headers.get("WWW-Authenticate"), "Bearer")

    def test_invalid_scheme(self):
        """2. Incorrect scheme (not Bearer) should raise 401 with WWW-Authenticate header"""
        response = client.get("/test-auth", headers={"Authorization": "Basic some_token"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Unauthorized"})
        self.assertEqual(response.headers.get("WWW-Authenticate"), "Bearer")

    def test_empty_bearer(self):
        """3. Authorization: Bearer with whitespace only should raise 401 with WWW-Authenticate header"""
        response = client.get("/test-auth", headers={"Authorization": "Bearer   "})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Unauthorized"})
        self.assertEqual(response.headers.get("WWW-Authenticate"), "Bearer")

    @patch("main.auth.verify_id_token")
    def test_scheme_mixed_case(self, mock_verify):
        """4. Scheme 'bearer' in lowercase should be handled correctly"""
        mock_decoded = {"uid": "user_123", "email": "test@example.com"}
        mock_verify.return_value = mock_decoded

        response = client.get("/test-auth", headers={"Authorization": "bearer token_value"})
        self.assertEqual(response.status_code, 200)
        mock_verify.assert_called_once_with("token_value")

    @patch("main.auth.verify_id_token")
    def test_verify_id_token_returns_none(self, mock_verify):
        """5. verify_id_token() returning None should raise 401 with WWW-Authenticate header"""
        mock_verify.return_value = None

        response = client.get("/test-auth", headers={"Authorization": "Bearer token123"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Unauthorized"})
        self.assertEqual(response.headers.get("WWW-Authenticate"), "Bearer")

    @patch("main.auth.verify_id_token")
    def test_verify_id_token_returns_non_dict(self, mock_verify):
        """6. verify_id_token() returning non-dict value should raise 401 with WWW-Authenticate header"""
        mock_verify.return_value = "invalid_decoded_token_type"

        response = client.get("/test-auth", headers={"Authorization": "Bearer token123"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Unauthorized"})
        self.assertEqual(response.headers.get("WWW-Authenticate"), "Bearer")

    @patch("main.auth.verify_id_token")
    def test_verify_id_token_returns_empty_uid(self, mock_verify):
        """7. UID set to empty string should raise 401 with WWW-Authenticate header"""
        mock_decoded = {"uid": "", "email": "test@example.com"}
        mock_verify.return_value = mock_decoded

        response = client.get("/test-auth", headers={"Authorization": "Bearer token123"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Unauthorized"})
        self.assertEqual(response.headers.get("WWW-Authenticate"), "Bearer")

    @patch("main.auth.verify_id_token")
    def test_verify_id_token_returns_missing_uid(self, mock_verify):
        """8. Decoded token missing UID should raise 401 with WWW-Authenticate header"""
        mock_decoded = {"email": "test@example.com"}
        mock_verify.return_value = mock_decoded

        response = client.get("/test-auth", headers={"Authorization": "Bearer token123"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Unauthorized"})
        self.assertEqual(response.headers.get("WWW-Authenticate"), "Bearer")

    @patch("main.auth.verify_id_token")
    def test_standard_claims_filtering(self, mock_verify):
        """9. Standard claims are filtered out, only custom claims remain"""
        mock_decoded = {
            "uid": "user_123",
            "email": "test@example.com",
            # Standard claims to be filtered out
            "iss": "issuer",
            "aud": "audience",
            "auth_time": 100,
            "sub": "subject",
            "iat": 100,
            "exp": 200,
            "firebase": {},
            "email_verified": True,
            "user_id": "user_123",
            "name": "John Doe",
            "picture": "http://pic",
            "phone_number": "+12345",
            "nbf": 100,
            "jti": "jti-id",
            "nonce": "nonce-val",
            "azp": "azp-val",
            "amr": ["pwd"],
            "acr": "acr-val",
            # Custom claims
            "role": "admin",
            "tenant": "tenant-1"
        }
        mock_verify.return_value = mock_decoded

        response = client.get("/test-auth", headers={"Authorization": "Bearer token123"})
        self.assertEqual(response.status_code, 200)

        user_data = response.json().get("user")
        self.assertEqual(user_data["uid"], "user_123")
        self.assertEqual(user_data["email"], "test@example.com")
        self.assertEqual(user_data["claims"], {"role": "admin", "tenant": "tenant-1"})

    @patch("main.auth.verify_id_token")
    def test_valid_token_missing_email(self, mock_verify):
        """10. Valid token without email should return 200 with email as None"""
        mock_decoded = {
            "uid": "user_54321",
            "user_id": "user_54321",
            "role": "admin"
        }
        mock_verify.return_value = mock_decoded

        response = client.get("/test-auth", headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 200)

        user_data = response.json().get("user")
        self.assertIsNotNone(user_data)
        self.assertEqual(user_data["uid"], "user_54321")
        self.assertIsNone(user_data.get("email"))
        self.assertEqual(user_data["claims"], {"role": "admin"})

    @patch("main.auth.verify_id_token")
    def test_firebase_unexpected_error(self, mock_verify):
        """11. Unexpected Firebase error does not leak internal details, raises 401, and is logged at warning level without sensitive details"""
        mock_verify.side_effect = RuntimeError("Fatal connection error to Firebase 500")

        with self.assertLogs("WindowApp", level="WARNING") as captured_logs:
            response = client.get("/test-auth", headers={"Authorization": "Bearer token123"})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Unauthorized"})
        self.assertEqual(response.headers.get("WWW-Authenticate"), "Bearer")

        # Verify log output contents
        self.assertEqual(len(captured_logs.output), 1)
        log_message = captured_logs.output[0]
        self.assertIn("Authentication failed: RuntimeError", log_message)
        self.assertNotIn("token123", log_message)
        self.assertNotIn("Fatal connection error", log_message)

    @patch("main.auth.verify_id_token")
    def test_verify_id_token_called_exactly_once(self, mock_verify):
        """12. verify_id_token is called exactly once with the passed token"""
        mock_decoded = {
            "uid": "user_123",
            "email": "test@example.com"
        }
        mock_verify.return_value = mock_decoded

        response = client.get("/test-auth", headers={"Authorization": "Bearer my_super_token"})
        self.assertEqual(response.status_code, 200)
        mock_verify.assert_called_once_with("my_super_token")

if __name__ == "__main__":
    unittest.main()
