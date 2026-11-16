"""R35→R36: the chunk-boundary false negative, before and after F1.

Round 35 recorded, from the real failed run, that a verbatim page line could not be
supported by the evidence chunk it starts in. Round 36 changed the chunker to end
its slices on line breaks. This file now asserts both halves of that story:

  * the historical probe artefact still shows the old failure (frozen evidence);
  * the shipped `EvidenceCatalog` keeps the same line whole and supports it.

No model, no network, no database.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from interview_api.modules.public_career.research.evidence import EvidenceCatalog

DATA = Path(__file__).resolve().parents[3] / "data"
NIO = DATA / "r34_target_jd" / "text" / "NIO_AIA_025.txt"
PROBE = DATA / "r44_nio_diagnosis" / "validator_probe.json"
BEFORE_AFTER = DATA / "r48_span_fix" / "span_fix.json"


class _Snapshot:
    def __init__(self, id_: int, source_id: int, text: str):
        self.id, self.source_id, self.content_text = id_, source_id, text


@pytest.fixture(scope="module")
def page_text() -> str:
    return NIO.read_bytes().decode("utf-8")


@pytest.fixture(scope="module")
def catalog(page_text):
    return EvidenceCatalog([_Snapshot(2, 1, page_text)], max_chars=24000, span_chars=600)


def _span_containing(catalog, needle: str) -> list[str]:
    return [key for key, span in catalog.spans.items() if needle in span.excerpt]


def test_the_projected_page_is_the_bytes_the_failed_run_used(page_text):
    probe = json.loads(PROBE.read_text(encoding="utf-8"))
    assert sha256(NIO.read_bytes()).hexdigest() == probe["snapshot_sha256"]
    assert probe["snapshot_sha256"] == probe["manifest_sha256"]


def test_the_round35_probe_still_documents_the_old_false_negative():
    """Frozen artefact: with the pre-F1 chunker this line failed on one span."""
    probe = json.loads(PROBE.read_text(encoding="utf-8"))
    rows = {row["line"]: row for row in probe["verbatim_lines"]}
    assert rows[17]["passes_single_span"] is False
    assert rows[17]["passes_with_all_overlapping_spans"] is True
    assert rows[17]["split_at_char"] == [600]
    assert all(row["passes_single_span"] for number, row in rows.items() if number != 17)


def test_the_shipped_chunker_keeps_that_line_inside_one_span(catalog, page_text):
    line = page_text.splitlines()[16].strip()
    ids = _span_containing(catalog, line)
    assert ids, "the F1 chunk boundary must not split this page line"
    assert catalog.supports_literal(ids, line) is True


def test_every_page_line_is_supported_by_a_single_chunk(catalog, page_text):
    for line in page_text.splitlines():
        needle = line.strip()
        if len(needle) < 6:
            continue
        ids = _span_containing(catalog, needle)
        assert ids, needle
        assert catalog.supports_literal(ids, needle), needle


def test_f1_removed_all_measured_line_cuts_but_is_not_a_universal_guarantee():
    payload = json.loads(BEFORE_AFTER.read_text(encoding="utf-8"))
    assert payload["f1_applied"] is True
    totals = payload["totals"]
    assert totals["cut_before"] == 11  # measured in this sample, not a universal count
    assert totals["cut_after"] == 0
    assert totals["lines_before"] == 206
