"""Fix corrupted service.py"""
import re

path = 'src/interview_api/modules/interview/service.py'
with open(path, 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

# Find boundaries
start_marker = "# Get completed questions summary"
end_marker = "# Stream LLM response for progress"

start = content.find(start_marker)
end = content.find(end_marker, start)

if start < 0 or end < 0:
    print(f"ERROR start={start} end={end}")
    exit(1)

# Clean replacement block
replacement = """        # Get completed questions summary
        completed = [q for q in existing if q.status in ("ANSWERED",)]
        completed_text = ""
        for q in completed[:10]:
            q_text = q.question[:100] if q.question else ""
            completed_text += f"Q{q.question_index}: {q_text}\\n"

        # Build prompt for single question generation (Phase 3.5b)
        prompt = (
            f"You are a technical interviewer. Position: {session.target_position or 'general'}. Dimension: {dim_name}.\\n\\n"
            f"Resume: {resume_text[:1000]}\\n\\n"
            f"KB: {kb_context[:500] or '(none)'}\\n"
            f"History: {completed_text[:300] or '(none)'}\\n"
            f"Recent: {user_answer[:200] or '(start)'}\\n\\n"
            "Return JSON with question and standard_answer (3-5 points):\\n"
            f'{{"question":"...","standard_answer":"...","dimension":"{dim_name}",'
            '"difficulty":"MEDIUM","source":"LLM_GENERATED"}'
        )

        """

new_content = content[:start] + replacement + content[end:]
with open(path, 'w', encoding='utf-8') as f:
    f.write(new_content)
print("Done")
