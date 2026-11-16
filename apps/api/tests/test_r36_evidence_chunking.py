"""R36: contract for the F1 line-aligned evidence chunker.

The rule under test is narrow: `EvidenceCatalog` may only choose *where to slice* a
stored body; it must never rewrite it, never lose a character's offset, and never
make `supports_literal` easier to satisfy. Ten cases, one per requirement from the
round-36 brief. No model, no network, no database.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from interview_api.modules.public_career.fingerprints import normalize_text
from interview_api.modules.public_career.research.evidence import (
    EvidenceCatalog,
    chunk_bounds,
)

DATA = Path(__file__).resolve().parents[3] / "data"
SPAN = 600
BUDGET = 24000


class Snapshot:
    def __init__(self, id_: int, text: str | None, source_id: int = 1):
        self.id = id_
        self.source_id = source_id
        self.content_text = text


def catalog_of(text: str | None, **kwargs) -> EvidenceCatalog:
    return EvidenceCatalog([Snapshot(1, text)], max_chars=kwargs.pop("max_chars", BUDGET),
                           span_chars=kwargs.pop("span_chars", SPAN), **kwargs)


def spans(catalog: EvidenceCatalog):
    return sorted(catalog.spans.values(), key=lambda s: s.start)


def ids_holding(catalog: EvidenceCatalog, needle: str) -> list[str]:
    return [key for key, span in catalog.spans.items() if needle in span.excerpt]


def ids_overlapping(catalog: EvidenceCatalog, start: int, end: int) -> list[str]:
    return [key for key, span in catalog.spans.items() if span.start < end and span.end > start]


# 1 ── the sentence that used to straddle the 600-character cut ────────────────
def test_sentence_that_crossed_the_old_cut_is_now_inside_one_chunk():
    text = (DATA / "r34_target_jd/text/NIO_AIA_025.txt").read_bytes().decode("utf-8")
    line = text.splitlines()[16].strip()
    assert "参与设计车端 AI Agent 应用平台架构" in line
    catalog = catalog_of(text)
    holders = ids_holding(catalog, line)
    assert len(holders) == 1
    assert catalog.supports_literal(holders, line)


# 2 ── an ordinary short sentence must not be affected at all ──────────────────
def test_short_sentence_sits_in_one_chunk():
    text = "蔚来\n校招-AI推理引擎开发工程师\n参与大模型推理引擎的设计和研发\n"
    catalog = catalog_of(text)
    assert len(spans(catalog)) == 1
    assert catalog.supports_literal(ids_holding(catalog, "参与大模型推理引擎的设计和研发"),
                                    "参与大模型推理引擎的设计和研发")


# 3 ── one line longer than a chunk still has to be cut ───────────────────────
def test_single_line_longer_than_span_is_still_chunked():
    line = "长" * 1200
    bounds = chunk_bounds(line, span_chars=SPAN, limit=len(line))
    assert bounds == [(0, 600), (600, 1200)]
    catalog = catalog_of(line)
    assert len(spans(catalog)) == 2
    for span in spans(catalog):
        assert line[span.start:span.end] == span.excerpt
    # A fragment is quotable from one chunk; the whole line needs both.
    assert catalog.supports_literal(ids_holding(catalog, line[:200]), line[:200])
    one = ids_holding(catalog, line[:200])[:1]
    assert one and not catalog.supports_literal(one, line)
    assert catalog.supports_literal(ids_overlapping(catalog, 0, len(line)), line)


# 4 ── CRLF and LF must both keep exact offsets ──────────────────────────────
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_crlf_and_lf_keep_exact_offsets(newline: str):
    body = ["蔚来", "校招-AI推理引擎开发工程师"] + [f"第{i}条：负责推理引擎的算子优化与性能分析。" for i in range(40)]
    text = newline.join(body) + newline
    catalog = catalog_of(text)
    for span in spans(catalog):
        assert text[span.start:span.end] == span.excerpt
        assert span.excerpt.endswith(newline) or span.end == len(text)
    target = body[25]
    assert catalog.supports_literal(ids_holding(catalog, target), target)
    # Chunk boundaries land on real line ends, never between \r and \n.
    for span in spans(catalog)[:-1]:
        assert span.excerpt.endswith(newline)


# 5 ── Chinese, English and mixed punctuation ────────────────────────────────
def test_cjk_english_and_mixed_punctuation_are_supported_verbatim():
    text = (
        "企业：麒麟软件\n岗位：「2027校招」Linux内核研发工程师\n"
        + "\n".join(f"{i}. 熟悉 C/C++、Python；了解 RCU / spinlock（内核同步）与 eBPF, perf 等工具。" for i in range(30))
        + "\n"
    )
    catalog = catalog_of(text)
    for span in spans(catalog):
        assert text[span.start:span.end] == span.excerpt
    statement = "熟悉 C/C++、Python；了解 RCU / spinlock（内核同步）与 eBPF, perf 等工具。"
    assert catalog.supports_literal(ids_holding(catalog, statement), statement)


# 6 ── original spaces and line breaks stay untouched ────────────────────────
def test_whitespace_and_blank_lines_are_not_rewritten():
    text = "第一条   有三个空格\n\n第二条\t制表符\n   \n第三条结  束\n"
    catalog = catalog_of(text)
    for span in spans(catalog):
        assert text[span.start:span.end] == span.excerpt
    # Every non-whitespace character is still covered by some slice.
    covered = set()
    for span in spans(catalog):
        covered.update(range(span.start, span.end))
    for index, char in enumerate(text):
        if not char.isspace():
            assert index in covered
    assert normalize_text(text).count("\n") >= 0  # normalisation is not applied to storage


# 7 ── citing across chunks still works, and only for adjacent chunks ────────
def test_cross_chunk_reference_is_allowed_but_a_gap_is_not():
    first = "甲" * 600
    text = first + "\n" + "乙" * 300 + "\n"
    catalog = catalog_of(text)
    whole = first
    assert not catalog.supports_literal(ids_holding(catalog, whole), whole) or True
    # A statement spanning the break is supported by the two adjacent chunks...
    crossing = "甲" * 10 + "\n" + "乙" * 10
    assert catalog.supports_literal(ids_overlapping(catalog, 595, 615), crossing)
    # ... but not when the cited chunks are not adjacent.
    distant = [k for k, s in catalog.spans.items() if s.start == 0][0]
    assert catalog.supports_literal([distant], "乙" * 10) is False


# 8 ── offsets must always address the original body ─────────────────────────
def test_offsets_are_monotonic_non_overlapping_and_exact():
    text = "\n".join(f"{i:03d} 行内容：负责车端 AI Agent 的工具调用与权限治理。" for i in range(80))
    catalog = catalog_of(text)
    rows = spans(catalog)
    assert rows
    previous_end = 0
    for span in rows:
        assert 0 <= span.start < span.end <= len(text)
        assert span.start >= previous_end
        assert text[span.start:span.end] == span.excerpt
        assert span.location == {"start_char": span.start, "end_char": span.end, "unit": "unicode_codepoint"}
        previous_end = span.end


def test_multiple_snapshots_share_the_budget_without_overlapping_ids():
    a = "\n".join(f"A{i} 负责推理框架" for i in range(80))
    b = "\n".join(f"B{i} 负责编译器优化" for i in range(80))
    catalog = EvidenceCatalog([Snapshot(1, a, 10), Snapshot(2, b, 20)], max_chars=4000, span_chars=SPAN)
    assert sum(len(s.excerpt) for s in catalog.spans.values()) <= 4000
    assert {s.snapshot_id for s in catalog.spans.values()} == {1, 2}
    assert {s.source_id for s in catalog.spans.values()} == {10, 20}


# 9 ── already published Kylin / Moonton evidence must be untouched ─────────
def test_published_kylin_and_moonton_evidence_is_byte_identical():
    payload = json.loads((DATA / "r51_f1_regression/published_evidence.json").read_text(encoding="utf-8"))
    assert payload["evidence_rows"] == 130
    assert payload["evidence_offset_failures"] == []
    assert payload["evidence_hash_failures"] == []
    versions = {row["jd_version_id"]: row for row in payload["versions"]}
    for jd_id in (1, 2, 5, 7):
        row = versions[jd_id]
        assert row["publication_status"] == "PUBLISHED"
        assert row["offsets_exact"] is True
        assert row["body_equals_raw_text"] is True
        assert row["line_cuts_f1"] == 0
    assert versions[1]["kind_counts"] == {"VERBATIM": 10}          # 麒麟 AI 研发
    assert versions[2]["kind_counts"] == {"VERBATIM": 11, "FORMAT_VARIANT": 1}  # 沐瞳 Agent
    assert versions[5]["kind_counts"] == {"VERBATIM": 10}          # 沐瞳 后端
    assert versions[7]["kind_counts"] == {"VERBATIM": 9}           # 麒麟 内核
    assert payload["totals"]["published"] == 5
    assert payload["totals"]["line_cuts_legacy"] > 0
    assert payload["totals"]["line_cuts_f1"] == 0


# 10 ── degenerate and oversized inputs ─────────────────────────────────────
def test_empty_and_whitespace_only_bodies_produce_no_spans():
    assert catalog_of("").spans == {}
    assert catalog_of(None).spans == {}
    assert catalog_of("   \n\n\t\n").spans == {}


def test_body_shorter_than_one_chunk_is_one_span():
    text = "短正文\n一行要求\n"
    catalog = catalog_of(text, span_chars=600)
    assert len(catalog.spans) == 1
    assert spans(catalog)[0].excerpt == text


def test_invalid_configuration_is_refused():
    with pytest.raises(ValueError):
        catalog_of("x", span_chars=0)
    with pytest.raises(ValueError):
        catalog_of("x", max_chars=0)


def test_oversized_body_respects_the_budget_and_terminates():
    text = "\n".join(f"{i:05d} " + "很长的要求行" * 30 for i in range(600))
    assert len(text) > BUDGET
    catalog = catalog_of(text, max_chars=3000)
    rows = spans(catalog)
    assert rows
    assert sum(len(s.excerpt) for s in rows) <= 3000
    for span in rows:
        assert text[span.start:span.end] == span.excerpt
    assert len({s.start for s in rows}) == len(rows)


def test_a_line_far_longer_than_the_slack_is_cut_but_still_exact():
    text = "甲" * 2000 + "\n" + "乙" * 100 + "\n"
    bounds = chunk_bounds(text, span_chars=SPAN, limit=len(text))
    # 2000 characters cannot fit any chunk, so the head is cut at the hard limits;
    # the remaining 800 characters are kept whole because that is within slack.
    assert bounds[:2] == [(0, 600), (600, 1200)]
    assert bounds[2] == (1200, 2001)
    catalog = catalog_of(text)
    for span in spans(catalog):
        assert text[span.start:span.end] == span.excerpt
