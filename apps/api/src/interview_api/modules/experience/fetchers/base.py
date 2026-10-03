"""Compatibility exports for the shared public fetch contract."""

from interview_api.modules.public_career.tools.providers import (
    FetchProvider,
    FetchResult,
)

ContentFetcher = FetchProvider

__all__ = ["ContentFetcher", "FetchResult"]
