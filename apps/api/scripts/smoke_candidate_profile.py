"""Real local TXT upload, storage/parser/Celery, with explicit REPLAY model smoke.

Start m3_demo_runtime.py api and worker on an isolated migrated database first.
No Search/Fetch/Research calls. No live-model acceptance claim.
"""
import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import select

from interview_api.infrastructure.db.session import async_session_factory
from interview_api.infrastructure.db.engine import engine
from interview_api.infrastructure.storage.provider import MinioObjectStorageProvider
from interview_api.core.config import settings
from interview_api.modules.resume.models import Resume


async def main(base_url):
    if "m3_smoke" not in settings.database_url:
        raise SystemExit("Requires isolated m3_smoke database")
    file = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "m3_resume_demo.txt"
    storage = MinioObjectStorageProvider()
    await storage.ensure_bucket(settings.minio_bucket)
    email = f"m3demo-{uuid4().hex[:12]}@example.com"
    password = "M3ReplayFixtureDemo!"
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
        register = await client.post("/api/v1/auth/register", json={"email": email, "username": email.split("@")[0], "password": password})
        register.raise_for_status()
        login = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
        login.raise_for_status()
        client.headers["Authorization"] = "Bearer " + login.json()["data"]["access_token"]
        upload = await client.post("/api/v1/resumes/upload", files={"file": (file.name, file.read_bytes(), "text/plain")})
        upload.raise_for_status()
        rid = upload.json()["data"]["id"]
        for _ in range(60):
            current_response = await client.get("/api/v1/candidate-profile")
            current_response.raise_for_status()
            state = current_response.json()["data"]
            if state["current"] and state["current"]["source_resume_id"] == rid:
                break
            if any(row["status"] == "FAILED" and row["resume_id"] == rid for row in state["generations"]):
                raise RuntimeError(state["generations"])
            await asyncio.sleep(1)
        else:
            raise RuntimeError("profile generation timeout")
        profile = state["current"]
        assert profile["provider"] == "replay" and profile["producer"] == "REPLAY_FIXTURE"
        assert all(profile["normalized"][key] for key in ("projects", "education", "skills", "work_experience"))
        report = await client.get(f"/api/v1/resumes/{rid}/report")
        report.raise_for_status()
        assert report.json()["data"]["summary_json"]["mode"] == "replay_fixture_demo"
        async with async_session_factory() as db:
            resume = await db.get(Resume, rid)
            assert resume.raw_text == file.read_text(encoding="utf-8")
            object_key = resume.storage_key
        assert await storage.download(settings.minio_bucket, object_key) == file.read_bytes()
        retry = await client.post("/api/v1/candidate-profile/generate", json={"resume_id": rid, "regenerate": False})
        retry.raise_for_status()
        await asyncio.sleep(1)
        assert (await client.get("/api/v1/candidate-profile")).json()["data"]["current"]["id"] == profile["id"]
        deleted = await client.delete(f"/api/v1/resumes/{rid}")
        assert deleted.status_code == 409
        assert await storage.download(settings.minio_bucket, object_key) == file.read_bytes()
        print(json.dumps(dict(status="COMPLETED", model_mode="replay_fixture_demo", live_model=False,
            file=str(file), resume_id=rid, task_id=upload.json()["data"]["task_id"],
            version_id=profile["id"], version_number=profile["version_number"],
            evidence_count=len(profile["evidence"]), normalized_counts={key: len(value) for key, value in profile["normalized"].items()},
            resume_delete_status=deleted.status_code, minio_object_retained=True,
            demo_login={"email": email, "password": password}), ensure_ascii=False, indent=2))
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    asyncio.run(main(parser.parse_args().base_url))
