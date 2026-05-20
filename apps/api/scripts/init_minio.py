"""Initialize MinIO bucket for the interview-agent project.

Usage:
    cd apps/api
    uv run python scripts/init_minio.py
"""

import asyncio
import sys

from minio import Minio
from minio.error import S3Error

from interview_api.core.config import settings


async def main():
    client = Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )

    bucket = settings.minio_bucket
    try:
        found = client.bucket_exists(bucket)
        if found:
            print(f"Bucket '{bucket}' already exists.")
        else:
            client.make_bucket(bucket)
            print(f"Bucket '{bucket}' created successfully.")
    except S3Error as e:
        print(f"ERROR: Failed to ensure bucket '{bucket}': {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
