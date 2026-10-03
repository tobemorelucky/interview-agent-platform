"""Compatibility exports for the shared public search contract."""

from interview_api.modules.public_career.tools.providers import (
    SearchProvider,
    SearchResult,
)

__all__ = ["SearchProvider", "SearchResult"]
