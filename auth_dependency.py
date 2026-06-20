import logging
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from firebase_admin import auth

# Initialize logger
logger = logging.getLogger("WindowApp")

# Centralized HTTPBearer dependency
security_bearer = HTTPBearer(auto_error=False)

# Centralized list of reserved/standard Firebase and JWT claims
STANDARD_CLAIMS = {
    "iss", "aud", "auth_time", "sub", "iat", "exp", "firebase",
    "uid", "user_id", "email", "email_verified", "name", "picture",
    "phone_number", "nbf", "jti", "nonce", "azp", "amr", "acr",
    "at_hash", "c_hash", "cnf"
}

def raise_unauthorized():
    """Raises a centralized 401 Unauthorized exception with WWW-Authenticate header."""
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized",
        headers={"WWW-Authenticate": "Bearer"}
    )

async def verify_firebase_token(credentials: HTTPAuthorizationCredentials = Depends(security_bearer)):
    """
    Verifies the Firebase ID Token passed in the Authorization header.
    Returns a dict containing normalized user data: {'uid': ..., 'email': ..., 'claims': ...}
    Raises HTTP 401 on any failure, without exposing internal Firebase exception details
    and without logging/printing the token.
    """
    if credentials is None:
        raise_unauthorized()

    if credentials.scheme.lower() != "bearer":
        raise_unauthorized()

    token = credentials.credentials
    if not token or not token.strip():
        raise_unauthorized()

    try:
        # Call Firebase Admin to verify token
        decoded_token = auth.verify_id_token(token)

        if decoded_token is None or not isinstance(decoded_token, dict):
            raise_unauthorized()

        # Verify UID is present in the decoded token
        uid = decoded_token.get("uid")
        if not uid or uid == "":
            raise_unauthorized()

        email = decoded_token.get("email") # may be missing/None

        # Extract custom claims by excluding standard claims
        custom_claims = {k: v for k, v in decoded_token.items() if k not in STANDARD_CLAIMS}

        return {
            "uid": uid,
            "email": email,
            "claims": custom_claims
        }
    except HTTPException:
        # Re-raise our own HTTPException
        raise
    except Exception as e:
        # Generic error handling to avoid leaking Firebase internals
        # We also do NOT log the token itself to prevent security leaks
        logger.warning(f"Authentication failed: {type(e).__name__}")
        raise_unauthorized()
