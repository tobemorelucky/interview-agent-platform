"""Explicit V2.1 Replay model boundary; never describes LIVE model behavior."""
import asyncio
import json
from interview_api.infrastructure.models.provider import ModelProvider, ModelResponse


def selection_artifact(value, payload, *, scoped=False):
    """Translate an explicitly constructed test artifact, not a LIVE response."""
    pair = next(c for c in payload["adaptive"]["candidates"] if
        c["target_knowledge_id"] == value["target_knowledge_id"] and c["source"]["ref"] == value["question_source_ref"])
    draft = {k: value["proposed_question"][k] for k in ("question", "expected_points", "dimension", "difficulty")}
    result = dict(candidate_id=pair["candidate_id"], strategy=value["strategy"],
        supporting_refs=value["supporting_refs"], reason_tags=value["reason_tags"], proposed_question=draft)
    if scoped:
        gaps = payload["adaptive"]["knowledge_state"]["items"].get(pair["target_knowledge_id"], {}).get("open_gaps", [])
        result["focus_gap_ids"] = [g["id"] for g in gaps[:5]] if value["strategy"] == "FOLLOW_UP" else []
    return result


class AdaptiveReplayProvider(ModelProvider):
    provider_name, model_name = "replay", "DEMO_V21_NOT_LIVE"

    def __init__(self):
        self.calls = []
        self.delayed = set()

    async def generate(self, messages, *, schema):
        p = json.loads(messages[-1]["content"])["input"]
        # Decode the documented shared-dictionary wire DTO at the model seam.
        # No Controller/DB behavior is mocked; original candidate IDs stay exact.
        if p.get('adaptive', {}).get('targets'):
            a = p['adaptive']
            a['candidates'] = [dict(c, label=a['targets'][c['target_knowledge_id']]['label'],
                topic=a['targets'][c['target_knowledge_id']]['topic'],
                source=a['sources'][c['source']['ref']]) for c in a['candidates']]
        title = schema["title"]
        self.calls.append(title)
        if title == "AdaptiveEvaluation":
            text = p["answer"]
            if text == "DEMO_DISCONNECT_ONCE" and text not in self.delayed:
                self.delayed.add(text)
                await asyncio.sleep(3)
            if text == "DEMO_TIMEOUT_ONCE" and text not in self.delayed:
                self.delayed.add(text)
                from interview_api.infrastructure.models.provider import ModelProviderError
                raise ModelProviderError("model_timeout", "DEMO one-shot timeout")
            kid = p["adaptive"]["active_knowledge_id"]
            gaps = p["adaptive"]["knowledge_state"]["items"].get(kid, {}).get("open_gaps", [])
            concrete = "事务" in text or "失败恢复" in text
            mechanism_gap = "ACK" in text or "工具" in text
            gap_text = "ACK 与事务提交边界" if "ACK" in text else "工具权限与失败恢复" if mechanism_gap else "具体实现与失败恢复"
            value = dict(**{k: 4 if concrete else 2 for k in ("technical_correctness", "coverage", "depth", "relevance", "communication")},
                evaluation="DEMO：本轮说明了具体机制。" if concrete else "DEMO：回答较笼统，需要具体实现例子。",
                covered_points=["已描述基本思路"], missing_points=[] if concrete else [gap_text],
                factual_or_reasoning_issues=[], follow_up_focus=[] if concrete else ["提供一个实际请求处理例子"],
                next_action_recommendation="NEXT", knowledge_observations=[dict(knowledge_id=kid,
                    state="DEMONSTRATED_IN_SESSION" if concrete else "INSUFFICIENT_EVIDENCE",
                    answer_quotes=[text[:80]] if concrete else [], missing_mechanisms=[] if concrete else [gap_text],
                    resolved_gap_ids=[g["id"] for g in gaps] if concrete else [])])
        elif title in ("AdaptiveDecision", "AdaptiveSelection"):
            choices = p["adaptive"].get("choices") or [dict(target=dict(
                knowledge_id=c["target_knowledge_id"], label=c["label"], sources=[c["source"]]),
                strategies=c["strategies"]) for c in p["adaptive"]["candidates"]]
            probe = next((c for c in choices if "REQUEST_EXAMPLE" in c["strategies"]), None)
            choice = probe or choices[0]
            mechanism_gap = any("ACK" in point or "工具" in point for point in (p.get("evaluation") or {}).get("missing_points", []))
            strategy = "DEEP_DIVE" if probe and mechanism_gap else "REQUEST_EXAMPLE" if probe else "NEXT"
            target = choice["target"]
            source = target["sources"][0]
            n = len(p["history"]) + 1
            wording = (f"你刚才提到的 {p['evaluation']['missing_points'][0]}，具体由谁校验，失败后如何避免重复执行？" if mechanism_gap and probe else
                f"你刚才提到了整体思路，能以 {target['label']} 的实际请求为例，讲清失败时如何恢复吗？" if probe else
                f"接下来聊 {target['label']}，你会怎样解释其中的关键机制和一个异常边界？")
            value = dict(decision_version="2.1.0", strategy=strategy, target_knowledge_id=target["knowledge_id"],
                supporting_refs=[p["adaptive"]["current_answer_ref"] or source["ref"]],
                reason_tags=["NEEDS_EXAMPLE" if probe else "COVERAGE_SWITCH" if p["history"] else "OPENING"],
                question_source_ref=source["ref"], proposed_question=dict(question=f"DEMO 第{n}轮：{wording}",
                    expected_points=["机制", "异常边界"], dimension=target["label"], difficulty=p["desired_difficulty"],
                    source_type="FOLLOW_UP" if strategy == "REQUEST_EXAMPLE" else "DYNAMIC_GENERATED"))
            if title == "AdaptiveSelection":
                value = selection_artifact(value, p, scoped="focus_gap_ids" in schema.get("properties", {}))
        elif title == "CoachArtifact":
            value = dict(overall_summary="DEMO：本场根据实际回答补问并切换技术主题，观察仅反映本场表现。",
                strengths=[], weaknesses=[], recurring_issues=[], job_alignment=[],
                recommended_actions=["用具体实现和异常处理解释技术决策"], practice_plan=["复盘本场回答中的机制与边界"], memory_proposals=[])
        else:
            raise RuntimeError("Unsupported V2.1 Replay schema: " + title)
        return ModelResponse(json.dumps(value, ensure_ascii=False), dict(input_tokens=0, output_tokens=0, total_tokens=0))
