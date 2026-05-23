"""Interview prompt builder — assembles system/user prompts for the interviewer LLM."""

import json
from pathlib import Path

from interview_api.core.config import settings

_PROMPT_DIR = (
    Path(__file__).parent.parent.parent.parent.parent / "prompt_templates"
)


def _load_prompt(name: str) -> str:
    return (_PROMPT_DIR / name).read_text(encoding="utf-8")


class InterviewPromptBuilder:
    """Builds interview system prompts and retrieval queries."""

    def build_system_prompt(
        self,
        resume_raw_text: str | None,
        resume_structured: dict | None,
        memory_summary: str | None,
        recent_messages: list,
        retrieved_context: list[dict] | None,
    ) -> str:
        """Build the full system prompt for the interviewer LLM."""
        template = _load_prompt("interview_interviewer_system_v1.md")

        # Resume context
        resume_context = ""
        if resume_structured:
            resume_context = self._format_resume_context(
                resume_structured, resume_raw_text
            )
        elif resume_raw_text:
            preview = resume_raw_text[: settings.interview_resume_raw_text_preview_chars]
            resume_context = f"候选人简历原文（节选）：\n{preview}"

        # Memory summary
        memory_text = memory_summary or "（无历史摘要）"

        # Retrieved knowledge
        knowledge_text = self._format_retrieved_context(retrieved_context)

        # Recent conversation
        conv_text = self._format_recent_conversation(recent_messages)

        # Total context cap
        max_chars = settings.interview_max_context_chars
        knowledge_alloc = min(len(knowledge_text), max_chars // 4)
        resume_alloc = min(len(resume_context), max_chars // 3)
        conv_alloc = min(len(conv_text), max_chars // 3)
        memory_alloc = min(len(memory_text), max_chars // 6)

        return template.format(
            resume_context=resume_context[:resume_alloc],
            memory_summary=memory_text[:memory_alloc],
            retrieved_knowledge=knowledge_text[:knowledge_alloc],
            recent_conversation=conv_text[:conv_alloc],
        )

    def build_retrieval_query(
        self,
        resume_structured: dict | None,
        current_message: str,
        recent_messages: list,
    ) -> str:
        """Build a retrieval query string for Milvus search.

        Combines: resume keywords + recent topics + current user message.
        """
        parts: list[str] = []

        # Extract tech keywords from structured resume
        if resume_structured:
            skills = resume_structured.get("skills", {})
            if isinstance(skills, dict):
                tech_keywords = []
                for key in ("languages", "frameworks", "databases", "tools", "ai_ml"):
                    vals = skills.get(key, [])
                    if isinstance(vals, list):
                        tech_keywords.extend(vals)
                if tech_keywords:
                    parts.append("技术栈: " + ", ".join(tech_keywords[:10]))

            # Extract target role
            basic_info = resume_structured.get("basic_info", {})
            if isinstance(basic_info, dict):
                target = basic_info.get("target_role", "")
                if target:
                    parts.append(f"目标岗位: {target}")

        # Recent conversation topics (last 3 rounds)
        recent_user_msgs = [
            m.content
            for m in recent_messages[-6:]
            if getattr(m, "role", None) == "USER"
        ]
        if recent_user_msgs:
            parts.append("最近话题: " + " | ".join(recent_user_msgs[-3:]))

        # Current message
        parts.append(f"当前问题: {current_message}")

        query = " ".join(parts)
        if len(query) > 300:
            # Keep first 150 + last 150 chars
            query = query[:150] + " ... " + query[-150:]
        return query

    def extract_source_label(self, retrieved_context: list[dict] | None) -> str:
        """Determine source label based on KB retrieval hits."""
        if not retrieved_context:
            return "LLM_GENERATED"
        hit_count = len(retrieved_context)
        if hit_count >= 3:
            return "KB_RETRIEVED"
        return "HYBRID"

    # ── Private helpers ──

    def _format_resume_context(
        self, structured: dict, raw_text: str | None
    ) -> str:
        """Format structured resume into a concise string for the prompt."""
        basic = structured.get("basic_info", {})
        skills = structured.get("skills", {})
        projects = structured.get("projects", [])
        internships = structured.get("internships", [])
        highlights = structured.get("highlights", [])
        risk_points = structured.get("risk_points", [])

        lines = ["【候选人简历摘要】"]

        if basic:
            name = basic.get("name", "")
            target = basic.get("target_role", "")
            current = basic.get("current_role", "")
            years = basic.get("years_of_experience", "")
            if name:
                lines.append(f"姓名: {name}")
            if target or current:
                roles = f"{current} → {target}" if current and target else (current or target)
                lines.append(f"岗位: {roles}")
            if years:
                lines.append(f"工作年限: {years}")

        if isinstance(skills, dict):
            all_skills = []
            for key in ("languages", "frameworks", "databases", "tools", "ai_ml"):
                vals = skills.get(key, [])
                if isinstance(vals, list):
                    all_skills.extend(vals)
            if all_skills:
                lines.append(f"技术栈: {', '.join(all_skills)}")

        if isinstance(projects, list) and projects:
            lines.append(f"项目经验 ({len(projects)}个):")
            for p in projects[:5]:
                if isinstance(p, dict):
                    p_name = p.get("name", "")
                    p_desc = p.get("description", "")
                    p_tech = p.get("tech_stack", [])
                    tech_str = ", ".join(p_tech) if isinstance(p_tech, list) else ""
                    lines.append(f"  - {p_name}: {p_desc[:100]} [{tech_str}]")

        if isinstance(internships, list) and internships:
            lines.append(f"实习/工作经历 ({len(internships)}个):")
            for i in internships[:3]:
                if isinstance(i, dict):
                    lines.append(f"  - {i.get('company', '')} | {i.get('role', '')}")

        if isinstance(highlights, list) and highlights:
            lines.append(f"亮点: {'; '.join(highlights[:5])}")

        if isinstance(risk_points, list) and risk_points:
            lines.append("风险点:")
            for r in risk_points[:5]:
                if isinstance(r, dict):
                    lines.append(
                        f"  - [{r.get('area', '')}] {r.get('description', '')[:100]}"
                    )

        return "\n".join(lines)

    def _format_retrieved_context(
        self, retrieved: list[dict] | None
    ) -> str:
        """Format retrieved KB chunks for the prompt."""
        if not retrieved:
            return "（无相关知识库参考内容）"

        lines = ["【知识库参考内容】"]
        for i, chunk in enumerate(retrieved[:8]):
            title = chunk.get("title", "未知")
            content = chunk.get("content", chunk.get("preview", ""))
            source = chunk.get("source_type", "")
            score = chunk.get("score", 0)
            lines.append(
                f"[{i + 1}] ({title}) [来源:{source} 相关度:{score:.2f}]\n{content[:300]}"
            )

        return "\n\n".join(lines)

    def _format_recent_conversation(self, messages: list) -> str:
        """Format recent messages into conversation text."""
        if not messages:
            return "（新对话）"

        lines = ["【最近对话记录】"]
        for m in messages:
            role_label = {
                "USER": "候选人",
                "ASSISTANT": "面试官",
                "SYSTEM": "系统",
            }.get(getattr(m, "role", ""), "未知")
            content = getattr(m, "content", "")
            lines.append(f"{role_label}: {content}")

        return "\n".join(lines)

    # ── Phase 3.3: Question-driven evaluation / follow-up prompts ──

    def build_evaluation_prompt(
        self,
        current_question: str,
        standard_answer: str,
        resume_structured: dict | None,
        resume_raw_text: str | None,
        memory_summary: str | None,
        recent_messages: list,
        user_answer: str,
        target_position: str = "",
        interview_mode: str = "comprehensive",
        completed_summaries: str = "（无）",
        remaining_summaries: str = "（无）",
        interview_enable_dynamic: bool = True,
    ) -> str:
        """Build evaluation prompt for the first answer to a question.

        Does NOT retrieve KB — the standard_answer is the scoring rubric.
        """
        template = _load_prompt("interview_question_evaluation_v1.md")

        resume_context = ""
        if resume_structured:
            resume_context = self._format_resume_context(
                resume_structured, resume_raw_text
            )
        elif resume_raw_text:
            preview = resume_raw_text[: settings.interview_resume_raw_text_preview_chars]
            resume_context = f"候选人简历原文（节选）：\n{preview}"

        memory_text = memory_summary or "（无历史摘要）"
        conv_text = self._format_recent_conversation(recent_messages)

        # Cap context sizes
        max_chars = settings.interview_max_context_chars
        resume_alloc = min(len(resume_context), max_chars // 4)
        conv_alloc = min(len(conv_text), max_chars // 3)
        memory_alloc = min(len(memory_text), max_chars // 6)

        enable_dynamic = (
            "true" if (interview_enable_dynamic and settings.interview_enable_dynamic_question)
            else "false"
        )

        return template.format(
            current_question=current_question,
            standard_answer=standard_answer or "（无参考答案，请根据简历和常识评价）",
            resume_context=resume_context[:resume_alloc],
            memory_summary=memory_text[:memory_alloc],
            recent_conversation=conv_text[:conv_alloc],
            user_answer=user_answer,
            target_position=target_position or "未指定",
            interview_mode=interview_mode,
            completed_questions_summary=completed_summaries,
            remaining_questions_summary=remaining_summaries,
            interview_enable_dynamic_question=enable_dynamic,
        )

    def build_follow_up_prompt(
        self,
        current_question: str,
        standard_answer: str,
        resume_structured: dict | None,
        resume_raw_text: str | None,
        memory_summary: str | None,
        recent_messages: list,
        user_answer: str,
        retrieved_context: list[dict] | None,
        target_position: str = "",
        interview_mode: str = "comprehensive",
        completed_summaries: str = "（无）",
        remaining_summaries: str = "（无）",
    ) -> str:
        """Build evaluation prompt for follow-up answers.

        Retrieves KB context to help the LLM ask deeper follow-up questions.
        """
        template = _load_prompt("interview_question_evaluation_v1.md")

        resume_context = ""
        if resume_structured:
            resume_context = self._format_resume_context(
                resume_structured, resume_raw_text
            )
        elif resume_raw_text:
            preview = resume_raw_text[: settings.interview_resume_raw_text_preview_chars]
            resume_context = f"候选人简历原文（节选）：\n{preview}"

        memory_text = memory_summary or "（无历史摘要）"
        conv_text = self._format_recent_conversation(recent_messages)
        knowledge_text = self._format_retrieved_context(retrieved_context)

        max_chars = settings.interview_max_context_chars
        knowledge_alloc = min(len(knowledge_text), max_chars // 4)
        resume_alloc = min(len(resume_context), max_chars // 4)
        conv_alloc = min(len(conv_text), max_chars // 4)
        memory_alloc = min(len(memory_text), max_chars // 6)

        enable_dynamic = (
            "true" if settings.interview_enable_dynamic_question else "false"
        )

        return template.format(
            current_question=current_question,
            standard_answer=standard_answer or "（无参考答案，请根据简历和常识评价）",
            resume_context=resume_context[:resume_alloc],
            memory_summary=memory_text[:memory_alloc],
            recent_conversation=conv_text[:conv_alloc],
            user_answer=user_answer,
            target_position=target_position or "未指定",
            interview_mode=interview_mode,
            completed_questions_summary=completed_summaries,
            remaining_questions_summary=remaining_summaries,
            interview_enable_dynamic_question=enable_dynamic,
        ) + f"\n\n【知识库参考（用于追问深度）】\n{knowledge_text[:knowledge_alloc]}"

    def build_dimension_extraction_prompt(
        self, resume_structured: dict, target_position: str = "", question_count: int = 20
    ) -> str:
        """Build prompt for extracting interview dimensions from resume."""
        import json as _json
        template = _load_prompt("interview_dimension_extraction_v1.md")
        resume_json_str = _json.dumps(resume_structured, ensure_ascii=False, indent=2)
        return template.format(
            resume_summary_json=resume_json_str,
            target_position=target_position or "未指定",
            question_count=str(question_count),
        )

    def build_question_summary_text(
        self, questions: list[dict], question_type: str = "completed"
    ) -> str:
        """Format question summaries for prompt inclusion.

        Args:
            questions: list of question summary dicts (no standard_answer).
            question_type: "completed" or "remaining".
        """
        if not questions:
            return "（无）"
        label = "已完成题目" if question_type == "completed" else "剩余题目"
        lines = [f"【{label}】"]
        for q in questions:
            status_mark = {
                "ASKED": "提问中",
                "ANSWERED": "已答",
                "SKIPPED": "已跳过",
                "PENDING": "待提问",
            }.get(q.get("status", ""), q.get("status", ""))
            dyn = " [动态插入]" if q.get("is_dynamic") else ""
            lines.append(
                f"- Q{q.get('question_index', '?')}: {q.get('question', '')[:100]} "
                f"[{status_mark}][{q.get('dimension', '')}]{dyn}"
            )
            if q.get("answer_summary"):
                lines.append(f"  表现: {q['answer_summary'][:200]}")
        return "\n".join(lines)
