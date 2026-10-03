"""URL normalization helpers for source item de-duplication."""

from hashlib import sha256
from interview_api.core.url_safety import URLSafetyError, canonicalize_public_url


def normalize_url(url: str) -> str:
    try:
        return canonicalize_public_url(url)
    except URLSafetyError:
        return ""


def hash_url(url: str) -> str:
    return sha256(normalize_url(url).encode("utf-8")).hexdigest()
