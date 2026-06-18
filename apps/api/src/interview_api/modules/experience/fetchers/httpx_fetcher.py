"""httpx based HTML content fetcher."""

import httpx

from interview_api.core.errors import ValidationAppError
from interview_api.core.url_safety import validate_public_http_url
from interview_api.modules.experience.fetchers.base import ContentFetcher, FetchResult
from interview_api.modules.experience.fetchers.text_extractor import extract_text_from_html


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0 Safari/537.36 InterviewAgentPlatform/0.1"
)


class HttpxContentFetcher(ContentFetcher):
    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        max_response_bytes: int = 2_000_000,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.user_agent = user_agent

    async def fetch(self, url: str) -> FetchResult:
        try:
            safe_url = validate_public_http_url(url)
        except ValidationAppError as exc:
            return _failed(url, error_message=_url_validation_error_code(exc))

        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=True,
                headers=headers,
            ) as client:
                async with client.stream("GET", safe_url) as response:
                    status_code = response.status_code
                    final_url = str(response.url)
                    content_type = response.headers.get("content-type")

                    try:
                        validate_public_http_url(final_url)
                    except ValidationAppError as exc:
                        return _failed(
                            url,
                            final_url=final_url,
                            status_code=status_code,
                            content_type=content_type,
                            error_message=_url_validation_error_code(exc),
                        )

                    if status_code < 200 or status_code >= 300:
                        return _failed(
                            url,
                            final_url=final_url,
                            status_code=status_code,
                            content_type=content_type,
                            error_message=_http_error_code(status_code),
                        )

                    if "text/html" not in (content_type or "").lower():
                        return _failed(
                            url,
                            final_url=final_url,
                            status_code=status_code,
                            content_type=content_type,
                            error_message="non_html_content",
                        )

                    chunks: list[bytes] = []
                    total_size = 0
                    async for chunk in response.aiter_bytes():
                        total_size += len(chunk)
                        if total_size > self.max_response_bytes:
                            return _failed(
                                url,
                                final_url=final_url,
                                status_code=status_code,
                                content_type=content_type,
                                error_message="response_too_large",
                            )
                        chunks.append(chunk)
        except httpx.TimeoutException:
            return _failed(url, error_message="timeout")
        except httpx.HTTPError as exc:
            return _failed(url, error_message=f"request_failed: {exc}")

        encoding = response.encoding or "utf-8"
        html = b"".join(chunks).decode(encoding, errors="replace")
        extracted = extract_text_from_html(html, url=final_url)
        if extracted.error_message:
            return _failed(
                url,
                final_url=final_url,
                status_code=status_code,
                content_type=content_type,
                title=extracted.title,
                error_message=extracted.error_message,
            )

        return FetchResult(
            url=url,
            final_url=final_url,
            status_code=status_code,
            content_type=content_type,
            title=extracted.title,
            raw_text=extracted.raw_text,
            fetch_method="httpx",
        )


def _failed(
    url: str,
    *,
    final_url: str | None = None,
    status_code: int | None = None,
    content_type: str | None = None,
    title: str | None = None,
    error_message: str,
) -> FetchResult:
    return FetchResult(
        url=url,
        final_url=final_url,
        status_code=status_code,
        content_type=content_type,
        title=title,
        raw_text=None,
        error_message=error_message,
        fetch_method="httpx",
    )


def _http_error_code(status_code: int) -> str:
    if status_code == 403:
        return "http_403"
    if status_code == 404:
        return "http_404"
    return "http_error"


def _url_validation_error_code(exc: ValidationAppError) -> str:
    message = exc.message.lower()
    if "private" in message or "local" in message or "localhost" in message:
        return "ssrf_blocked"
    return "invalid_url"
