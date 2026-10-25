"""Read-only fingerprints, or rollback-only 0026 lock rehearsal in a restored clone."""
import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from time import perf_counter

API = Path(__file__).resolve().parents[1]
ROOT = API.parents[1]
sys.path.insert(0, str(API / "src"))
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from interview_api.core.config import settings


async def inspect(database, *, lock_rehearsal=False):
    if lock_rehearsal and not database.startswith("interview_agent_v21_v22_migration_review_"):
        raise ValueError("DDL lock rehearsal is ONLY allowed in the dedicated restored clone")
    if database != "interview_agent" and not database.startswith("interview_agent_v21_v22_"):
        raise ValueError("Unrelated database refused")
    engine = create_async_engine(make_url(settings.database_url).set(database=database))
    try:
        async with engine.connect() as conn:
            transaction = await conn.begin()
            try:
                if not lock_rehearsal:
                    await conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
                await conn.execute(text("SET LOCAL lock_timeout = '3s'"))
                await conn.execute(text("SET LOCAL statement_timeout = '15s'"))
                revision = await conn.scalar(text("SELECT version_num FROM alembic_version"))
                value = dict(database=database, revision=revision, tables={}, lock_rehearsal=lock_rehearsal)
                if lock_rehearsal:
                    assert revision == "0025", "Rehearse only before the isolated upgrade"
                    def upgrade(sync):
                        from alembic.migration import MigrationContext
                        from alembic.operations import Operations
                        path = API / "alembic/versions/0026_capability_memory.py"
                        spec = importlib.util.spec_from_file_location("reviewed_0026", path)
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)
                        with Operations.context(MigrationContext.configure(sync)):
                            module.upgrade()
                    start = perf_counter()
                    await conn.run_sync(upgrade)
                    value["ddl_elapsed_ms"] = round((perf_counter() - start) * 1000, 2)
                    value["locks"] = [dict(r) for r in (await conn.execute(text("""
                        SELECT c.relname, l.mode, l.granted FROM pg_locks l
                        JOIN pg_class c ON c.oid=l.relation
                        WHERE l.pid=pg_backend_pid() AND c.relnamespace='public'::regnamespace
                        ORDER BY c.relname,l.mode
                    """))).mappings()]
                else:
                    names = (await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"))).scalars().all()
                    for name in names:
                        # Names come only from the database's public table catalog.
                        quoted = '"' + name.replace('"', '""') + '"'
                        row = (await conn.execute(text(f"SELECT count(*) AS rows, md5(coalesce(string_agg(row_to_json(t)::text, '' ORDER BY row_to_json(t)::text), '')) AS fingerprint FROM public.{quoted} t"))).mappings().one()
                        value["tables"][name] = dict(row)
                    value["capability_constraints"] = [dict(r) for r in (await conn.execute(text("""
                        SELECT conname,contype::text AS contype,convalidated,conrelid::regclass::text AS table_name,
                               pg_get_constraintdef(oid) AS definition
                        FROM pg_constraint WHERE conrelid IN
                          (SELECT oid FROM pg_class WHERE relname IN ('capability_memories','capability_memory_versions')
                           AND relnamespace='public'::regnamespace) ORDER BY conname
                    """))).mappings()]
                    value["capability_indexes"] = [dict(r) for r in (await conn.execute(text("""
                        SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public'
                          AND tablename IN ('capability_memories','capability_memory_versions') ORDER BY indexname
                    """))).mappings()]
                    if database.startswith("interview_agent_v21_v22_live_prepare_"):
                        from interview_api.modules.candidate_profile.evidence import digest
                        value["prepared_sessions"] = []
                        records = (await conn.execute(text("""
                            SELECT s.id,s.user_id,s.status,s.state_version,s.active_question_id,s.started_at,
                              c.context_json,c.content_fingerprint,
                              (SELECT count(*) FROM interview_agent_calls a WHERE a.session_id=s.id) model_calls,
                              (SELECT count(*) FROM interview_session_questions q WHERE q.session_id=s.id) questions,
                              (SELECT count(*) FROM interview_answers a WHERE a.session_id=s.id) answers,
                              (SELECT count(*) FROM interview_evaluations e WHERE e.session_id=s.id) evaluations
                            FROM interview_sessions s JOIN interview_context_snapshots c ON c.id=s.context_snapshot_id
                            WHERE s.title LIKE 'DEMO LIVE PENDING AUTHORIZATION %' ORDER BY s.id
                        """))).mappings()
                        for record in records:
                            row = dict(record)
                            context = row.pop("context_json")
                            assert row["content_fingerprint"] == digest(context)
                            assert row["status"] == "READY" and row["started_at"] is None and row["active_question_id"] is None
                            assert row["model_calls"] == row["questions"] == row["answers"] == row["evaluations"] == 0
                            row.update(prompt_version=context["adaptive_policy"]["prompts"]["interview_main"]["version"],
                                snapshot_verified=True, memory_guided_plan=context["memory_guided_plan"])
                            value["prepared_sessions"].append(row)
                return value
            finally:
                await transaction.rollback()  # Rehearsal DDL NEVER commits.
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--lock-rehearsal", action="store_true")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if output.exists() or not output.is_relative_to((ROOT / ".runtime/acceptance").resolve()):
        raise SystemExit("Use a fresh ignored audit artifact")
    value = asyncio.run(asyncio.wait_for(inspect(args.database, lock_rehearsal=args.lock_rehearsal), 90))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(database=args.database, revision=value["revision"],
        table_count=len(value["tables"]), rollback_only_ddl=args.lock_rehearsal)))
