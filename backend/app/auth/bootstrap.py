import asyncio
from getpass import getpass

from app.auth.permissions import RoleName
from app.auth.service import AuthService
from app.config.settings import get_settings
from app.database.session import SessionFactory, close_database


async def _bootstrap() -> None:
    username = input("Super-admin username: ").strip()
    if not username:
        raise SystemExit("Username is required")
    password = getpass("Password (minimum 12 characters): ")
    confirmation = getpass("Confirm password: ")
    if len(password) < 12:
        raise SystemExit("Password must contain at least 12 characters")
    if password != confirmation:
        raise SystemExit("Passwords do not match")
    service = AuthService(SessionFactory, get_settings())
    await service.ensure_roles()
    try:
        user = await service.create_user(username, password, RoleName.SUPER_ADMIN)
    finally:
        await close_database()
    print(f"Created SUPER_ADMIN user {user['username']}")


def main() -> None:
    asyncio.run(_bootstrap())


if __name__ == "__main__":
    main()
