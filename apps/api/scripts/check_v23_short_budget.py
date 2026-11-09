"""Offline hard-limit checks for the short acceptance meter; no network."""
import asyncio
import json
from pathlib import Path
import tempfile
import time
import httpx
from accept_v23_live import LiveBatch, Meter, BudgetStop


async def main():
    batch = LiveBatch(Path(tempfile.gettempdir()) / 'v23_short_budget_check.json')
    batch.limits = dict(logical=7, http=9, tokens=120000, seconds=600)
    inner = type('Offline', (), dict(provider_name='offline', model_name='offline',
        base_url='', timeout=180, max_output_tokens=3000,
        thinking_type='disabled', governed=True))()
    meter = Meter(inner, batch, 'interview_main', None)
    batch.logical = 7
    try:
        await meter.generate([dict(content='normal')], schema={})
    except BudgetStop as error:
        assert str(error) == 'LOGICAL_BUDGET'
    else:
        raise AssertionError('Logical limit bypassed')
    for records, charged in (([{}] * 9, 0), ([], 119999)):
        batch.stop_reason = None
        batch.records, batch.charged = records, charged
        request = httpx.Request('POST', 'https://offline.invalid', content=json.dumps(
            dict(model='deepseek-v4-pro-ga-260813', max_tokens=3000, messages=[])))
        try:
            await batch.on_request(request)
        except BudgetStop as error:
            assert str(error) == 'HTTP_OR_TOKEN_BUDGET'
        else:
            raise AssertionError('HTTP/token limit bypassed')
    batch.stop_reason = None
    batch.start = time.monotonic() - 601
    try:
        batch.check()
    except BudgetStop:
        pass
    else:
        raise AssertionError('Deadline bypassed')
    print('PASS: logical=7, HTTP=9, tokens=120000, seconds=600; zero HTTP sent')


if __name__ == '__main__':
    asyncio.run(main())
