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


def chunk_bounds(text: str, *, span_chars: int, limit: int) -> list[tuple[int, int]]:
    """Evidence slice points that follow the document's own line breaks.

    A fixed 600-character cut can bisect a page line, which makes a perfectly
    verbatim sentence impossible to support with the chunk it starts in. The cut
    is therefore nudged to the nearest line break: back a little when that loses
    at most half a chunk, forward a little when the next break is within half a
    chunk of slack. Nothing else changes — the slice still comes straight from
    the stored body, so offsets stay exact and no text is rewritten.

    A single line longer than `span_chars + span_chars // 2` cannot be kept whole;
    it is cut at the hard boundary and can only be cited by adjacent chunks.
    """
    if span_chars < 1:
        raise ValueError("Evidence span must be positive")
    bounds: list[tuple[int, int]] = []
    stop = max(0, min(limit, len(text)))
    slack = span_chars // 2
    position = 0
    while position < stop:
        hard = min(position + span_chars, stop)
        end = hard
        if hard < len(text):
            before = text.rfind("\n", position, hard)
            after = text.find("\n", hard)
            if before > position and hard - (before + 1) <= slack:
                end = before + 1
            elif after != -1 and (after + 1 - position) <= span_chars + slack:
                end = after + 1
        end = min(end, stop)
        if end <= position:  # defensive: a pathological boundary must still advance
            end = hard
        bounds.append((position, end))
        position = end
    return bounds


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
            for start, end in chunk_bounds(text, span_chars=span_chars,
                                           limit=min(len(text), per_source_budget)):
                if used >= max_chars:
                    break
                # The total budget still wins over line alignment; it only bites
                # on documents larger than `max_chars`.
                end = min(end, start + (max_chars - used))
                excerpt = text[start:end]
                if not excerpt.strip():
                    continue
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

    def supports_literal(self, ids: list[str], statement: str) -> bool:
        """Allow a literal to cross adjacent cited spans, never a gap/source.

        A fixed-size Evidence chunk boundary must not turn valid original text
        into an unsupported fact. This is not paraphrase/semantic matching.
        """
        from interview_api.modules.public_career.fingerprints import normalize_text
        groups = []
        previous = None
        for span in sorted(self.resolve(ids), key=lambda s: (s.snapshot_id, s.start)):
            if previous and previous.snapshot_id == span.snapshot_id and previous.end == span.start:
                groups[-1] += span.excerpt
            else:
                groups.append(span.excerpt)
            previous = span
        value = normalize_text(statement)
        return bool(value) and any(value in normalize_text(group) for group in groups)

    def validate_identity(self, ids, *names):
        """Exact identity rule shared by early selection and final publication."""
        from interview_api.modules.public_career.fingerprints import normalize_text
        groups, previous = [], None
        for span in sorted(self.resolve(ids), key=lambda s: (s.snapshot_id, s.start)):
            if previous and previous.snapshot_id == span.snapshot_id and previous.end == span.start:
                groups[-1] += span.excerpt
            else:
                groups.append(span.excerpt)
            previous = span
        texts = [''.join(normalize_text(group).split()) for group in groups]
        for name in names:
            value = ''.join(normalize_text(name).split()) if name else ''
            if value and not any(value in text for text in texts):
                elsewhere = any(value in ''.join(normalize_text(s.excerpt).split()) for s in self.spans.values())
                raise EvidenceValidationError(
                    f'Company/job identity absent from exact cited evidence: name={name!r}; '
                    f'evidence_ids={sorted(set(ids))}; literal_elsewhere={elsewhere}')
