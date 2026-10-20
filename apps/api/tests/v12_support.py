"""Explicit offline model double; real API/controller/tools/DB are not mocked."""
import json
import asyncio
from interview_api.infrastructure.models.provider import ModelProvider, ModelResponse


class DesignReplayProvider(ModelProvider):
    provider_name, model_name = "replay", "DEMO_V12_POLICY_DOUBLE_NOT_LIVE"

    def __init__(self):
        self.calls = []
        self.delayed_answers = set()

    async def generate(self, messages, *, schema):
        envelope = json.loads(messages[-1]["content"])
        p = envelope["input"]
        title = schema["title"]
        self.calls.append(title)
        if title == "ToolDecision" and p.get("answer") == "DEMO_DISCONNECT_ONCE" and p["answer"] not in self.delayed_answers:
            self.delayed_answers.add(p["answer"])
            await asyncio.sleep(2)  # bounded model-delay injection, test server only
        evidence = p.get("tool_evidence")
        refs = dict(tool_result_ids=[evidence["id"]], tool_observed_outcome="PASS" if evidence["result"]["passed"] else "FAIL") if evidence else {}
        if title == "ToolDecision":
            revision = p["design_revision"]
            tool = "verify_idempotency" if revision["plan"]["deduplication"] != "ATOMIC_KEY" else "verify_capacity"
            value = dict(tool=tool, arguments=dict(plan_version=revision["version"], requests=min(40, revision["plan"]["capacity"] + 2)), reason="DEMO 基于方案当前未解决风险选择验证目标")
        elif title == "MainArtifact":
            n = len(p["history"])
            questions = ["请设计一个限量名额预约接口，如何处理请求重试和多用户并发？",
                "本次重复请求模拟产生了重复预约，你准备怎样用原子业务键避免重复写入？",
                "去重后并发请求仍会超售，你会怎样原子扣减名额并处理事务回滚？",
                "本次交错已通过，你准备如何处理预约取消后的名额释放与重试？"]
            value = dict(recommendation=p["requested_action"], question=dict(question=questions[min(n, 3)],
                expected_points=["原子去重", "条件更新", "事务边界"], dimension="系统设计", difficulty=p.get("desired_difficulty") or "MEDIUM",
                source_type="DYNAMIC_GENERATED"), **refs)
        elif title == "EvaluationArtifact":
            value = dict(**{key: 4 if evidence["result"]["passed"] else 2 for key in
                ("technical_correctness", "coverage", "depth", "relevance", "communication")},
                evaluation="DEMO 根据用户实际方案与程序验证结果评价，不是实时模型验收。",
                covered_points=["明确声明策略"], missing_points=[] if evidence["result"]["passed"] else ["原子性与并发边界"],
                factual_or_reasoning_issues=[], follow_up_focus=["解释原子转换"],
                next_action_recommendation="FOLLOW_UP" if p["design_revision"]["version"] == 1 else "NEXT", **refs)
        elif title == "CoachArtifact":
            value = dict(overall_summary="DEMO：方案经历重复请求、并发名额验证及修订；模拟仅覆盖声明策略。",
                strengths=[], weaknesses=[], recurring_issues=[], job_alignment=[],
                recommended_actions=["解释去重键和名额扣减的事务边界"], practice_plan=["复盘模拟未覆盖的故障调度"], memory_proposals=[])
        else:
            raise RuntimeError("Unexpected test model schema: " + title)
        return ModelResponse(json.dumps(value, ensure_ascii=False), {"input_tokens": 100, "output_tokens": 100, "total_tokens": 200})
