"""Pre-scoring, conservative answer relevance checks. No model call.

The goal is NOT to judge competence. It is to stop an obviously pasted,
non-answer from being scored or written into capability memory, and to let the
candidate correct it. Ambiguous answers stay VALID: their quality is judged by
the unchanged Evaluation 2.3.4 and the Main interviewer's follow-up decision.
"""
from dataclasses import dataclass
import re

from interview_api.modules.interview.end_intent import end_intent

CHANGE_INTENT = re.compile(
    r"^(?:能|可以|我们|咱们)?(?:换个?题|换一个?问题|跳过(?:这|本|当前)?题?|下一题吧|先换(?:个|一道)题|"
    r"暂停(?:一下|本题)?|这题先过了|先不答这题)", re.IGNORECASE)
DOCUMENT_MARKERS = (
    r"#{1,4}\s", r"第[一二三四五六七八九十\d]+章", r"研究决定", r"正式停止", r"不运行", r"不修改",
    r"会议纪要", r"实验结论", r"待办事项", r"TODO", r"截止日期", r"技术计划", r"实施方案",
)
PERSONAL_MARKER = re.compile(r"(?:^|[\s，。；\n])(?:我|我们|本人|我之前|我曾经|我负责)")
STOP_TERMS = {"这个", "那个", "一下", "大概", "可以", "什么", "怎么", "如何", "或者", "以及", "关于", "请讲", "说说"}


@dataclass(frozen=True)
class Relevance:
    kind: str  # USER_CONTROL | OFF_TOPIC_SUSPECTED | UNKNOWN | VALID
    reason: str
    detail: dict


def _terms(text):
    ascii_terms = re.findall(r"[a-zA-Z][a-zA-Z0-9+#.\-]{2,}", text or "")
    han_terms = re.findall(r"[\u4e00-\u9fff]{2,4}", text or "")
    return {t.casefold() for t in ascii_terms + han_terms if t.casefold() not in STOP_TERMS}


def classify(content, question, *, job_title="", skill_label=""):
    """Conservative pre-score classification.

    Only a *document-like* paste with no personal answer and no overlap with
    the question/role is sent back for confirmation. Short answers, jargon-free
    answers and partial answers are never treated as paste errors here.
    """
    text = (content or "").strip()
    if end_intent(text):
        return Relevance("USER_CONTROL", "END", {"matched": "end_intent"})
    if CHANGE_INTENT.search(text):
        return Relevance("USER_CONTROL", "CHANGE_TOPIC", {"matched": "change_intent"})

    unknown = re.search(r"^(?:这个|这题|这块)?(?:我)?(?:真的)?(?:不(?:知道|清楚|了解|会)|没(?:做过|接触过|研究过))", text)
    if unknown and len(text) < 160:
        return Relevance("UNKNOWN", "EXPLICIT_UNKNOWN", {"matched": unknown.group(0)})

    markers = [pattern for pattern in DOCUMENT_MARKERS if re.search(pattern, text, re.IGNORECASE)]
    expected = _terms(f"{question or ''} {job_title} {skill_label}")
    overlap = sorted(expected & _terms(text))
    personal = bool(PERSONAL_MARKER.search(text[:240]))
    if len(text) >= 240 and len(markers) >= 3 and not overlap and not personal:
        return Relevance("OFF_TOPIC_SUSPECTED", "DOCUMENT_PASTE",
            {"length": len(text), "document_markers": len(markers), "overlap": overlap,
             "personal_marker": personal})
    return Relevance("VALID", "NO_PRECHECK_OBJECTION",
        {"length": len(text), "document_markers": len(markers), "overlap": overlap})
