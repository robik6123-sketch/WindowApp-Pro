import unittest
from unittest.mock import patch
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from auth_dependency import verify_firebase_token

# Mount test endpoint on a clean app
app = FastAPI()

@app.get("/test-auth")
async def test_endpoint(user: dict = Depends(verify_firebase_token)):
    return {"user": user}

client = TestClient(app)

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

    def test_empty_bearer_only(self):
        """3. Authorization header is exactly 'Bearer' -> 401"""
        response = client.get("/test-auth", headers={"Authorization": "Bearer"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Unauthorized"})
        self.assertEqual(response.headers.get("WWW-Authenticate"), "Bearer")

    def test_empty_bearer_whitespace(self):
        """4. Authorization header is 'Bearer   ' -> 401"""
        response = client.get("/test-auth", headers={"Authorization": "Bearer   "})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Unauthorized"})
        self.assertEqual(response.headers.get("WWW-Authenticate"), "Bearer")

    @patch("auth_dependency.auth.verify_id_token")
    def test_lowercase_bearer(self, mock_verify):
        """5. lowercase 'bearer token' -> processed correctly"""
        mock_decoded = {"uid": "user_123", "email": "test@example.com"}
        mock_verify.return_value = mock_decoded

        response = client.get("/test-auth", headers={"Authorization": "bearer token_val"})
        self.assertEqual(response.status_code, 200)
        mock_verify.assert_called_once_with("token_val")

    @patch("auth_dependency.auth.verify_id_token")
    def test_invalid_token(self, mock_verify):
        """6. Invalid token -> 401"""
        mock_verify.side_effect = Exception("Firebase ID token has invalid signature")

        response = client.get("/test-auth", headers={"Authorization": "Bearer invalid_token"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Unauthorized"})
        self.assertEqual(response.headers.get("WWW-Authenticate"), "Bearer")

    @patch("auth_dependency.auth.verify_id_token")
    def test_expired_token(self, mock_verify):
        """7. Expired token -> 401"""
        mock_verify.side_effect = Exception("Firebase ID token has expired")

        response = client.get("/test-auth", headers={"Authorization": "Bearer expired_token"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Unauthorized"})
        self.assertEqual(response.headers.get("WWW-Authenticate"), "Bearer")

    @patch("auth_dependency.auth.verify_id_token")
    def test_decoded_token_none(self, mock_verify):
        """8. verify_id_token() returns None -> 401"""
        mock_verify.return_value = None

        response = client.get("/test-auth", headers={"Authorization": "Bearer token123"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Unauthorized"})
        self.assertEqual(response.headers.get("WWW-Authenticate"), "Bearer")

    @patch("auth_dependency.auth.verify_id_token")
    def test_decoded_token_not_dict(self, mock_verify):
        """9. verify_id_token() returns a non-dict value -> 401"""
        mock_verify.return_value = "string_token_value"

        response = client.get("/test-auth", headers={"Authorization": "Bearer token123"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Unauthorized"})
        self.assertEqual(response.headers.get("WWW-Authenticate"), "Bearer")

    @patch("auth_dependency.auth.verify_id_token")
    def test_uid_missing(self, mock_verify):
        """10. UID is missing -> 401"""
        mock_decoded = {"email": "test@example.com"}
        mock_verify.return_value = mock_decoded

        response = client.get("/test-auth", headers={"Authorization": "Bearer token123"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Unauthorized"})
        self.assertEqual(response.headers.get("WWW-Authenticate"), "Bearer")

    @patch("auth_dependency.auth.verify_id_token")
    def test_uid_empty(self, mock_verify):
        """11. UID is empty string -> 401"""
        mock_decoded = {"uid": "", "email": "test@example.com"}
        mock_verify.return_value = mock_decoded

        response = client.get("/test-auth", headers={"Authorization": "Bearer token123"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Unauthorized"})
        self.assertEqual(response.headers.get("WWW-Authenticate"), "Bearer")

    @patch("auth_dependency.auth.verify_id_token")
    def test_email_missing_is_allowed(self, mock_verify):
        """12. email missing is allowed -> 200 with email as None"""
        mock_decoded = {"uid": "user_123"}
        mock_verify.return_value = mock_decoded

        response = client.get("/test-auth", headers={"Authorization": "Bearer token123"})
        self.assertEqual(response.status_code, 200)
        user_data = response.json().get("user")
        self.assertEqual(user_data["uid"], "user_123")
        self.assertIsNone(user_data.get("email"))

    @patch("auth_dependency.auth.verify_id_token")
    def test_valid_token_uid_extracted(self, mock_verify):
        """13. valid token returns correct UID and custom claims"""
        mock_decoded = {
            "uid": "user_123",
            "email": "test@example.com",
            "role": "admin"
        }
        mock_verify.return_value = mock_decoded

        response = client.get("/test-auth", headers={"Authorization": "Bearer token123"})
        self.assertEqual(response.status_code, 200)
        user_data = response.json().get("user")
        self.assertEqual(user_data["uid"], "user_123")
        self.assertEqual(user_data["email"], "test@example.com")
        self.assertEqual(user_data["claims"], {"role": "admin"})

    @patch("auth_dependency.auth.verify_id_token")
    def test_reserved_claims_excluded(self, mock_verify):
        """14. Reserved claims, including at_hash, c_hash, cnf, are excluded from custom claims"""
        mock_decoded = {
            "uid": "user_123",
            "email": "test@example.com",
            # Reserved claims to be excluded
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
            "at_hash": "at-hash-val",
            "c_hash": "c-hash-val",
            "cnf": {"key": "val"},
            # Custom claims
            "role": "editor",
            "tenant": "tenant-1"
        }
        mock_verify.return_value = mock_decoded

        response = client.get("/test-auth", headers={"Authorization": "Bearer token123"})
        self.assertEqual(response.status_code, 200)
        user_data = response.json().get("user")
        self.assertEqual(user_data["claims"], {"role": "editor", "tenant": "tenant-1"})

    @patch("auth_dependency.auth.verify_id_token")
    def test_verify_id_token_called_exactly_once(self, mock_verify):
        """15. verify_id_token is called exactly once with the correct token"""
        mock_decoded = {"uid": "user_123"}
        mock_verify.return_value = mock_decoded

        response = client.get("/test-auth", headers={"Authorization": "Bearer token123"})
        self.assertEqual(response.status_code, 200)
        mock_verify.assert_called_once_with("token123")

    @patch("auth_dependency.auth.verify_id_token")
    def test_log_does_not_contain_secrets(self, mock_verify):
        """16. logs do not contain token, email, traceback or full exception message"""
        mock_verify.side_effect = RuntimeError("Fatal connection error to Firebase 500")

        with self.assertLogs("WindowApp", level="WARNING") as captured_logs:
            response = client.get("/test-auth", headers={"Authorization": "Bearer token123"})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(len(captured_logs.output), 1)
        log_message = captured_logs.output[0]

        # Verify it logs the generalized type
        self.assertIn("Authentication failed: RuntimeError", log_message)
        # Verify it DOES NOT leak secrets or details
        self.assertNotIn("token123", log_message)
        self.assertNotIn("test@example.com", log_message)
        self.assertNotIn("Fatal connection error", log_message)

if __name__ == "__main__":
    unittest.main()
