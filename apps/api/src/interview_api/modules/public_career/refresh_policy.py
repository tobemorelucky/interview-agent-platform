"""Configurable, deterministic public-data refresh policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class RefreshPolicyConfig:
    stale_after: timedelta | None = None
    minimum_independent_content_count: int | None = None


@dataclass(frozen=True)
class RefreshPolicyInput:
    exists: bool
    observed_at: datetime | None = None
    independent_content_count: int = 0
    now: datetime | None = None


@dataclass(frozen=True)
class RefreshPolicyDecision:
    should_refresh: bool
    missing: bool
    stale: bool
    insufficient: bool


def evaluate_refresh_policy(
    value: RefreshPolicyInput,
    config: RefreshPolicyConfig,
) -> RefreshPolicyDecision:
    missing = not value.exists
    now = value.now or datetime.now(value.observed_at.tzinfo if value.observed_at else None)
    stale = bool(
        value.exists
        and config.stale_after is not None
        and (value.observed_at is None or now - value.observed_at >= config.stale_after)
    )
    insufficient = bool(
        value.exists
        and config.minimum_independent_content_count is not None
        and value.independent_content_count
        < config.minimum_independent_content_count
    )
    return RefreshPolicyDecision(
        should_refresh=missing or stale or insufficient,
        missing=missing,
        stale=stale,
        insufficient=insufficient,
    )
