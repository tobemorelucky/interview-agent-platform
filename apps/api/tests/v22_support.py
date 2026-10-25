"""Fixed Replay behavior for memory wiring tests, NOT real model effectiveness."""
import json
from v21_support import AdaptiveReplayProvider


class MemoryReplayProvider(AdaptiveReplayProvider):
    async def generate(self, messages, *, schema):
        payload = json.loads(messages[-1]["content"])["input"]
        selected_memory = None
        if schema["title"] in ("AdaptiveDecision", "AdaptiveSelection") and not payload.get("answer"):
            plan = payload["snapshot"].get("memory_guided_plan", {}).get("items", [])
            if plan:
                # Replay explicitly selects a legal pair for the remembered target,
                # even when the default Blueprint ranking prefers another target.
                candidates = payload["adaptive"].get("candidates", [])
                preferred = [c for c in candidates if c["target_knowledge_id"] == plan[0]["knowledge_id"]]
                if preferred:
                    selected_memory = preferred[0]
                    payload["adaptive"]["candidates"] = preferred + [c for c in candidates if c not in preferred]
                    messages = [*messages[:-1], {**messages[-1], "content": json.dumps(dict(input=payload), ensure_ascii=False)}]
        result = await super().generate(messages, schema=schema)
        if selected_memory and selected_memory["target_knowledge_id"].endswith(":kafka_reliability"):
            from interview_api.infrastructure.models.provider import ModelResponse
            value = json.loads(result.text)
            value["proposed_question"]["question"] = "DEMO 独立复测：Kafka 消费支付回调时，提交成功却尚未 ACK 就宕机，如何避免重复扣款？"
            return ModelResponse(json.dumps(value, ensure_ascii=False), result.usage)
        return result


async def kafka_seed(factory):
    from test_m4_interview import seed
    from sqlalchemy import select
    from interview_api.modules.public_career.models import JobDescriptionVersion, JobRequirement
    _, _, _, uid, detail = await seed(factory, public=False, budget=2)
    ctx = detail["context_snapshot"]
    async with factory() as db:
        jd = JobDescriptionVersion(job_id=ctx["job_id"], version_number=2,
            raw_text="DEMO Replay：Kafka 消息可靠消费", publication_status="PUBLISHED")
        db.add(jd)
        await db.flush()
        db.add(JobRequirement(job_description_version_id=jd.id, statement="Kafka 消息可靠消费", requirement_type="SKILL"))
        await db.commit()
    return uid, ctx
