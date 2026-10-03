"""One minimal LIVE structured connection check, with availability-only fallback."""
import asyncio
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict
from interview_api.infrastructure.models.router import EXTRACT_ROUTE, build_model_router
from interview_api.infrastructure.models.structured import generate_structured


class ConnectionProbe(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ok: Literal[True]


async def main():
    router = build_model_router()
    primary = router.primary(EXTRACT_ROUTE)
    assert primary.model_name == 'deepseek-v4-pro-ga-260813'
    assert router.fallback_routes[EXTRACT_ROUTE].model_name == 'doubao-seed-2-1-turbo-260628'
    result = dict(model_mode='LIVE',primary_model=primary.model_name,success=False)
    try:
        structured = await generate_structured(router,EXTRACT_ROUTE,
            system='Return only JSON. This is a connection check; return {"ok":true}.',
            payload={'purpose':'F2 minimum structured connection check'},response_model=ConnectionProbe,
            timeout_seconds=primary.timeout)
        actual = router.resolve(EXTRACT_ROUTE)
        result.update(success=True,selected_model=actual.model_name,provider=actual.provider_name,
            structured_repair_count=structured.structured_retry,usage=structured.usage,
            invocations=structured.invocations,latency_ms=structured.latency_ms)
    except Exception as exc:
        result.update(error_code=getattr(exc,'code',type(exc).__name__),
            invocations=getattr(exc,'invocations',[]),structured_repair_count=int(getattr(exc,'retried',False)))
    print('F2_CONNECTIVITY '+json.dumps(result),flush=True)
    return result['success']


if __name__=='__main__':
    raise SystemExit(0 if asyncio.run(main()) else 1)
