from __future__ import annotations

import asyncio

from interview_api.modules.experience.agents.repository import (
    ExperienceAgentRepository,
)
from interview_api.modules.experience.models import ExperienceSourceItem
from interview_api.modules.public_career.fingerprints import (
    compute_content_fingerprint,
    compute_question_fingerprint,
)


class FakeSession:
    def __init__(self) -> None:
        self.items: list[object] = []

    def add(self, value: object) -> None:
        self.items.append(value)

    async def flush(self) -> None:
        return None


async def _exercise_write_path() -> None:
    session = FakeSession()
    repository = ExperienceAgentRepository(session)  # type: ignore[arg-type]
    source = ExperienceSourceItem(
        id=1,
        task_id=2,
        source_url="https://example.com/experience",
        normalized_url_hash="a" * 64,
        raw_text="  Interview body\nwith RAG  ",
    )
    experience = await repository.create_experience_draft(
        source=source,
        company="Example",
        position="AI Engineer",
        round_name="一面",
        summary="summary",
        extraction_confidence=0.9,
        extraction_output_json={},
    )
    assert experience.content_fingerprint == compute_content_fingerprint(
        source.raw_text or ""
    )

    questions = await repository.create_question_drafts(
        experience=experience,
        questions=[{"question": "Why use rerank?"}],
    )
    assert questions[0].question_fingerprint == compute_question_fingerprint(
        "Why use rerank?"
    )


def test_experience_and_question_write_paths_set_fingerprints() -> None:
    asyncio.run(_exercise_write_path())

