from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.auth.permissions import RoleName


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class UserResponse(BaseModel):
    user_id: str
    username: str
    role: RoleName
    permissions: list[str]
    is_active: bool = True


class AuthResponse(UserResponse):
    access_expires_at: datetime


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=1024)
    role: RoleName


class UpdateUserRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: RoleName


class SessionResponse(BaseModel):
    session_id: str
    created_at: datetime
    access_expires_at: datetime
    refresh_expires_at: datetime
    revoked_at: datetime | None


class MessageResponse(BaseModel):
    detail: str
