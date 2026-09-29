from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from config import SECRET_KEY

# Serializer for secure, tamper-proof session cookies
_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="crankspan-session-cookie")
_csrf_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="crankspan-csrf-token")

# Max age: 1 year for sessions
MAX_SESSION_AGE = 365 * 24 * 3600
# Max age: 24 hours for CSRF tokens
MAX_CSRF_AGE = 24 * 3600

def create_session_token(user_id: int) -> str:
    """Create a cryptographically signed token containing user_id."""
    return _serializer.dumps({"user_id": user_id})

def verify_session_token(token: str | None) -> int | None:
    """Verify session token signature and return user_id, or None if invalid/expired."""
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=MAX_SESSION_AGE)
        return data.get("user_id")
    except (BadSignature, SignatureExpired):
        return None

def create_csrf_token(user_id: int) -> str:
    """Generate a CSRF token bound to the user."""
    return _csrf_serializer.dumps({"user_id": user_id})

def verify_csrf_token(user_id: int, token: str | None) -> bool:
    """Verify that CSRF token is valid, unexpired, and matches user_id."""
    if not token:
        return False
    try:
        data = _csrf_serializer.loads(token, max_age=MAX_CSRF_AGE)
        return data.get("user_id") == user_id
    except (BadSignature, SignatureExpired):
        return False
