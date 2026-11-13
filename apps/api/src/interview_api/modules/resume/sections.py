"""Resume section detection: one definition, used by evidence rows and by the
employment-kind resolver.

Heading aliases match a whole line only (after stripping decoration), so a
sentence that merely mentions "工作经历" never opens a section. A heading line
opens the section for the lines that follow it.

A short title-like line we do not recognise ("其他经历", "校园活动") still ends
the previous section: without that, everything under an unknown heading would
inherit the section above it and a school activity could be read as work.
"""
import re

from interview_api.modules.public_career.fingerprints import normalize_text

HEADINGS = {
    "projects": ("项目经历", "项目经验", "项目", "projects", "project experience"),
    "work": ("工作经历", "工作经验", "work experience", "employment"),
    "internship": ("实习经历", "实习经验", "internships", "internship experience"),
    "education": ("教育经历", "教育背景", "教育", "education"),
    "skills": ("专业技能", "技术技能", "技能", "skills", "technical skills"),
}
# Title-like endings that mark a section we deliberately do not classify.
OTHER_SECTION_SUFFIXES = ("经历", "经验", "背景", "情况", "证书", "荣誉", "活动", "实践",
                          "培训", "评价", "兴趣")
MAX_OTHER_SECTION_CHARS = 20

LINE = re.compile(r"[^\r\n]+")
SENTENCE_PUNCTUATION = re.compile(r"[。；;，,！!？?：:]")


def heading_section(line: str) -> str | None:
    """Section name when the whole line is a heading, else None."""
    heading = normalize_text(line).strip(" #:：-•")
    for name, aliases in HEADINGS.items():
        if heading in aliases:
            return name
    return None


def other_section(line: str) -> bool:
    """A short title line that is not a known heading: it closes the section."""
    text = normalize_text(line).strip(" #:：-•")
    return (bool(text) and len(text) <= MAX_OTHER_SECTION_CHARS
            and not SENTENCE_PUNCTUATION.search(text)
            and text.endswith(OTHER_SECTION_SUFFIXES)
            and heading_section(line) is None)


def line_headings(raw_text: str):
    """Yield (regex match, heading name or None) in document order."""
    for match in LINE.finditer(raw_text or ""):
        yield match, heading_section(match.group())


def _walk(raw_text: str, *, toc_guard: bool):
    lines = list(line_headings(raw_text))
    section = "general"
    for index, (match, heading) in enumerate(lines):
        if heading:
            # A heading that is immediately followed by another heading is part
            # of a contents block and cannot open a section on its own.
            if toc_guard and index + 1 < len(lines) and lines[index + 1][1] is not None:
                yield match, section
                continue
            section = heading
        elif other_section(match.group()):
            section = "general"
        yield match, section


def section_lines(raw_text: str):
    """Yield (regex match, section) for evidence rows.

    Evidence keys are derived from the section name, so this walk stays the
    definition every stored row was produced with.
    """
    yield from _walk(raw_text, toc_guard=False)


def stable_sections(raw_text: str):
    """Same walk plus a table-of-contents guard for structural decisions."""
    yield from _walk(raw_text, toc_guard=True)
