from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from interview_api.modules.public_career.dedupe import group_independent_content
from interview_api.modules.public_career.refresh_policy import (
    RefreshPolicyConfig,
    RefreshPolicyInput,
    evaluate_refresh_policy,
)


@dataclass
class Snapshot:
    id: int
    source_id: int
    content_text: str | None
    normalized_content: str | None = None


def test_independent_content_grouping_has_exact_deterministic_metrics() -> None:
    result = group_independent_content(
        [
            Snapshot(1, 10, "Python   SQL"),
            Snapshot(2, 11, " PYTHON sql "),
            Snapshot(3, 12, "Java Spring"),
        ]
    )
    assert result.independent_content_count == 2
    assert result.occurrence_count == 3
    assert result.multi_source is True
    assert result.source_count == result.independent_content_count
    assert sorted(group.occurrence_count for group in result.groups) == [1, 2]


def test_refresh_policy_thresholds_are_caller_configured() -> None:
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    config = RefreshPolicyConfig(
        stale_after=timedelta(days=7),
        minimum_independent_content_count=3,
    )
    missing = evaluate_refresh_policy(RefreshPolicyInput(exists=False, now=now), config)
    stale_and_insufficient = evaluate_refresh_policy(
        RefreshPolicyInput(
            exists=True,
            observed_at=now - timedelta(days=8),
            independent_content_count=2,
            now=now,
        ),
        config,
    )
    fresh = evaluate_refresh_policy(
        RefreshPolicyInput(
            exists=True,
            observed_at=now - timedelta(days=1),
            independent_content_count=3,
            now=now,
        ),
        config,
    )
    assert missing.missing and missing.should_refresh
    assert stale_and_insufficient.stale and stale_and_insufficient.insufficient
    assert not fresh.should_refresh
