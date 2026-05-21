import io

from minio import Minio
from minio.error import S3Error

from interview_api.core.config import settings

from . import ObjectStorageProvider


class MinioObjectStorageProvider:
    def __init__(self) -> None:
        self._client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )

    async def ensure_bucket(self, bucket_name: str) -> None:
        try:
            found = self._client.bucket_exists(bucket_name)
            if not found:
                self._client.make_bucket(bucket_name)
        except S3Error:
            raise

    async def upload(
        self, bucket_name: str, object_key: str, data: bytes, content_type: str
    ) -> None:
        self._client.put_object(
            bucket_name,
            object_key,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )

    async def download(self, bucket_name: str, object_key: str) -> bytes:
        response = None
        try:
            response = self._client.get_object(bucket_name, object_key)
            return response.read()
        finally:
            if response is not None:
                response.close()
                response.release_conn()

    async def get_text(self, bucket_name: str, object_key: str) -> str:
        data = await self.download(bucket_name, object_key)
        return data.decode("utf-8")

    async def delete(self, bucket_name: str, object_key: str) -> None:
        """Delete an object from MinIO.  Logs and skips if the object does not exist."""
        import logging
        logger = logging.getLogger(__name__)
        try:
            self._client.remove_object(bucket_name, object_key)
        except S3Error as e:
            if e.code == "NoSuchKey":
                logger.warning(
                    "MinIO object not found during delete: bucket=%s key=%s",
                    bucket_name,
                    object_key,
                )
            else:
                raise
