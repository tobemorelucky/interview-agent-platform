"""URL validation helpers for SSRF protection."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from interview_api.core.errors import ValidationAppError

MAX_URL_LENGTH = 2048

BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def validate_public_http_url(url: str) -> str:
    if not url or len(url) > MAX_URL_LENGTH:
        raise ValidationAppError("Invalid URL length")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValidationAppError("Only http/https URLs are allowed")
    if not parsed.hostname:
        raise ValidationAppError("URL host is required")

    host = parsed.hostname.strip().lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        raise ValidationAppError("Localhost URLs are not allowed")

    try:
        ip = ipaddress.ip_address(host)
        _ensure_public_ip(ip)
    except ValueError:
        for resolved in _resolve_host(host):
            _ensure_public_ip(resolved)
    return url


def _resolve_host(host: str) -> list[ipaddress._BaseAddress]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValidationAppError("URL host cannot be resolved") from exc
    ips: list[ipaddress._BaseAddress] = []
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        try:
            ips.append(ipaddress.ip_address(sockaddr[0]))
        except ValueError:
            continue
    if not ips:
        raise ValidationAppError("URL host cannot be resolved")
    return ips


def _ensure_public_ip(ip: ipaddress._BaseAddress) -> None:
    if ip.is_multicast or ip.is_unspecified:
        raise ValidationAppError("Private or local network URLs are not allowed")
    if any(ip in network for network in BLOCKED_NETWORKS):
        raise ValidationAppError("Private or local network URLs are not allowed")
