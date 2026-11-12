"""Local operational entry point. No domain/agent behavior or credentials emitted."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps/api/src"), str(ROOT / "apps/worker/src")]
DEMO_DB = "interview_agent_m6_f3_ux_demo"


def configuration(demo=False):
    from interview_api.core import config as settings_module
    Settings = settings_module.Settings
    from sqlalchemy.engine import make_url
    settings = Settings(_env_file=ROOT / ".env")
    if demo:
        os.environ["DATABASE_URL"] = make_url(settings.database_url).set(database=DEMO_DB).render_as_string(hide_password=False)
        os.environ["CELERY_BROKER_URL"] = make_url(settings.celery_broker_url).set(database="13").render_as_string(hide_password=False)
        os.environ["CELERY_RESULT_BACKEND"] = make_url(settings.celery_result_backend).set(database="14").render_as_string(hide_password=False)
        os.environ["MINIO_BUCKET"] = "interview-agent-demo"
        for field in Settings.model_fields:
            if field.endswith("api_key"):
                os.environ[field.upper()] = ""
        settings = Settings(_env_file=ROOT / ".env")
        settings_module.settings = settings
    return settings


async def revision(settings):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    engine = create_async_engine(settings.database_url, connect_args={"timeout": 5})
    try:
        async with engine.connect() as conn:
            return (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()
    finally:
        await engine.dispose()


def migrate(settings, demo):
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    if demo:
        # CREATE DATABASE cannot be transactional; only this exact DEMO name is allowed.
        import asyncpg
        from sqlalchemy.engine import make_url
        async def ensure_demo():
            url = make_url(settings.database_url).set(database="postgres", drivername="postgresql")
            conn = await asyncpg.connect(url.render_as_string(hide_password=False), timeout=5)
            try:
                exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", DEMO_DB)
                if not exists:
                    await conn.execute(f'CREATE DATABASE "{DEMO_DB}"')
            finally:
                await conn.close()
        asyncio.run(ensure_demo())
    os.chdir(ROOT / "apps/api")
    cfg = Config("alembic.ini")
    # The head belongs to the checked-in migration scripts, never to a
    # constant: pinning one revision here silently blocks every later
    # migration from ever starting the platform.
    heads = ScriptDirectory.from_config(cfg).get_heads()
    if len(heads) != 1:
        raise RuntimeError("AMBIGUOUS_MIGRATION_HEAD")
    head = heads[0]
    # Normal mode never creates, drops or rebuilds a database.
    command.upgrade(cfg, "head")
    if asyncio.run(revision(settings)) != [head]:
        raise RuntimeError("REVISION_MISMATCH")
    print(json.dumps({"revision": head, "mode": "DEMO_REPLAY" if demo else "LOCAL_LIVE"}))


def seed_if_missing():
    from sqlalchemy import text
    from interview_api.infrastructure.db.session import async_session_factory
    async def exists():
        async with async_session_factory() as db:
            return bool((await db.execute(text("SELECT 1 FROM users WHERE email LIKE 'm6-demo-%@example.com' LIMIT 1"))).scalar())
    if asyncio.run(exists()):
        print("DEMO fixture already exists; retained without reseeding")
        return
    sys.argv = ["replay_runtime.py", "seed"]
    runpy.run_path(str(ROOT / "apps/web/qa/replay_runtime.py"), run_name="__main__")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["config", "revision", "redis-ping", "migrate", "seed", "api", "worker", "worker-ping"])
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    try:
        s = configuration(args.demo)
    except Exception:
        print("CONFIGURATION_REJECTED: check APP_ENV and JWT_SECRET_KEY; values are not logged", file=sys.stderr)
        return 2
    if args.action == "config":
        from sqlalchemy.engine import make_url
        db = make_url(s.database_url)
        redis = make_url(s.redis_url)
        print(json.dumps(dict(app_env=s.app_env, database=db.database, db_host=db.host,
            db_port=db.port or 5432, redis_host=redis.host, redis_port=redis.port or 6379,
            minio_endpoint=s.minio_endpoint, minio_health_url=f"{'https' if s.minio_secure else 'http'}://{s.minio_endpoint}/minio/health/live",
            milvus_host=s.milvus_host, milvus_port=s.milvus_port,
            searxng_url=s.searxng_base_url, provider=s.llm_provider,
            primary_model=s.llm_model, fallback_model=s.llm_fallback_model,
            mode="DEMO_REPLAY" if args.demo else "LIVE_MODEL_CONFIG / Live Search frozen",
            vector_required=bool(s.resume_kb_retrieval_enabled),
            api_port=8013 if args.demo else 8000, web_port=5174 if args.demo else 5173,
            model_key_present=bool(s.llm_api_key), jwt_development_default=s.jwt_secret_key=="change_me_to_a_random_string")))
    elif args.action == "redis-ping":
        from redis import Redis
        with Redis.from_url(s.redis_url, socket_connect_timeout=2, socket_timeout=2) as client:
            print(json.dumps({"redis_pong": bool(client.ping())}))
    elif args.action == "revision":
        try:
            print(json.dumps({"revision": asyncio.run(revision(s))}))
        except Exception:
            print(json.dumps({"revision": None, "error": "DATABASE_UNAVAILABLE_OR_UNMIGRATED"}))
            return 1
    elif args.action == "migrate":
        try:
            migrate(s, args.demo)
        except Exception as exc:
            # Do not leak a DSN/password from driver exception text.
            print(f"MIGRATION_FAILED ({type(exc).__name__}); no workaround performed", file=sys.stderr)
            return 1
    elif args.action == "seed":
        if not args.demo:
            raise SystemExit("SEED_REQUIRES_EXPLICIT_DEMO")
        seed_if_missing()
    elif args.action == "api":
        if args.demo:
            sys.argv = ["replay_runtime.py", "api"]
            runpy.run_path(str(ROOT / "apps/web/qa/replay_runtime.py"), run_name="__main__")
        else:
            import uvicorn
            uvicorn.run("interview_api.main:app", host="127.0.0.1", port=8000)
    elif args.action in {"worker", "worker-ping"}:
        if args.action == "worker-ping":
            import socket
            from sqlalchemy.engine import make_url
            broker = make_url(s.celery_broker_url)
            try:
                with socket.create_connection((broker.host, broker.port or 6379), timeout=2):
                    pass
            except OSError:
                print(json.dumps({"pong": False, "error": "BROKER_UNAVAILABLE"}))
                return 1
        from interview_worker.celery_app import app
        name = f"iap-{'demo' if args.demo else 'local'}@{os.environ.get('COMPUTERNAME', 'localhost')}"
        if args.action == "worker-ping":
            result = app.control.inspect(destination=[name], timeout=3).ping()
            print(json.dumps({"worker": name, "pong": bool(result and result.get(name, {}).get("ok")=="pong")}))
            return 0 if result else 1
        if args.demo:
            from unittest.mock import patch
            with patch("httpx.AsyncClient.request", side_effect=RuntimeError("DEMO_OUTBOUND_HTTP_BLOCKED")):
                app.worker_main(["worker", "--pool=solo", "--loglevel=INFO", "--hostname", name, "--queues", "iap_f4_demo"])
        else:
            app.worker_main(["worker", "--pool=solo", "--loglevel=INFO", "--hostname", name])
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception as exc:
        print(f"DELIVERY_OPERATION_FAILED ({type(exc).__name__}); credentials/driver details suppressed", file=sys.stderr)
        code = 1
    raise SystemExit(code)
