from __future__ import annotations

from interview_api.infrastructure.db.base import Base
import interview_api.modules.models  # noqa: F401
import pytest
from interview_api.modules.resume.parser import ResumeParser
from interview_api.modules.interview.prompt_builder import InterviewPromptBuilder
from interview_api.modules.experience.router import router as experience_router
from interview_api.modules.interview.router import router as interview_router
from interview_api.modules.resume.router import router as resume_router


def test_existing_resume_interview_experience_modules_still_register() -> None:
    assert "resumes" in Base.metadata.tables
    assert "interview_sessions" in Base.metadata.tables
    assert "interview_experiences" in Base.metadata.tables
    assert resume_router.routes
    assert interview_router.routes
    assert experience_router.routes


def test_resume_text_parsing_is_unchanged():
    content = "候选人简历\nPython 后端项目"
    assert ResumeParser().parse(content.encode(), "txt") == content
    with pytest.raises(ValueError):
        ResumeParser().parse(b"content", "doc")


def test_mock_interview_prompt_still_uses_resume_memory_and_knowledge():
    prompt = InterviewPromptBuilder().build_system_prompt(
        resume_raw_text="候选人有 FastAPI 项目经验", resume_structured=None,
        memory_summary="上一题讨论 Redis 缓存", recent_messages=[],
        retrieved_context=[{"content": "索引知识", "source": "knowledge"}],
    )
    assert "FastAPI" in prompt and "Redis" in prompt and "索引知识" in prompt
