"""Maintainer-only MANUAL_SOURCE_IMPORT + LIVE MODEL; no API/UI or discovery."""
import argparse
import asyncio
import json

from interview_api.modules.public_career.manual_import import ManualDocument, ManualImportService, require_live_router


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--source-url", required=True)
    result.add_argument("--source-type", required=True, choices=["official", "community"])
    result.add_argument("--document-type", required=True, choices=["job_jd", "interview_experience"])
    result.add_argument("--file", required=True)
    result.add_argument("--company", required=True)
    result.add_argument("--job-title", required=True)
    return result


async def run(args):
    # Read/validate before creating any DB records. Help does not load settings.
    document = ManualDocument.from_file(**vars(args))
    import interview_api.modules.models  # noqa: F401
    from interview_api.infrastructure.db.engine import engine
    from interview_api.infrastructure.db.session import async_session_factory
    from interview_api.infrastructure.models.router import build_model_router
    from interview_api.core.config import settings
    try:
        if len(document.content) > settings.public_research_max_evidence_chars:
            raise ValueError("MANUAL_BODY_EXCEEDS_EVIDENCE_BUDGET: split/select an explicitly documented source excerpt")
        router = build_model_router()
        require_live_router(router)
        service = ManualImportService(async_session_factory)
        prepared = await service.prepare(document)
        result = await service.execute(prepared, router=router)
        # A completed-task reuse makes no model call; distinguish configured
        # mode from actual provider/model invocations recorded in StepRun.
        print(json.dumps({**result, "configured_model_mode": "LIVE"}, ensure_ascii=False))
        return 0 if result["status"] == "COMPLETED" else 1
    finally:
        await engine.dispose()


def main():
    args = parser().parse_args()
    try:
        return asyncio.run(run(args))
    except Exception as exc:
        # Never dump DB DSNs, model responses, credentials, or local body text.
        print(json.dumps({"status": "FAILED", "error_code": getattr(exc, "code", type(exc).__name__),
                          "acquisition_mode": "MANUAL_SOURCE_IMPORT"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
