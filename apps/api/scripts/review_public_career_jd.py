"""Explicit maintainer review of an existing JD; never creates final entities."""
import argparse
import asyncio
import json


async def run(args):
    import interview_api.modules.models  # noqa: F401
    from interview_api.infrastructure.db.engine import engine
    from interview_api.infrastructure.db.session import async_session_factory
    from interview_api.modules.public_career.review import PublicCareerReviewService
    try:
        async with async_session_factory() as db:
            result = await PublicCareerReviewService(db).publish_jd(args.jd_version_id,
                reviewer_id=args.reviewer_id, reason=args.reason,
                matching_constraints=json.loads(args.constraints), requirement_topics=json.loads(args.topics))
            await db.commit()
            print(json.dumps(result, ensure_ascii=False))
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="人工审核并发布现有公共JD；保留原文与来源身份")
    parser.add_argument("--jd-version-id", type=int, required=True)
    parser.add_argument("--reviewer-id", type=int, required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--constraints", default="{}", help="原文支持的资格/地点JSON，不推断缺失信息")
    parser.add_argument("--topics", default="{}", help="要求ID到精确文本主题JSON，不表示满足完整要求")
    try:
        asyncio.run(run(parser.parse_args()))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "error_code": getattr(exc, "code", type(exc).__name__)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
