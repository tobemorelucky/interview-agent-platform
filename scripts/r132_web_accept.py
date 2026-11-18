"""Bounded R132 acceptance driver: search -> select/fetch -> extract, one step per call."""
import argparse, asyncio, json, os, sys, uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api/src"))
if not os.environ.get("R132_DATABASE_URL"):
    raise SystemExit("set R132_DATABASE_URL to the isolated acceptance database")
os.environ["DATABASE_URL"] = os.environ["R132_DATABASE_URL"]

from interview_api.infrastructure.db.session import async_session_factory
from interview_api.modules.public_career.web_collection import WebCollectionService
from interview_api.modules.public_career.research.agent import PublicDataResearchAgent


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["search", "detail", "select", "extract"])
    p.add_argument("--company", default="")
    p.add_argument("--title", default="")
    p.add_argument("--keywords", default="面经")
    p.add_argument("--sites", default="nowcoder.com")
    p.add_argument("--count", type=int, default=5)
    p.add_argument("--task", type=int)
    p.add_argument("--url")
    p.add_argument("--document-type", default="interview_experience")
    p.add_argument("--source-type", default="community")
    p.add_argument("--request-key", default=None)
    args = p.parse_args()
    service = WebCollectionService(async_session_factory)
    if args.action == "search":
        data = await service.search(dict(company=args.company, job_title=args.title,
            keywords=args.keywords, count=args.count, need_content=False,
            sites=[s for s in args.sites.split(",") if s],
            request_key=args.request_key or str(uuid.uuid4())))
    elif args.action == "detail":
        data = await service.detail(args.task)
    elif args.action == "select":
        data = await service.select(args.task, args.url, args.document_type, args.source_type)
    else:
        data = await PublicDataResearchAgent(async_session_factory).run(args.task)
    print(json.dumps(data, ensure_ascii=False, indent=1, default=str)[:12000])

asyncio.run(main())
