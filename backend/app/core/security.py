"""Password hashing and JWT creation / validation helpers."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import bcrypt
import jwt

from app.core.config import settings

# bcrypt only considers the first 72 bytes of a password.
BCRYPT_MAX_BYTES = 72

ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(
        password.encode("utf-8")[:BCRYPT_MAX_BYTES], bcrypt.gensalt()
    ).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(
        plain_password.encode("utf-8")[:BCRYPT_MAX_BYTES],
        hashed_password.encode("utf-8"),
    )


def _encode_token(
    *, user_id: int, token_type: str, expires_delta: timedelta
) -> tuple[str, str, datetime]:
    """Returns (encoded_jwt, jti, expires_at)."""
    now = datetime.now(timezone.utc)
    expires_at = now + expires_delta
    jti = uuid4().hex
    payload = {
        "sub": str(user_id),
        "type": token_type,
        "iat": now,
        "exp": expires_at,
        "jti": jti,
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, jti, expires_at


def create_access_token(user_id: int) -> str:
    token, _, _ = _encode_token(
        user_id=user_id,
        token_type=ACCESS_TOKEN_TYPE,
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return token


def create_refresh_token(user_id: int) -> tuple[str, str, datetime]:
    """Returns (encoded_jwt, jti, expires_at) for a new refresh token."""
    return _encode_token(
        user_id=user_id,
        token_type=REFRESH_TOKEN_TYPE,
        expires_delta=timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )


def decode_token(token: str) -> dict:
    """Decode and validate a JWT. Raises jwt.InvalidTokenError on failure."""
    return jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        options={"require": ["exp", "sub", "type", "jti"]},
    )
