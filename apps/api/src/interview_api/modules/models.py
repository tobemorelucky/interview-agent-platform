"""Unified ORM model registry.

Import this module once before using Base.metadata (e.g. in Alembic env.py,
scripts, or any entrypoint that needs FK resolution across modules) to ensure
all model tables are registered.

To add a new model module, import it here.  Alembic and all scripts will
pick it up automatically.
"""

from interview_api.modules.users.models import User  # noqa: F401
from interview_api.modules.kb.models import KbDocument, KbChunk  # noqa: F401
from interview_api.modules.qa.models import ChatSession, ChatMessage  # noqa: F401
from interview_api.modules.resume.models import Resume, ResumeReport  # noqa: F401
from interview_api.modules.interview.models import (  # noqa: F401
    InterviewSession,
    InterviewMessage,
)
