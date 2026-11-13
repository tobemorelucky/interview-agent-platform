"""Place full-time work and internships by the resume's own structure.

The first-stage extraction reports entries in fixed buckets, but a bucket is not
an employment kind. This resolver maps every entry back to the resume lines that
actually mention it and reads the governing heading. Company names, lengths of
service, and wording such as "参与项目" are never treated as kind evidence: only
the section the text sits in is.

Entries whose anchors point at two different employment sections, at a project
section, or at no section at all are not credited to either kind. They are kept
verbatim in `unclassified_experience` so nothing is lost and nothing is invented.
"""
from copy import deepcopy

from interview_api.modules.public_career.fingerprints import normalize_text
from interview_api.modules.resume.sections import heading_section, stable_sections

POLICY = "EMPLOYMENT_KIND_FROM_SECTION_V1"
WORK, INTERNSHIP, CONFLICT, UNRESOLVED = "WORK", "INTERNSHIP", "CONFLICT", "UNRESOLVED"
REPORTED_BUCKETS = ("work_experience", "internships", "unclassified_experience")
UNCLASSIFIED = "unclassified_experience"
AUDIT_KEY = "employment_kind"
# Identity anchors also match short company names ("华为"); descriptive anchors
# must be long enough to be a real quote rather than a stray word.
IDENTITY_ANCHORS = (("company", 3, 2), ("role", 2, 2))
CONTENT_ANCHORS = (("duration", 2, 4), ("responsibilities", 2, 4), ("tech_stack", 1, 4))
EMPLOYMENT_SECTIONS = ("work", "internship")


def _lines(raw_text: str) -> list[tuple[str, str, bool]]:
    return [(match.group(), section, heading_section(match.group()) is not None)
            for match, section in stable_sections(raw_text)]


def _anchors(entry: dict) -> list[tuple[str, int, int]]:
    anchors = []
    for name, weight, minimum in IDENTITY_ANCHORS + CONTENT_ANCHORS:
        value = entry.get(name)
        if isinstance(value, str):
            anchors.append((value, weight, minimum))
        elif isinstance(value, list):
            anchors.extend((item, weight, minimum) for item in value if isinstance(item, str))
    return anchors


def classify_entry(entry: dict, lines) -> tuple[str, dict]:
    """Return the employment kind decided by the text, never by the bucket."""
    votes = {WORK: 0, INTERNSHIP: 0}
    sections = set()
    matched = 0
    longest = 0
    for value, weight, minimum in _anchors(entry):
        needle = normalize_text(value)
        if len(needle) < minimum:
            continue
        found = {section for text, section, is_heading in lines
                 if not is_heading and needle in normalize_text(text)}
        employment = found & set(EMPLOYMENT_SECTIONS)
        # An anchor that appears in both sections cannot discriminate between
        # them; it is dropped instead of voting twice.
        if len(employment) != 1:
            continue
        kind = WORK if "work" in employment else INTERNSHIP
        votes[kind] += weight
        sections.add(kind.lower())
        matched += 1
        longest = max(longest, len(needle))
    if votes[WORK] > votes[INTERNSHIP]:
        kind = WORK
    elif votes[INTERNSHIP] > votes[WORK]:
        kind = INTERNSHIP
    elif votes[WORK]:
        kind = CONFLICT
    else:
        kind = UNRESOLVED
    return kind, dict(matched_anchors=matched, longest_anchor_chars=longest, sections=sorted(sections))


def reconcile_employment(structured: dict, raw_text: str) -> tuple[dict, dict]:
    """Return a new report whose buckets follow the resume, plus an audit block."""
    report = deepcopy(structured) if isinstance(structured, dict) else {}
    entries = [(bucket, index, item) for bucket in REPORTED_BUCKETS
               for index, item in enumerate(report.get(bucket) or []) if isinstance(item, dict)]
    if not entries:
        return report, dict(policy=POLICY, entries=[], counts={}, skipped="NO_ENTRIES")
    lines = _lines(raw_text or "")
    if not lines:
        return report, dict(policy=POLICY, entries=[], counts={}, skipped="NO_RAW_TEXT")
    placed = {WORK: [], INTERNSHIP: [], CONFLICT: [], UNRESOLVED: []}
    audit = []
    for bucket, index, item in entries:
        kind, detail = classify_entry(item, lines)
        placed[kind].append(item)
        audit.append(dict(reported_as=bucket, index=index, resolved=kind, **detail))
    report["work_experience"] = placed[WORK]
    report["internships"] = placed[INTERNSHIP]
    unresolved = placed[CONFLICT] + placed[UNRESOLVED]
    if unresolved:
        report[UNCLASSIFIED] = unresolved
    else:
        report.pop(UNCLASSIFIED, None)
    block = dict(policy=POLICY,
                 counts={name: len(items) for name, items in placed.items()},
                 entries=audit,
                 note="按简历原文小节标题归类；无法确定的一律不归入工作或实习。")
    report[AUDIT_KEY] = block
    return report, block
