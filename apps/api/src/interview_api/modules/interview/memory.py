"""Interview memory compression manager."""

import logging
from pathlib import Path

from interview_api.core.config import settings

logger = logging.getLogger(__name__)

_PROMPT_DIR = (
    Path(__file__).parent.parent.parent.parent.parent / "prompt_templates"
)


def _load_prompt(name: str) -> str:
    return (_PROMPT_DIR / name).read_text(encoding="utf-8")


class InterviewMemoryManager:
    """Manages conversation memory compression for interview sessions."""

    @property
    def trigger_turns(self) -> int:
        return settings.interview_memory_compression_trigger_turns

    @property
    def recent_keep_count(self) -> int:
        return settings.interview_memory_recent_keep_count

    def should_compress(self, turn_count: int, last_compressed_turn: int) -> bool:
        return (turn_count - last_compressed_turn) >= self.trigger_turns

    def partition_messages(
        self,
        messages: list,
        last_compressed_turn: int,
        current_turn: int,
    ) -> tuple[list, list]:
        """Split messages into (to_compress, to_keep).

        to_compress: messages from last_compressed_turn up to (current_turn - recent_keep_count)
        to_keep: messages from (current_turn - recent_keep_count) onward
        """
        keep_start = current_turn - self.recent_keep_count
        to_compress = [
            m for m in messages if last_compressed_turn <= m.turn_index < keep_start
        ]
        to_keep = [m for m in messages if m.turn_index >= keep_start]
        return to_compress, to_keep

    def format_conversation(self, messages: list) -> str:
        """Format a list of messages into a readable conversation string."""
        lines: list[str] = []
        for m in messages:
            role_label = {
                "USER": "候选人",
                "ASSISTANT": "面试官",
                "SYSTEM": "系统",
            }.get(m.role, m.role)
            lines.append(f"[{role_label}]: {m.content}")
        return "\n\n".join(lines)

    async def compress(
        self,
        llm,
        to_compress: list,
        existing_summary: str | None,
    ) -> str:
        """Call LLM to generate a compressed memory summary."""
        conversation_text = self.format_conversation(to_compress)
        prompt = _load_prompt("interview_memory_compression_v1.md").format(
            existing_summary=existing_summary or "无",
            recent_conversation=conversation_text,
        )
        logger.info(
            "Memory compression: %s messages -> LLM call (%s prompt chars)",
            len(to_compress),
            len(prompt),
        )
        result = await llm.chat([{"role": "user", "content": prompt}])
        logger.info("Memory compression done: %s chars summary", len(result))
        return result.strip()
