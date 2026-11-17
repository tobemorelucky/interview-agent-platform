"""A user asking to stop is a request, never a technical answer.

A real interview ends when the candidate says so. Before this module the only
way out of a session was the Controller's budget, so "能提前结束面试吗" was
persisted as an answer, scored 1/5 on every dimension, and followed by a fresh
technical question — the transcript then read as if the candidate had failed a
question they never attempted.

The detector is deliberately narrow and deterministic:
  * it only fires on wording that asks to stop the interview as a whole;
  * a topic-scoped complaint ("不想继续这个话题") is a topic switch, not an exit;
  * a short "不知道 / 这题不会" is a normal weak answer, never an exit;
  * length is bounded, so a long answer that merely mentions 结束 is not matched.

No model call, no fuzzy matching, and no silent behaviour change: callers decide
what to do with the signal.
"""
import re

# Wording that asks to stop the whole interview.
END_REQUEST = re.compile(
    r"结束(?:一下)?(?:这场|本次|这个)?面试"
    r"|面试(?:就)?(?:到|先)?结束"
    r"|提前结束"
    r"|(?:能|可以|能否|能不能)(?:提前)?(?:结束|停止)(?:吗|么|嘛)"
    r"|(?:想|要|希望)(?:提前)?结束"
    r"|(?:就)?(?:先)?(?:到此为止|到这里吧|到这儿吧)"
    r"|(?:我)?不想(?:继续|面)(?:了|啦)?"
    r"|不(?:想)?继续(?:了|啦)"
    r"|不面了|停止面试|结束吧|可以结束了"
)
# "this topic / this question" scopes the complaint to one turn.
TOPIC_SCOPED = re.compile(r"这道题|这题|这个题|这一题|这个话题|这个问题|换个话题|换一题|换题|下一个话题")
# An explicit statement of not knowing is an answer, not an exit request.
NOT_KNOWING = re.compile(r"^(?:我)?(?:不知道|不会|不清楚|没做过|没接触过|不懂|不了解)|这题不会|不会做")
MAX_CHARS = 40


def normalise(text):
    return re.sub(r"\s+", "", (text or "").strip())


def end_intent(text):
    """True only for a clear request to end the whole interview."""
    value = normalise(text)
    if not value or len(value) > MAX_CHARS:
        return False
    if not END_REQUEST.search(value):
        return False
    if TOPIC_SCOPED.search(value) and "面试" not in value:
        # Wants a different topic, not the end of the interview.
        return False
    if NOT_KNOWING.search(value) and "面试" not in value:
        return False
    return True


def end_intent_detail(text):
    """Auditable record of the decision; never the answer text itself."""
    value = normalise(text)
    return dict(detector="interview_end_intent@1.0.0", chars=len(value),
                matched=bool(END_REQUEST.search(value)),
                topic_scoped=bool(TOPIC_SCOPED.search(value)),
                not_knowing=bool(NOT_KNOWING.search(value)))
