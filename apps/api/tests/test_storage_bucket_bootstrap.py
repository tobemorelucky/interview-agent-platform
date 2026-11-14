"""The bucket has to exist before put_object; a fresh environment has none.

Round 20 hit this on the first upload: the acceptance launcher pointed the app
at a new per-round bucket, `upload()` called put_object straight away, and MinIO
answered NoSuchBucket as a 500 on a resume the user had just chosen.
"""
import asyncio
import io

from interview_api.infrastructure.storage import provider as storage_provider


class _FakeMinio:
    def __init__(self):
        self.calls = []

    def bucket_exists(self, name):
        self.calls.append(("exists", name))
        return False

    def make_bucket(self, name):
        self.calls.append(("make", name))

    def put_object(self, bucket, key, data, length=None, content_type=None):
        assert isinstance(data, io.BytesIO)
        self.calls.append(("put", bucket, key, length))
        return None


def _provider(monkeypatch):
    fake = _FakeMinio()
    monkeypatch.setattr(storage_provider, "Minio", lambda *a, **k: fake)
    return storage_provider.MinioObjectStorageProvider(), fake


def test_upload_creates_a_missing_bucket_before_writing(monkeypatch):
    provider, fake = _provider(monkeypatch)
    asyncio.run(provider.upload("interview-agent-r20-rc", "u/1/a.txt", b"hello", "text/plain"))
    kinds = [call[0] for call in fake.calls]
    assert kinds == ["exists", "make", "put"], kinds
    assert fake.calls[-1] == ("put", "interview-agent-r20-rc", "u/1/a.txt", 5)


def test_upload_does_not_recreate_an_existing_bucket(monkeypatch):
    provider, fake = _provider(monkeypatch)
    fake.bucket_exists = lambda name: (fake.calls.append(("exists", name)), True)[1]
    asyncio.run(provider.upload("b", "k", b"x", "text/plain"))
    assert [call[0] for call in fake.calls] == ["exists", "put"]
