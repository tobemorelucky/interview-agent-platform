# 18 App-layer Governance

This stage adds application-level governance inside the FastAPI service. It does
not introduce Kong, Nginx, an external API gateway, LangGraph, ReAct, agent
trace logging, crawling changes, or Phase 4 extraction/routing/reliability
agents.

## Runtime Features

- `X-Request-ID` is accepted from incoming requests or generated with UUIDv4.
- Responses include `X-Request-ID`.
- Error responses use:

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human readable message",
    "request_id": "request-id",
    "details": {}
  }
}
```

- Admin audit logs are available at `GET /api/v1/admin/audit/logs`.
- Redis-backed rate limits are applied to:
  - interview chat messages,
  - memory item writes,
  - interview memory consolidation,
  - experience task creation,
  - experience task search.
- Redis locks protect:
  - experience task search,
  - interview memory consolidation.
- Public HTTP URL validation protects experience source fetching from local and
  private network targets.

## Local Verification

```bash
cd apps/api
uv run alembic upgrade head
uv run python scripts/smoke_governance.py
```

The smoke script requires PostgreSQL. Redis is required for the rate-limit and
lock checks.
