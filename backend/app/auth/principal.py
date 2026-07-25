from dataclasses import dataclass
from datetime import datetime

from app.auth.permissions import Permission, RoleName


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    username: str
    role: RoleName
    permissions: frozenset[Permission]
    session_id: str
    access_expires_at: datetime

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions
