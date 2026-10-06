"""Shared FastAPI dependencies."""
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import ACCESS_TOKEN_TYPE, decode_token
from app.db.session import get_db
from app.models.user import User

bearer_scheme = HTTPBearer(auto_error=False)

_credentials_error = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """Resolve the authenticated user from the Bearer access token."""
    if credentials is None:
        raise _credentials_error

    try:
        payload = decode_token(credentials.credentials)
    except jwt.InvalidTokenError:
        raise _credentials_error from None

    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise _credentials_error

    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        raise _credentials_error from None

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise _credentials_error
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
