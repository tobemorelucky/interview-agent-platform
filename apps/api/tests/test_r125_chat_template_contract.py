"""R125: a running interview must not render per-question marks in the chat.

There is no browser test runner in this repo, so this pins the *template
contract* the Vue file must keep: the marking card, the dimension scores, the
reference answer and the interviewer hint are only reachable once the session is
finished. It is a guard against quietly re-exposing them, not a render test.
"""
from pathlib import Path

PAGE = Path(__file__).resolve().parents[2] / "web/src/pages/InterviewChatPage.vue"


def source():
    return PAGE.read_text(encoding="utf-8")


def test_live_turn_shows_an_acknowledgement_not_a_score():
    text = source()
    assert 'v-if="!interviewFinished"' in text, "the live evaluation branch was removed"
    ack = text.index("面试官已记下这段回答")
    first_score = text.index("得分 {{ item.score")
    # The score card sits after the live branch, i.e. inside the finished-only block.
    assert ack < first_score


def test_reference_answer_requires_a_finished_interview():
    lines = source().splitlines()
    index = next(i for i, line in enumerate(lines) if "展开参考答案" in line)
    # The label lives inside the toggle button; its opening tag is one line above.
    assert "interviewFinished" in lines[index - 1], lines[index - 1]
    view = next(line for line in lines if "<ReferenceAnswerView" in line)
    assert "interviewFinished" in view, view


def test_target_label_and_hint_are_not_shown_while_interviewing():
    text = source()
    for marker in ('displayLabel(item.dimension', 'item.interviewer_guidance', "item.question_kind === 'DEEP_DIVE'"):
        line = next((row for row in text.splitlines() if marker in row and "v-if" in row), None)
        assert line is not None, marker
        assert "interviewFinished" in line, line


def test_finished_state_is_derived_from_the_session_not_from_a_local_flag():
    text = source()
    assert "const interviewFinished = computed" in text
    assert "status === 'COMPLETED'" in text or 'status === "COMPLETED"' in text
