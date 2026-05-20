"""Seed an admin user from environment variables.

Required environment variables:
    ADMIN_EMAIL
    ADMIN_USERNAME
    ADMIN_PASSWORD

Usage:
    cd apps/api
    uv run python scripts/seed_admin.py
"""

import asyncio
import os
import sys

from sqlalchemy import select

# Register all models before DB access
import interview_api.modules.models  # noqa: F401

from interview_api.core.security import hash_password
from interview_api.infrastructure.db.engine import engine
from interview_api.infrastructure.db.session import async_session_factory
from interview_api.modules.users.models import User


def check_env() -> dict:
    missing = []
    for key in ("ADMIN_EMAIL", "ADMIN_USERNAME", "ADMIN_PASSWORD"):
        if not os.getenv(key):
            missing.append(key)
    if missing:
        print(f"ERROR: Missing environment variables: {', '.join(missing)}")
        print("Set them before running this script, e.g.:")
        print("  export ADMIN_EMAIL=admin@example.com")
        print("  export ADMIN_USERNAME=admin")
        print("  export ADMIN_PASSWORD=change_me_admin")
        sys.exit(1)
    return {
        "email": os.getenv("ADMIN_EMAIL"),
        "username": os.getenv("ADMIN_USERNAME"),
        "password": os.getenv("ADMIN_PASSWORD"),
    }


async def seed_admin(env: dict) -> None:
    async with async_session_factory() as session:
        # Check if admin already exists (by email)
        result = await session.execute(
            select(User).where(User.email == env["email"])
        )
        existing = result.scalar_one_or_none()
        if existing:
            print(f"Admin user already exists: {existing.email} (id={existing.id})")
            return

        # Check if username is already taken by another user
        result = await session.execute(
            select(User).where(User.username == env["username"])
        )
        username_taken = result.scalar_one_or_none()
        if username_taken:
            print(
                f"ERROR: Username '{env['username']}' is already taken "
                f"(by user id={username_taken.id}, email={username_taken.email})"
            )
            sys.exit(1)

        user = User(
            email=env["email"],
            username=env["username"],
            password_hash=hash_password(env["password"]),
            role="ADMIN",
        )
        session.add(user)
        await session.commit()
        print(f"Admin user created: {user.email} (id={user.id})")


async def main():
    env = check_env()
    await seed_admin(env)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
