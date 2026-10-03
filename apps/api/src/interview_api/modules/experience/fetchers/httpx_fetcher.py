"""Safe httpx fetch adapter for the shared public FetchProvider contract."""

from __future__ import annotations

from urllib.parse import urljoin

import httpx

from interview_api.core.config import settings
from interview_api.core.url_safety import URLSafetyError, URLSafetyPolicy
from interview_api.modules.experience.fetchers.base import ContentFetcher, FetchResult
from interview_api.modules.experience.fetchers.text_extractor import extract_text_from_html
from interview_api.modules.public_career.tools.contracts import ProviderMode


DEFAULT_USER_AGENT = "InterviewAgentPublicData/0.2 (+safe-public-fetch)"


class HttpxContentFetcher(ContentFetcher):
    def __init__(
        self,
        *,
        timeout_seconds: float | None = None,
        max_response_bytes: int | None = None,
        max_content_chars: int | None = None,
        max_redirects: int | None = None,
        allowed_content_types: set[str] | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        safety_policy: URLSafetyPolicy | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds or settings.public_fetch_timeout_seconds
        self.max_response_bytes = (
            max_response_bytes or settings.public_fetch_max_response_bytes
        )
        self.max_content_chars = (
            max_content_chars or settings.public_fetch_max_content_chars
        )
        self.max_redirects = max_redirects or settings.public_fetch_max_redirects
        configured_types = {
            item.strip().lower()
            for item in settings.public_fetch_allowed_content_types.split(",")
            if item.strip()
        }
        self.allowed_content_types = allowed_content_types or configured_types
        self.user_agent = user_agent
        self.safety_policy = safety_policy or URLSafetyPolicy()
        self._client = client

    @property
    def provider_name(self) -> str:
        return "safe_http_fetch"

    @property
    def provider_mode(self) -> ProviderMode:
        return ProviderMode.LIVE

    async def fetch(self, url: str) -> FetchResult:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml;q=0.9",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        client = self._client or httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=False,
            headers=headers,
        )
        current_url = url
        redirect_chain: list[str] = []
        try:
            for redirect_index in range(self.max_redirects + 1):
                try:
                    current_url = await self.safety_policy.validate(current_url)
                except URLSafetyError as exc:
                    return _failed(
                        url,
                        final_url=current_url if current_url != url else None,
                        error_code=exc.code,
                        error_message=_legacy_url_error(exc),
                        metadata={"redirect_chain": redirect_chain},
                    )

                request = client.build_request("GET", current_url, headers=headers)
                try:
                    response = await client.send(
                        request,
                        stream=True,
                        follow_redirects=False,
                    )
                except httpx.TimeoutException:
                    return _failed(
                        url,
                        final_url=current_url,
                        error_code="fetch_timeout",
                        error_message="timeout",
                        metadata={"redirect_chain": redirect_chain},
                    )
                except httpx.HTTPError:
                    return _failed(
                        url,
                        final_url=current_url,
                        error_code="fetch_connection_failed",
                        error_message="request_failed",
                        metadata={"redirect_chain": redirect_chain},
                    )

                try:
                    status_code = response.status_code
                    content_type_header = response.headers.get("content-type", "")
                    content_type = content_type_header.split(";", 1)[0].strip().lower()
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            return _failed(
                                url,
                                final_url=current_url,
                                status_code=status_code,
                                content_type=content_type or None,
                                error_code="invalid_redirect",
                                error_message="invalid_redirect",
                                metadata={"redirect_chain": redirect_chain},
                            )
                        if redirect_index >= self.max_redirects:
                            return _failed(
                                url,
                                final_url=current_url,
                                status_code=status_code,
                                content_type=content_type or None,
                                error_code="redirect_limit_exceeded",
                                error_message="redirect_limit_exceeded",
                                metadata={"redirect_chain": redirect_chain},
                            )
                        redirect_chain.append(current_url)
                        current_url = urljoin(current_url, location)
                        continue

                    if status_code < 200 or status_code >= 300:
                        return _failed(
                            url,
                            final_url=current_url,
                            status_code=status_code,
                            content_type=content_type or None,
                            error_code="fetch_http_error",
                            error_message=_http_error_code(status_code),
                            metadata={"redirect_chain": redirect_chain},
                        )
                    if content_type not in self.allowed_content_types:
                        return _failed(
                            url,
                            final_url=current_url,
                            status_code=status_code,
                            content_type=content_type or None,
                            error_code="unsupported_content_type",
                            error_message="non_html_content",
                            metadata={"redirect_chain": redirect_chain},
                        )
                    declared_length = response.headers.get("content-length")
                    if declared_length:
                        try:
                            if int(declared_length) > self.max_response_bytes:
                                return _failed(
                                    url,
                                    final_url=current_url,
                                    status_code=status_code,
                                    content_type=content_type,
                                    error_code="response_too_large",
                                    error_message="response_too_large",
                                    metadata={"redirect_chain": redirect_chain},
                                )
                        except ValueError:
                            pass

                    chunks: list[bytes] = []
                    total_size = 0
                    async for chunk in response.aiter_bytes():
                        total_size += len(chunk)
                        if total_size > self.max_response_bytes:
                            return _failed(
                                url,
                                final_url=current_url,
                                status_code=status_code,
                                content_type=content_type,
                                error_code="response_too_large",
                                error_message="response_too_large",
                                metadata={"redirect_chain": redirect_chain},
                            )
                        chunks.append(chunk)

                    encoding = response.encoding or "utf-8"
                    html = b"".join(chunks).decode(encoding, errors="replace")
                    extracted = extract_text_from_html(html, url=current_url)
                    if extracted.error_message:
                        return _failed(
                            url,
                            final_url=current_url,
                            status_code=status_code,
                            content_type=content_type,
                            title=extracted.title,
                            error_code=extracted.error_message,
                            error_message=extracted.error_message,
                            metadata={"redirect_chain": redirect_chain},
                        )
                    return FetchResult(
                        url=url,
                        final_url=current_url,
                        status_code=status_code,
                        content_type=content_type,
                        title=extracted.title,
                        raw_text=(extracted.raw_text or "")[: self.max_content_chars],
                        fetch_method="httpx",
                        metadata={
                            "redirect_chain": redirect_chain,
                            "response_bytes": total_size,
                        },
                    )
                finally:
                    await response.aclose()
        finally:
            if self._client is None:
                await client.aclose()

        return _failed(
            url,
            final_url=current_url,
            error_code="redirect_limit_exceeded",
            error_message="redirect_limit_exceeded",
            metadata={"redirect_chain": redirect_chain},
        )


def _failed(
    url: str,
    *,
    final_url: str | None = None,
    status_code: int | None = None,
    content_type: str | None = None,
    title: str | None = None,
    error_code: str,
    error_message: str,
    metadata: dict | None = None,
) -> FetchResult:
    return FetchResult(
        url=url,
        final_url=final_url,
        status_code=status_code,
        content_type=content_type,
        title=title,
        raw_text=None,
        error_code=error_code,
        error_message=error_message,
        fetch_method="httpx",
        metadata=metadata or {},
    )


def _http_error_code(status_code: int) -> str:
    if status_code == 403:
        return "http_403"
    if status_code == 404:
        return "http_404"
    return "http_error"


def _legacy_url_error(exc: URLSafetyError) -> str:
    if exc.code == "private_network_url":
        return "ssrf_blocked"
    return "invalid_url"
