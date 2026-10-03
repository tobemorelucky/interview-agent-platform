"""Exact span catalogs: model IDs resolve to stored Fetch bodies, never snippets."""

from dataclasses import dataclass
from hashlib import sha256
from collections.abc import Iterable

from interview_api.modules.public_career.models import SourceSnapshot


class EvidenceValidationError(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceSpan:
    evidence_id: str
    snapshot_id: int
    source_id: int
    excerpt: str
    start: int
    end: int

    @property
    def location(self) -> dict:
        return {"start_char": self.start, "end_char": self.end, "unit": "unicode_codepoint"}


class EvidenceCatalog:
    def __init__(self, snapshots: Iterable[SourceSnapshot], *, max_chars: int = 24000,
                 span_chars: int = 600):
        if span_chars < 1 or max_chars < 1:
            raise ValueError("Evidence budget must be positive")
        self.spans: dict[str, EvidenceSpan] = {}
        snapshots = sorted(snapshots, key=lambda s: s.id)
        per_source_budget = max_chars // max(1, len(snapshots))
        used = 0
        for snapshot in snapshots:
            text = snapshot.content_text or ""
            for start in range(0, min(len(text), per_source_budget), span_chars):
                excerpt = text[start:start + min(span_chars, max_chars - used, per_source_budget - start)]
                if used >= max_chars:
                    break
                if not excerpt.strip():
                    continue
                end = start + len(excerpt)
                key = "ev_" + sha256(f"{snapshot.id}:{start}:{end}:{excerpt}".encode()).hexdigest()[:24]
                self.spans[key] = EvidenceSpan(key, snapshot.id, snapshot.source_id, excerpt, start, end)
                used += len(excerpt)

    def resolve(self, ids: list[str]) -> list[EvidenceSpan]:
        if not ids:
            raise EvidenceValidationError("Claim must cite at least one Evidence ID")
        result = []
        for key in sorted(set(ids)):
            if key not in self.spans:
                raise EvidenceValidationError(f"Unknown Evidence ID: {key}")
            result.append(self.spans[key])
        return result

    def model_input(self) -> list[dict]:
        return [{"evidence_id": span.evidence_id, "text": span.excerpt} for span in self.spans.values()]
