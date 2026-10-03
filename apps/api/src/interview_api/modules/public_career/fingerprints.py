"""Deterministic text normalization and fingerprint helpers.

These helpers intentionally use no model calls, embeddings, or fuzzy matching.
The same input text always produces the same SHA-256 fingerprint.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata


_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    """Return a stable, case-insensitive representation of human text."""

    normalized = unicodedata.normalize("NFKC", value or "")
    normalized = _WHITESPACE_RE.sub(" ", normalized).strip()
    return normalized.casefold()


def compute_content_fingerprint(content: str) -> str:
    """Compute the deterministic SHA-256 fingerprint for body content."""

    return hashlib.sha256(normalize_text(content).encode("utf-8")).hexdigest()


def compute_question_fingerprint(question: str) -> str:
    """Compute the deterministic SHA-256 fingerprint for a question."""

    return hashlib.sha256(normalize_text(question).encode("utf-8")).hexdigest()

