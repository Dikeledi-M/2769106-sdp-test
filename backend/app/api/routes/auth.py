"""Authentication endpoints: register, login, refresh, logout, me.

Access tokens are short-lived JWTs; refresh tokens are JWTs too and are
tracked server-side by their ``jti`` so they can be rotated and revoked.
Reusing a rotated refresh token is treated as a compromised session and
invalidates every refresh token of that user.
"""
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser
from app.core.security import (
    REFRESH_TOKEN_TYPE,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.db.session import get_db
from app.models.user import RefreshToken, User
from app.schemas.auth import LoginRequest, RefreshRequest, TokenPair
from app.schemas.user import UserCreate, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])

DbSession = Annotated[Session, Depends(get_db)]

_invalid_refresh_error = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or expired refresh token",
    headers={"WWW-Authenticate": "Bearer"},
)


def _issue_token_pair(db: Session, user: User) -> TokenPair:
    """Create an access/refresh pair and persist the refresh token's jti."""
    refresh_token, jti, expires_at = create_refresh_token(user.id)
    db.add(RefreshToken(jti=jti, user_id=user.id, expires_at=expires_at))
    db.commit()
    return TokenPair(
        access_token=create_access_token(user.id),
        refresh_token=refresh_token,
    )


def _get_token_row(db: Session, jti: str) -> RefreshToken | None:
    return db.execute(select(RefreshToken).where(RefreshToken.jti == jti)).scalars().first()


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: DbSession) -> User:
    existing = (
        db.execute(
            select(User).where(
                (User.email == payload.email) | (User.username == payload.username)
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        detail = "Email already registered" if existing.email == payload.email else "Username already taken"
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)

    user = User(
        email=payload.email,
        username=payload.username,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenPair)
def login(payload: LoginRequest, db: DbSession) -> TokenPair:
    user = db.execute(select(User).where(User.email == payload.email)).scalars().first()
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user")
    return _issue_token_pair(db, user)


@router.post("/refresh", response_model=TokenPair)
def refresh(payload: RefreshRequest, db: DbSession) -> TokenPair:
    try:
        claims = decode_token(payload.refresh_token)
    except jwt.InvalidTokenError:
        raise _invalid_refresh_error from None

    if claims.get("type") != REFRESH_TOKEN_TYPE:
        raise _invalid_refresh_error

    token_row = _get_token_row(db, claims["jti"])
    if token_row is None:
        raise _invalid_refresh_error

    if token_row.revoked:
        # A rotated token was replayed: revoke every refresh token of the user.
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == token_row.user_id)
            .values(revoked=True)
        )
        db.commit()
        raise _invalid_refresh_error

    user = db.get(User, token_row.user_id)
    if user is None or not user.is_active:
        raise _invalid_refresh_error

    token_row.revoked = True
    return _issue_token_pair(db, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(payload: RefreshRequest, db: DbSession) -> None:
    """Revoke the given refresh token. Idempotent: always returns 204."""
    try:
        claims = decode_token(payload.refresh_token)
    except jwt.InvalidTokenError:
        return

    if claims.get("type") != REFRESH_TOKEN_TYPE:
        return

    token_row = _get_token_row(db, claims["jti"])
    if token_row is not None and not token_row.revoked:
        token_row.revoked = True
        db.commit()


@router.get("/me", response_model=UserRead)
def read_current_user(current_user: CurrentUser) -> User:
    return current_user
