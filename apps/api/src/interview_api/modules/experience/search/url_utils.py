"""URL normalization helpers for source item de-duplication."""

from hashlib import sha256
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "spm",
    "fbclid",
    "gclid",
    "share_source",
}


def normalize_url(url: str) -> str:
    text = (url or "").strip()
    if not text:
        return ""

    parsed = urlsplit(text)
    if not parsed.netloc:
        return ""

    query_items = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lower_key = key.lower()
        if lower_key.startswith("utm_") or lower_key in TRACKING_PARAMS:
            continue
        query_items.append((key, value))

    normalized_query = urlencode(query_items, doseq=True)
    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    return urlunsplit((scheme, netloc, path, normalized_query, ""))


def hash_url(url: str) -> str:
    return sha256(normalize_url(url).encode("utf-8")).hexdigest()
