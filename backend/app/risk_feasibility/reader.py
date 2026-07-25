from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import RiskSettings


class RiskSettingsReader:
    """Capability-limited reader; this class has no write method."""

    SETTINGS_ID = "default"

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_active(self) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            settings = await session.get(RiskSettings, self.SETTINGS_ID)
            return settings.to_dict() if settings is not None else None
