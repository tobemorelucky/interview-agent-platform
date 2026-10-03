"""Backfill M1 fingerprints and unique exact Company/Job links."""

from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_api.core.config import settings
from interview_api.modules.public_career.backfill import backfill_public_career_data


async def run(*, commit: bool) -> None:
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            result = await backfill_public_career_data(session)
            if commit:
                await session.commit()
            else:
                await session.rollback()
            print(
                json.dumps(
                    {"committed": commit, **result.to_dict()},
                    ensure_ascii=False,
                    indent=2,
                )
            )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Persist updates. Without this flag the command is a dry run.",
    )
    args = parser.parse_args()
    asyncio.run(run(commit=args.commit))


if __name__ == "__main__":
    main()

