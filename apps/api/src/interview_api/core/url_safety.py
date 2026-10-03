"""Canonical URL and SSRF protection for public HTTP fetches."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from interview_api.core.errors import ValidationAppError


MAX_URL_LENGTH = 2048
TRACKING_QUERY_NAMES = {"fbclid", "gclid", "msclkid", "spm", "share_source"}
SENSITIVE_QUERY_NAMES = {
    "access_token", "api_key", "apikey", "authorization", "key",
    "password", "secret", "signature", "token",
}
Resolver = Callable[[str, int], Awaitable[list[str]]]


class URLSafetyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def canonicalize_public_url(raw_url: str) -> str:
    if not raw_url or len(raw_url) > MAX_URL_LENGTH:
        raise URLSafetyError("invalid_url", "Invalid URL length")
    try:
        parsed = urlsplit(raw_url.strip())
    except ValueError as exc:
        raise URLSafetyError("invalid_url", "URL could not be parsed") from exc
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise URLSafetyError(
            "unsupported_url_scheme", "Only HTTP and HTTPS URLs are allowed"
        )
    if parsed.username or parsed.password:
        raise URLSafetyError(
            "url_credentials_forbidden", "Credentials in URLs are not allowed"
        )
    host = parsed.hostname
    if not host:
        raise URLSafetyError("invalid_url", "URL host is required")
    try:
        host = host.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise URLSafetyError("invalid_url", "URL hostname is invalid") from exc
    _reject_literal_or_local_host(host)
    try:
        port = parsed.port
    except ValueError as exc:
        raise URLSafetyError("invalid_url_port", "URL port is invalid") from exc
    if port is not None and not 1 <= port <= 65535:
        raise URLSafetyError("invalid_url_port", "URL port is invalid")
    default_port = (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    )
    host_for_netloc = f"[{host}]" if ":" in host else host
    netloc = host_for_netloc if port is None or default_port else f"{host_for_netloc}:{port}"
    path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query_items: list[tuple[str, str]] = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered in SENSITIVE_QUERY_NAMES:
            raise URLSafetyError(
                "sensitive_url_query_forbidden",
                "Sensitive URL query parameters are not allowed",
            )
        if lowered.startswith("utm_") or lowered in TRACKING_QUERY_NAMES:
            continue
        query_items.append((key, value))
    return urlunsplit(
        (scheme, netloc, path, urlencode(sorted(query_items), doseq=True), "")
    )


class URLSafetyPolicy:
    def __init__(self, resolver: Resolver | None = None) -> None:
        self._resolver = resolver or self._resolve

    async def validate(self, raw_url: str) -> str:
        canonical_url = canonicalize_public_url(raw_url)
        parsed = urlsplit(canonical_url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = await self._resolver(parsed.hostname or "", port)
        if not addresses:
            raise URLSafetyError(
                "dns_resolution_failed", "URL hostname did not resolve"
            )
        for value in addresses:
            try:
                address = ipaddress.ip_address(value)
            except ValueError as exc:
                raise URLSafetyError(
                    "dns_resolution_failed", "URL hostname returned an invalid address"
                ) from exc
            _ensure_public_ip(address)
        return canonical_url

    @staticmethod
    async def _resolve(host: str, port: int) -> list[str]:
        loop = asyncio.get_running_loop()
        try:
            records = await loop.getaddrinfo(
                host,
                port,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as exc:
            raise URLSafetyError(
                "dns_resolution_failed", "URL hostname did not resolve"
            ) from exc
        return sorted({record[4][0] for record in records})


def validate_public_http_url(url: str) -> str:
    """Backward-compatible synchronous validation for non-async callers."""
    try:
        canonical_url = canonicalize_public_url(url)
        parsed = urlsplit(canonical_url)
        for resolved in _resolve_host_sync(parsed.hostname or ""):
            _ensure_public_ip(resolved)
        return canonical_url
    except URLSafetyError as exc:
        raise ValidationAppError(exc.message) from exc


def _resolve_host_sync(host: str) -> list[ipaddress._BaseAddress]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise URLSafetyError(
            "dns_resolution_failed", "URL hostname did not resolve"
        ) from exc
    addresses: list[ipaddress._BaseAddress] = []
    for info in infos:
        try:
            addresses.append(ipaddress.ip_address(info[4][0]))
        except (ValueError, IndexError):
            continue
    if not addresses:
        raise URLSafetyError("dns_resolution_failed", "URL hostname did not resolve")
    return addresses


def _reject_literal_or_local_host(host: str) -> None:
    if host == "localhost" or host.endswith(".localhost"):
        raise URLSafetyError("private_network_url", "Localhost URLs are not allowed")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    _ensure_public_ip(address)


def _ensure_public_ip(address: ipaddress._BaseAddress) -> None:
    if not address.is_global:
        raise URLSafetyError(
            "private_network_url",
            "Private, local, reserved, or otherwise non-public addresses are not allowed",
        )
