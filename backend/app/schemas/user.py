"""Pydantic schemas for user accounts."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.security import BCRYPT_MAX_BYTES


def _normalize_email(value: str) -> str:
    return value.lower()


class UserBase(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    full_name: str | None = Field(default=None, max_length=255)


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return _normalize_email(value)

    @field_validator("password")
    @classmethod
    def password_within_bcrypt_limit(cls, value: str) -> str:
        if len(value.encode("utf-8")) > BCRYPT_MAX_BYTES:
            raise ValueError(f"Password must not exceed {BCRYPT_MAX_BYTES} bytes")
        return value


class UserRead(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    is_superuser: bool
    created_at: datetime
