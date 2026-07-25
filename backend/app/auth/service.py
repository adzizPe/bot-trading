from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hmac import compare_digest
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.crypto import (
    DUMMY_PASSWORD_HASH, hash_password, hash_token, new_opaque_token,
    verify_password,
)
from app.auth.permissions import ROLE_PERMISSIONS, RoleName
from app.auth.principal import Principal
from app.database.models.auth import (
    AuthenticationAuditEvent, AuthSession, LoginAttempt, Role, User,
)


class AuthenticationError(Exception):
    pass


class AccountLockedError(AuthenticationError):
    pass


class RateLimitError(AuthenticationError):
    pass


class CsrfValidationError(AuthenticationError):
    pass


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    csrf_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime
    principal: Principal


class AuthService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], settings: Any) -> None:
        self.sessions = session_factory
        self.settings = settings

    async def ensure_roles(self) -> None:
        now = datetime.now(timezone.utc)
        async with self.sessions() as session:
            for role, permissions in ROLE_PERMISSIONS.items():
                model = await session.get(Role, role.value)
                values = sorted(value.value for value in permissions)
                if model is None:
                    session.add(Role(name=role.value, permissions=values,
                                     description=f"Built-in {role.value} role", created_at=now))
                else:
                    model.permissions = values
            await session.commit()

    async def create_user(self, username: str, password: str, role: RoleName) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        model = User(user_id=str(uuid4()), username=username.strip().lower(),
                     password_hash=hash_password(password), role_name=role.value,
                     is_active=True, failed_login_count=0, locked_until=None,
                     created_at=now, updated_at=now)
        async with self.sessions() as session:
            session.add(model)
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                raise ValueError("Username already exists") from error
        return self._user_dict(model)

    async def login(self, username: str, password: str, source_ip: str) -> TokenPair:
        normalized = username.strip().lower()
        now = datetime.now(timezone.utc)
        window = now - timedelta(seconds=self.settings.auth_login_rate_window_seconds)
        async with self.sessions() as session:
            ip_failures = await session.scalar(select(func.count()).select_from(LoginAttempt).where(
                LoginAttempt.source_ip == source_ip, LoginAttempt.successful.is_(False),
                LoginAttempt.occurred_at >= window,
            ))
            if int(ip_failures or 0) >= self.settings.auth_login_rate_limit:
                raise RateLimitError("Too many login attempts")
            user = await session.scalar(select(User).where(User.username == normalized))
            password_hash = user.password_hash if user else DUMMY_PASSWORD_HASH
            valid = verify_password(password, password_hash)
            if user and user.locked_until and self._aware(user.locked_until) > now:
                await self._attempt(session, normalized, source_ip, False, now)
                raise AccountLockedError("Account is temporarily locked")
            if not valid or not user or not user.is_active:
                await self._attempt(session, normalized, source_ip, False, now)
                if user:
                    user.failed_login_count += 1
                    if user.failed_login_count >= self.settings.auth_account_lockout_attempts:
                        user.locked_until = now + timedelta(seconds=self.settings.auth_account_lockout_seconds)
                        user.failed_login_count = 0
                await session.commit()
                raise AuthenticationError("Invalid credentials")
            user.failed_login_count = 0
            user.locked_until = None
            await self._attempt(session, normalized, source_ip, True, now)
            pair = self._new_pair(user, now)
            session.add(self._session_model(user.user_id, pair, now))
            await session.commit()
            return pair

    async def authenticate_access(self, token: str) -> Principal:
        now = datetime.now(timezone.utc)
        computed_hash = hash_token(token)
        async with self.sessions() as session:
            row = (await session.execute(
                select(AuthSession, User).join(User, User.user_id == AuthSession.user_id).where(
                    AuthSession.access_token_hash == computed_hash
                )
            )).first()
            if not row:
                raise AuthenticationError("Unknown access token")
            auth_session, user = row
            if not compare_digest(computed_hash, auth_session.access_token_hash):
                raise AuthenticationError("Unknown access token")
            if (auth_session.revoked_at is not None or not user.is_active
                    or self._aware(auth_session.access_expires_at) <= now):
                raise AuthenticationError("Expired or revoked access token")
            return self._principal(
                user, auth_session.session_id,
                self._aware(auth_session.access_expires_at),
            )

    async def validate_csrf(self, session_id: str, csrf_token: str) -> None:
        computed_hash = hash_token(csrf_token)
        async with self.sessions() as session:
            auth_session = await session.get(AuthSession, session_id)
            if (auth_session is None or auth_session.revoked_at is not None
                    or not compare_digest(computed_hash, auth_session.csrf_token_hash)):
                raise CsrfValidationError("Invalid CSRF token")

    async def refresh(self, refresh_token: str, csrf_token: str) -> TokenPair:
        now = datetime.now(timezone.utc)
        computed_refresh_hash = hash_token(refresh_token)
        computed_csrf_hash = hash_token(csrf_token)
        async with self.sessions() as session:
            row = (await session.execute(
                select(AuthSession, User).join(User, User.user_id == AuthSession.user_id).where(
                    AuthSession.refresh_token_hash == computed_refresh_hash
                )
            )).first()
            if not row:
                raise AuthenticationError("Unknown refresh token")
            auth_session, user = row
            if not compare_digest(computed_refresh_hash, auth_session.refresh_token_hash):
                raise AuthenticationError("Invalid refresh session")
            if not compare_digest(computed_csrf_hash, auth_session.csrf_token_hash):
                raise CsrfValidationError("Invalid refresh CSRF token")
            if (auth_session.revoked_at is not None or not user.is_active
                    or self._aware(auth_session.refresh_expires_at) <= now):
                raise AuthenticationError("Expired or revoked refresh token")
            pair = self._new_pair(user, now, auth_session.session_id)
            auth_session.access_token_hash = hash_token(pair.access_token)
            auth_session.refresh_token_hash = hash_token(pair.refresh_token)
            auth_session.csrf_token_hash = hash_token(pair.csrf_token)
            auth_session.access_expires_at = pair.access_expires_at
            auth_session.refresh_expires_at = pair.refresh_expires_at
            auth_session.rotated_at = now
            await session.commit()
            return pair

    async def revoke_session(self, session_id: str, reason: str) -> bool:
        now = datetime.now(timezone.utc)
        async with self.sessions() as session:
            result = await session.execute(update(AuthSession).where(
                AuthSession.session_id == session_id, AuthSession.revoked_at.is_(None)
            ).values(revoked_at=now, revoke_reason=reason))
            await session.commit()
            return bool(result.rowcount)

    async def revoke_user_sessions(self, user_id: str, reason: str) -> int:
        now = datetime.now(timezone.utc)
        async with self.sessions() as session:
            result = await session.execute(update(AuthSession).where(
                AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)
            ).values(revoked_at=now, revoke_reason=reason))
            await session.commit()
            return int(result.rowcount or 0)

    async def list_users(self) -> list[dict[str, Any]]:
        async with self.sessions() as session:
            users = (await session.scalars(select(User).order_by(User.username))).all()
            return [self._user_dict(user) for user in users]

    async def set_role(self, user_id: str, role: RoleName) -> dict[str, Any]:
        async with self.sessions() as session:
            user = await session.get(User, user_id)
            if user is None:
                raise LookupError("User not found")
            user.role_name = role.value
            user.updated_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(user)
        await self.revoke_user_sessions(user_id, "role changed")
        return self._user_dict(user)

    async def list_sessions(self, user_id: str) -> list[dict[str, Any]]:
        async with self.sessions() as session:
            items = (await session.scalars(select(AuthSession).where(
                AuthSession.user_id == user_id
            ).order_by(AuthSession.created_at.desc()))).all()
            return [{key: getattr(item, key) for key in (
                "session_id", "created_at", "access_expires_at",
                "refresh_expires_at", "revoked_at",
            )} for item in items]

    async def audit(self, **values: Any) -> None:
        result = str(values.get("result", "")).upper()
        if result not in {"SUCCESS", "FAILURE"}:
            result = "FAILURE"
        failure_reason = values.get("failure_reason") if result == "FAILURE" else None
        allowed = {
            "event_id": str(uuid4()),
            "request_id": self._bounded(values.get("request_id"), 36) or str(uuid4()),
            "authenticated_user_id": self._bounded(
                values.get("authenticated_user_id"), 36
            ),
            "username": self._bounded(values.get("username"), 64),
            "role": self._bounded(values.get("role"), 32),
            "permission": self._bounded(values.get("permission"), 64),
            "endpoint": self._bounded(values.get("endpoint"), 255) or "unknown",
            "action": self._bounded(values.get("action"), 16) or "UNKNOWN",
            "result": result,
            "failure_reason": self._failure_reason(failure_reason),
            "source_ip": self._bounded(values.get("source_ip"), 64) or "unknown",
            "occurred_at": datetime.now(timezone.utc),
        }
        async with self.sessions() as session:
            session.add(AuthenticationAuditEvent(**allowed))
            await session.commit()

    async def _attempt(self, session: AsyncSession, username: str, source_ip: str,
                       successful: bool, occurred_at: datetime) -> None:
        session.add(LoginAttempt(attempt_id=str(uuid4()), username=username,
                                 source_ip=source_ip, successful=successful,
                                 occurred_at=occurred_at))

    def _new_pair(self, user: User, now: datetime,
                  session_id: str | None = None) -> TokenPair:
        access_expires_at = now + timedelta(
            seconds=self.settings.auth_access_ttl_seconds
        )
        return TokenPair(
            access_token=new_opaque_token(),
            refresh_token=new_opaque_token(),
            csrf_token=new_opaque_token(),
            access_expires_at=access_expires_at,
            refresh_expires_at=now + timedelta(
                seconds=self.settings.auth_refresh_ttl_seconds
            ),
            principal=self._principal(
                user, session_id or str(uuid4()), access_expires_at
            ),
        )

    @staticmethod
    def _session_model(user_id: str, pair: TokenPair, now: datetime) -> AuthSession:
        return AuthSession(
            session_id=pair.principal.session_id, user_id=user_id,
            access_token_hash=hash_token(pair.access_token),
            refresh_token_hash=hash_token(pair.refresh_token),
            csrf_token_hash=hash_token(pair.csrf_token),
            access_expires_at=pair.access_expires_at,
            refresh_expires_at=pair.refresh_expires_at,
            created_at=now, rotated_at=None, revoked_at=None, revoke_reason=None,
        )

    @staticmethod
    def _principal(user: User, session_id: str,
                   access_expires_at: datetime) -> Principal:
        role = RoleName(user.role_name)
        return Principal(
            user_id=user.user_id, username=user.username, role=role,
            permissions=ROLE_PERMISSIONS[role], session_id=session_id,
            access_expires_at=access_expires_at,
        )

    @staticmethod
    def _user_dict(user: User) -> dict[str, Any]:
        role = RoleName(user.role_name)
        return {"user_id": user.user_id, "username": user.username,
                "role": role, "permissions": sorted(p.value for p in ROLE_PERMISSIONS[role]),
                "is_active": user.is_active}

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    @staticmethod
    def _bounded(value: Any, limit: int) -> str | None:
        if value is None:
            return None
        sanitized = " ".join(str(value).split())
        return sanitized[:limit] or None

    @classmethod
    def _failure_reason(cls, value: Any) -> str | None:
        sanitized = cls._bounded(value, 255)
        if sanitized is None:
            return None
        if any(term in sanitized.lower() for term in (
            "password", "token", "secret", "authorization", "cookie",
        )):
            return "Request rejected"
        return sanitized

