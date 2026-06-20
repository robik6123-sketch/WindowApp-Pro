import os
import unittest
from unittest.mock import patch, MagicMock

# 1. Disable Firestore flag and credentials to prevent actual initialization during main.py import
os.environ["USE_FIRESTORE"] = "false"
os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"] = ""

# 2. Mock firebase_admin and its submodules BEFORE importing main.py to prevent any real side-effects
import firebase_admin
from firebase_admin import credentials, firestore

credentials.Certificate = MagicMock()
firebase_admin.initialize_app = MagicMock()
firestore.client = MagicMock(return_value=MagicMock())

# 3. Now import FastAPI and main's dependency safely in isolation
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from main import verify_firebase_token

# Mount test endpoint on a clean app
app = FastAPI()

@app.get("/test-auth")
async def test_endpoint(user: dict = Depends(verify_firebase_token)):
    return {"user": user}

client = TestClient(app)

class TestVerifyFirebaseToken(unittest.TestCase):
    
    def test_missing_header(self):
        """1. Missing Authorization header should raise 401 Unauthorized"""
        response = client.get("/test-auth")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Unauthorized"})

    def test_invalid_scheme(self):
        """2. Incorrect scheme (not Bearer) should raise 401 Unauthorized"""
        # Test wrong scheme (Basic)
        response = client.get("/test-auth", headers={"Authorization": "Basic some_token"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Unauthorized"})
        
        # Test Bearer prefix with empty credentials
        response2 = client.get("/test-auth", headers={"Authorization": "Bearer"})
        self.assertEqual(response2.status_code, 401)
        self.assertEqual(response2.json(), {"detail": "Unauthorized"})

    @patch("main.auth.verify_id_token")
    def test_invalid_token(self, mock_verify):
        """3. Invalid token should raise 401 Unauthorized"""
        # Mock verify_id_token to raise an exception
        mock_verify.side_effect = Exception("Firebase ID token has invalid signature")
        
        response = client.get("/test-auth", headers={"Authorization": "Bearer invalid_token"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Unauthorized"})

    @patch("main.auth.verify_id_token")
    def test_expired_token(self, mock_verify):
        """4. Expired token should raise 401 Unauthorized"""
        mock_verify.side_effect = Exception("Firebase ID token has expired")
        
        response = client.get("/test-auth", headers={"Authorization": "Bearer expired_token"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Unauthorized"})

    @patch("main.auth.verify_id_token")
    def test_valid_token(self, mock_verify):
        """5. Valid token should return 200 with UID, optional email, and custom claims only"""
        # Mock decoded token dictionary returned by Firebase verify_id_token
        mock_decoded = {
            "uid": "user_12345",
            "email": "test@example.com",
            "email_verified": True,
            "iss": "https://securetoken.google.com/windowapp-pro-2026",
            "aud": "windowapp-pro-2026",
            "auth_time": 1234567890,
            "sub": "user_12345",
            "iat": 1234567890,
            "exp": 1234567890,
            "firebase": {},
            "user_id": "user_12345",
            # Custom claims
            "role": "editor",
            "premium": True
        }
        mock_verify.return_value = mock_decoded
        
        response = client.get("/test-auth", headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 200)
        
        user_data = response.json().get("user")
        self.assertIsNotNone(user_data)
        self.assertEqual(user_data["uid"], "user_12345")
        self.assertEqual(user_data["email"], "test@example.com")
        
        # Verify custom claims are extracted correctly without standard ones
        claims = user_data["claims"]
        self.assertEqual(claims, {"role": "editor", "premium": True})
        
        # Verify standard claims are not in the custom claims dictionary
        for standard_key in ["iss", "aud", "auth_time", "sub", "iat", "exp", "firebase", "user_id", "email_verified"]:
            self.assertNotIn(standard_key, claims)

    @patch("main.auth.verify_id_token")
    def test_valid_token_missing_email(self, mock_verify):
        """5b. Valid token without email should return 200 with email as None"""
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
    def test_valid_token_missing_uid(self, mock_verify):
        """5c. Decoded token missing UID should raise 401 Unauthorized"""
        mock_decoded = {
            "email": "test@example.com",
            "role": "editor"
        }
        mock_verify.return_value = mock_decoded
        
        response = client.get("/test-auth", headers={"Authorization": "Bearer valid_token"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Unauthorized"})

    @patch("main.auth.verify_id_token")
    def test_firebase_unexpected_error(self, mock_verify):
        """6. Unexpected Firebase error does not leak internal details and raises 401"""
        # Mock unexpected library exception
        mock_verify.side_effect = RuntimeError("Fatal connection error to Firebase 500")
        
        response = client.get("/test-auth", headers={"Authorization": "Bearer token123"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Unauthorized"})

if __name__ == "__main__":
    unittest.main()
