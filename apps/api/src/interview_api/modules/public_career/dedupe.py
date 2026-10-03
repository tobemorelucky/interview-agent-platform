"""Deterministic independent-content grouping for public source snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

from interview_api.modules.public_career.fingerprints import (
    compute_content_fingerprint,
    normalize_text,
)


class SnapshotLike(Protocol):
    id: int
    source_id: int
    content_text: str | None
    normalized_content: str | None


@dataclass(frozen=True)
class IndependentContentGroup:
    fingerprint: str
    snapshot_ids: tuple[int, ...]
    source_ids: tuple[int, ...]
    occurrence_count: int


@dataclass(frozen=True)
class IndependentContentGrouping:
    groups: tuple[IndependentContentGroup, ...]
    independent_content_count: int
    occurrence_count: int
    multi_source: bool
    ignored_occurrence_count: int = 0

    @property
    def source_count(self) -> int:
        """Legacy deterministic alias; models must not generate this value."""
        return self.independent_content_count


def group_independent_content(
    snapshots: Iterable[SnapshotLike],
) -> IndependentContentGrouping:
    grouped: dict[str, list[SnapshotLike]] = {}
    occurrence_count = 0
    ignored = 0
    for snapshot in snapshots:
        occurrence_count += 1
        normalized = snapshot.normalized_content or normalize_text(
            snapshot.content_text or ""
        )
        if not normalized:
            ignored += 1
            continue
        fingerprint = compute_content_fingerprint(normalized)
        grouped.setdefault(fingerprint, []).append(snapshot)

    groups = tuple(
        IndependentContentGroup(
            fingerprint=fingerprint,
            snapshot_ids=tuple(sorted(item.id for item in items)),
            source_ids=tuple(sorted({item.source_id for item in items})),
            occurrence_count=len(items),
        )
        for fingerprint, items in sorted(grouped.items())
    )
    independent_content_count = len(groups)
    return IndependentContentGrouping(
        groups=groups,
        independent_content_count=independent_content_count,
        occurrence_count=occurrence_count,
        multi_source=independent_content_count >= 2,
        ignored_occurrence_count=ignored,
    )
