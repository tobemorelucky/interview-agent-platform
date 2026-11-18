"""Read-only, checkpointed action loop for an explicitly opted-in adaptive Main."""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Annotated, Literal

import anyio
from pydantic import BaseModel, ConfigDict, Field, RootModel, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from interview_api.core.config import settings
from interview_api.core.exceptions import AppError
from interview_api.infrastructure.models.provider import ModelProviderError
from interview_api.modules.candidate_profile.evidence import digest
from interview_api.modules.experience.models import ExperienceAgentRun, ExperienceAgentStepRun
from interview_api.modules.interview.adaptive import ScopedAdaptiveSelection, resolve_selection
from interview_api.modules.interview.agents import AgentResult, configured_prompt_registry
from interview_api.modules.interview.context_projection import model_payload
from interview_api.modules.interview.controller import SessionController
from interview_api.modules.interview.director_repair import SupportingRefsRepair
from interview_api.modules.interview.models import InterviewAgentCall, InterviewContextSnapshot
from interview_api.modules.interview.skill_engine import agent_payload
from interview_api.modules.public_career.research.audit import ResearchModelCalls, finish_research_run

VERSION = "MAIN_HARNESS_V1"
TOOL_LIMIT, MODEL_LIMIT, HTTP_LIMIT = 2, 3, 4
TOOL_TIMEOUT_SECONDS = 2
TOOL_STEP = "main_harness_tool"
logger = logging.getLogger(__name__)


class _AdmissionRefused(Exception):
    """The session budget refused this logical call before any HTTP was sent."""


class EmptyArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RequirementArguments(EmptyArguments):
    requirement_id: int = Field(gt=0, le=2**63 - 1)


class ReadGaps(EmptyArguments):
    kind: Literal["tool"]
    name: Literal["read_active_knowledge_gaps"]
    arguments: EmptyArguments


class InspectSkill(EmptyArguments):
    kind: Literal["tool"]
    name: Literal["inspect_current_skill"]
    arguments: EmptyArguments


class InspectRequirement(EmptyArguments):
    kind: Literal["tool"]
    name: Literal["inspect_job_requirement"]
    arguments: RequirementArguments


class ReviewedExperienceArguments(EmptyArguments):
    topics: list[str] = Field(default_factory=list, max_length=2)
    max_items: int = Field(default=3, ge=1, le=3)


class SearchReviewedExperiences(EmptyArguments):
    kind: Literal["tool"]
    name: Literal["search_reviewed_interview_experiences"]
    arguments: ReviewedExperienceArguments


class FinalAction(EmptyArguments):
    kind: Literal["final"]
    selection: ScopedAdaptiveSelection


ToolAction = Annotated[ReadGaps | InspectSkill | InspectRequirement | SearchReviewedExperiences,
                       Field(discriminator="name")]


class MainHarnessAction(RootModel[Annotated[ToolAction | FinalAction, Field(discriminator="kind")]]):
    @model_validator(mode="wrap")
    @classmethod
    def accept_identical_tool_echo(cls, value, handler):
        action_fields = {"kind", "name", "arguments"}
        # The archived hosted response echoed an empty input-only feedback list.
        # Never let a model supply feedback, or discard any other unknown field.
        if (isinstance(value, dict) and set(value) == action_fields | {"tool_feedback"}
                and value["kind"] == "tool" and type(value["tool_feedback"]) is list
                and not value["tool_feedback"]):
            outer = {key: item for key, item in value.items() if key != "tool_feedback"}
            action = handler(outer)
            logger.info("main_harness_empty_tool_feedback_normalized original_sha256=%s action_sha256=%s",
                        digest(value), digest(outer))
            return action

        # Some hosted responses echo the tool object as a JSON string in _raw.
        # Only remove this one field when it repeats the complete outer action.
        if (not isinstance(value, dict) or set(value) != action_fields | {"_raw"}
                or value["kind"] != "tool" or not isinstance(value["_raw"], str)
                or len(value["_raw"]) > 4096):
            return handler(value)

        def unique_object(pairs):
            obj = dict(pairs)
            if len(obj) != len(pairs):
                raise ValueError("duplicate JSON key")
            return obj

        outer = {key: value[key] for key in ("kind", "name", "arguments")}
        try:
            echo = json.loads(value["_raw"], object_pairs_hook=unique_object)
        except (ValueError, RecursionError):
            return handler(value)
        if digest(echo) != digest(outer):
            return handler(value)
        action = handler(outer)
        logger.info("main_harness_tool_echo_normalized tool=%s action_sha256=%s", action.root.name, digest(outer))
        return action


class FinalSelectionRepair(SupportingRefsRepair):
    """Keep the action envelope intact and delegate the existing subset rule."""

    def prepare(self, original, error):
        if not isinstance(original, dict) or set(original) != {"kind", "selection"} or original["kind"] != "final":
            return None
        if not isinstance(error, ValidationError) or len(error.errors()) != 1:
            return None
        item = error.errors()[0]
        if item["loc"] not in (("final", "selection", "supporting_refs"), ("selection", "supporting_refs")):
            return None
        try:
            ScopedAdaptiveSelection.model_validate(original["selection"])
        except ValidationError as selection_error:
            plan = super().prepare(original["selection"], selection_error)
            return {**plan, "field_path": ["selection", "supporting_refs"]} if plan else None
        return None

    def validate(self, original, repaired):
        if set(repaired) != set(original) or repaired["kind"] != original["kind"]:
            raise ValueError("Repair changed the action envelope")
        return super().validate(original["selection"], repaired["selection"])


def _clip(text, limit):
    return text.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def _feedback(status, code, data=None, refs=()):
    result = dict(status=status, code=code, data=data, source_refs=list(refs),
                  content_hash=digest(data), trust="UNTRUSTED_DATA")
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 2048:
        return _feedback("ERROR", "OUTPUT_LIMIT")
    return result


class MainHarness:
    def __init__(self, service, agent):
        self.service, self.agent = service, agent
        self.factory = service.factory

    async def _claim(self, sid, uid, expected, aid, payload):
        fingerprint = digest(payload)
        async with self.factory() as db:
            session = await SessionController(db).owned(sid, uid, lock=True)
            if session.state_version != expected:
                raise AppError("INTERVIEW_CONFLICT", "面试版本已变化", 409)
            snapshot = await db.get(InterviewContextSnapshot, session.context_snapshot_id)
            if (not snapshot or snapshot.user_id != uid or snapshot.interview_session_id != sid or
                    snapshot.context_json.get("adaptive_policy") != payload["snapshot"]["adaptive_policy"]):
                raise AppError("MAIN_HARNESS_SNAPSHOT", "本场 Main 工具版本不可用", 409)
            run = await db.scalar(select(ExperienceAgentRun).join(
                InterviewAgentCall, InterviewAgentCall.agent_run_id == ExperienceAgentRun.id).where(
                InterviewAgentCall.session_id == sid, InterviewAgentCall.answer_id == aid,
                ExperienceAgentRun.graph_name == "interview_main_harness",
                ExperienceAgentRun.input_hash == fingerprint).order_by(ExperienceAgentRun.id.desc()).limit(1))
            if run is not None:
                if run.status == "RUNNING":
                    if run.started_at + timedelta(seconds=settings.interview_model_timeout_seconds + 30) < datetime.now(timezone.utc):
                        raise AppError("MAIN_HARNESS_INTERRUPTED", "上次执行中断，无法确认未完成的调用；请新建面试", 409)
                    raise AppError("MAIN_HARNESS_RUNNING", "本轮 Main 正在处理，请稍候", 409)
                if run.status == "SUCCEEDED":
                    return run.id, run.output_json["validated_artifact"]
                if (run.output_json or {}).get("terminal"):
                    raise AppError(run.error_message, "本轮 Main 已安全停止，未推进会话", 422)
                run.status, run.error_message, run.finished_at = "RUNNING", None, None
                run.started_at = datetime.now(timezone.utc)
            else:
                run = ExperienceAgentRun(agent_role=self.agent.role, graph_name="interview_main_harness",
                    graph_version=VERSION, input_hash=fingerprint, status="RUNNING", started_at=datetime.now(timezone.utc))
                db.add(run)
                await db.flush()
                db.add(InterviewAgentCall(session_id=sid, answer_id=aid, agent_run_id=run.id))
            await db.commit()
            return run.id, None

    async def _steps(self, run_id):
        async with self.factory() as db:
            return list(await db.scalars(select(ExperienceAgentStepRun).where(
                ExperienceAgentStepRun.agent_run_id == run_id).order_by(ExperienceAgentStepRun.id)))

    async def _read_tool(self, action, sid, uid, expected, payload):
        session, context, _, _, _ = await self.service.read_state(sid, uid)
        snapshot = payload["snapshot"]
        if (session.state_version != expected or context["adaptive_policy"] != snapshot["adaptive_policy"] or
                context["adaptive_blueprint"]["fingerprint"] != snapshot["adaptive_blueprint"]["fingerprint"] or
                context["jd_fingerprint"] != snapshot["jd_fingerprint"]):
            raise AppError("INTERVIEW_CONFLICT", "面试版本已变化", 409)
        if isinstance(action, ReadGaps):
            adaptive = payload["adaptive"]
            kid = adaptive["active_knowledge_id"]
            gaps = adaptive["knowledge_state"]["items"].get(kid, {}).get("open_gaps", [])
            if not gaps:
                return _feedback("EMPTY", "NO_ACTIVE_GAPS")
            rows = [dict(id=gap["id"], text=_clip(gap["text"], 95)) for gap in gaps[:8]]
            refs = [ref for ref in (adaptive["current_answer_ref"], adaptive["current_evaluation_ref"]) if ref]
            return _feedback("OK", "ACTIVE_GAPS", dict(knowledge_id=kid, gaps=rows,
                truncated=len(gaps) > len(rows) or any(r["text"] != g["text"] for r, g in zip(rows, gaps)),
                state_scope="CURRENT_DECISION_INCLUDES_VALIDATED_PENDING_EVALUATION"), refs)
        if isinstance(action, SearchReviewedExperiences):
            return self._reviewed_experiences(action, payload)
        if isinstance(action, InspectRequirement):
            rid = action.arguments.requirement_id
            ref = f"requirement:{rid}"
            row = next((row for row in context["requirements"] if row["id"] == rid), None)
            if row is None or not any(source["ref"] == ref and source["source_type"] == "JOB_REQUIREMENT"
                    and source["fingerprint"] == row["fingerprint"]
                    for goal in context["adaptive_blueprint"]["targets"] for source in goal["sources"]):
                return _feedback("ERROR", "REQUIREMENT_NOT_IN_FROZEN_SCOPE")
            text = _clip(row["statement"], 1100)
            return _feedback("OK", "FROZEN_REQUIREMENT", dict(requirement_id=rid, statement=text,
                truncated=text != row["statement"], importance=row["importance"], fingerprint=row["fingerprint"],
                jd_version_id=context["job_description_version_id"]), [ref])
        skill = context["interviewer_config"]["skill"]
        instructions = _clip(skill["instructions"], 960)
        return _feedback("OK", "FROZEN_SKILL", dict(id=skill["id"], version=skill["version"],
            fingerprint=skill["content_fingerprint"], priority_topics=skill["priority_topics"],
            instructions=instructions, truncated=instructions != skill["instructions"]),
            [f"skill:{skill['id']}@{skill['version']}"])

    def _reviewed_experiences(self, action, payload):
        """Read only this session's frozen reviewed items; never a live query."""
        scope = payload["snapshot"].get("reviewed_experience_scope") or {}
        items = scope.get("items") or []
        if not items:
            return _feedback("EMPTY", "NO_REVIEWED_EXPERIENCE_FOR_SCOPE")
        legal = {candidate.get("topic") for candidate in payload["adaptive"].get("candidates") or []
                 if candidate.get("topic")}
        legal |= {target.get("topic") for target in (payload["adaptive"].get("targets") or {}).values()
                  if target.get("topic")}
        requested = list(dict.fromkeys(action.arguments.topics))
        if any(topic not in legal for topic in requested):
            return _feedback("ERROR", "TOPIC_OUT_OF_SCOPE")
        questions = {row["id"]: row for row in payload["snapshot"].get("public_questions") or []}
        chosen, refs = [], []
        for item in items:
            if requested and not set(requested) & set(item.get("topics") or []):
                continue
            rows = []
            for ref in item.get("question_refs") or []:
                try:
                    question = questions[int(ref.split(":", 1)[1])]
                except (KeyError, ValueError, IndexError):
                    continue
                text = _clip(question.get("question") or "", 140)
                rows.append(dict(ref=ref, text=text, dimension=question.get("dimension")))
                refs.append(ref)
                if len(rows) >= 3:
                    break
            chosen.append(dict(experience_ref=item["experience_ref"], round=item.get("round"),
                reviewed_at=item.get("reviewed_at"), reliability=item.get("reliability"),
                source_url=item.get("source_url"), topics=(item.get("topics") or [])[:6],
                questions=rows))
            refs.append(item["experience_ref"])
            if len(chosen) >= action.arguments.max_items:
                break
        if not chosen:
            return _feedback("EMPTY", "NO_MATCHING_REVIEWED_EXPERIENCE",
                             dict(topics=requested, scope_items=len(items)))
        return _feedback("OK", "REVIEWED_EXPERIENCES", dict(
            scope_company_job="EXACT_FROZEN_SCOPE", items=chosen,
            truncated=len(chosen) < len(items)), refs)

    async def _tool_step(self, run_id, action, feedback, sid, uid, expected, payload):
        started = perf_counter()
        arguments = action.arguments.model_dump()
        if any(f["tool"] == action.name and f["arguments"] == arguments for f in feedback):
            result = _feedback("ERROR", "DUPLICATE_TOOL")
        else:
            try:
                async with asyncio.timeout(TOOL_TIMEOUT_SECONDS):
                    result = await self._read_tool(action, sid, uid, expected, payload)
            except TimeoutError:
                result = _feedback("ERROR", "TOOL_TIMEOUT")
            except AppError as exc:
                if exc.status_code not in (403, 404) and exc.code != "INTERVIEW_CONFLICT":
                    raise
                result = _feedback("ERROR", "TOOL_SCOPE_UNAVAILABLE")
            except SQLAlchemyError:
                result = _feedback("ERROR", "TOOL_STORAGE_UNAVAILABLE")
        item = dict(tool=action.name, arguments=arguments, result=result)
        async with self.factory() as db:
            db.add(ExperienceAgentStepRun(agent_run_id=run_id, step_name=TOOL_STEP,
                status="SUCCEEDED" if result["status"] != "ERROR" else "FAILED",
                input_json=dict(tool=action.name, arguments=arguments), output_json=item,
                latency_ms=round((perf_counter() - started) * 1000)))
            await db.commit()
        return item

    async def _tool_audit(self, run_id, artifact):
        """Separate a real tool query from a preloaded public question.

        `used_tool_result` is true only when the final question's source ref was
        actually returned by a tool call in this run. A public_question that was
        already frozen in the snapshot is reported as preloaded, not as a tool hit.
        """
        tools = []
        for step in await self._steps(run_id):
            if step.step_name != TOOL_STEP:
                continue
            item = step.output_json or {}
            tools.append(dict(tool=item.get("tool"), arguments=item.get("arguments"),
                              status=(item.get("result") or {}).get("status"),
                              code=(item.get("result") or {}).get("code"),
                              source_refs=(item.get("result") or {}).get("source_refs") or []))
        source_ref = getattr(artifact, "question_source_ref", None)
        searched = [item for item in tools if item["tool"] == "search_reviewed_interview_experiences"]
        returned = {ref for item in searched for ref in item["source_refs"]}
        return dict(tool_called=bool(tools), tools=tools,
                    reviewed_search_called=bool(searched), reviewed_search_count=len(searched),
                    selection_source_ref=source_ref,
                    used_tool_result=bool(source_ref and source_ref in returned),
                    preloaded_public_question=bool(source_ref and source_ref.startswith("public_question:")),
                    used_preloaded_without_tool=bool(source_ref and source_ref.startswith("public_question:")
                                                     and not searched))

    async def _result(self, run_id, artifact, prompt, provider):
        usage, complete, repairs = {}, True, 0
        for step in await self._steps(run_id):
            if step.step_name == TOOL_STEP:
                continue
            if (step.provider_name, step.model_name) != (provider.provider_name, provider.model_name):
                raise AppError("MAIN_HARNESS_MODEL_CHANGED", "Main 模型与已开始的决策不一致", 409)
            for key, count in (step.token_usage_json or {}).items():
                if type(count) is int and count >= 0:
                    usage[key] = usage.get(key, 0) + count
            complete = complete and (step.output_json or {}).get("usage_complete", False)
            repairs += step.structured_retry
        return AgentResult(artifact, run_id, provider.provider_name, provider.model_name, prompt.version,
                           repairs, usage, complete)

    async def call(self, sid, uid, expected, payload, aid):
        from interview_api.modules.interview.adaptive import ResolvedScopedAdaptiveDecision
        payload = agent_payload(payload, self.agent.route)
        wire = model_payload(payload, self.agent.route)
        registry = configured_prompt_registry(payload)
        route = self.agent.route
        prompt = registry.get(route)
        provider = self.agent.router.primary(route)
        run_id, stored = await self._claim(sid, uid, expected, aid, payload)
        if stored is not None:
            artifact = ResolvedScopedAdaptiveDecision.model_validate(stored)
            self.agent.validate_artifact(artifact, payload)
            return await self._result(run_id, artifact, prompt, provider)
        terminal = False
        try:
            try:
                async with asyncio.timeout(settings.interview_model_timeout_seconds):
                    artifact = await self._loop(run_id, sid, uid, expected, payload, wire, registry, prompt, provider, aid)
            except TimeoutError as exc:
                raise ModelProviderError("main_harness_timeout", "本轮 Main 执行超时，未提交题目") from exc
            # An illegal FINAL is a business rejection, not a repair opportunity.
            try:
                artifact = resolve_selection(artifact, payload)
                self.agent.validate_artifact(artifact, payload)
            except AppError:
                terminal = True
                raise
            result = await self._result(run_id, artifact, prompt, provider)
            await finish_research_run(self.factory, run_id, status="SUCCEEDED",
                output={"validated_artifact": artifact.model_dump(), "harness_version": VERSION,
                        "tool_audit": await self._tool_audit(run_id, artifact)})
            return result
        except _AdmissionRefused:
            # Preserve the audited run as FAILED, then hand the refusal back as
            # None exactly like the non-harness Main path.
            with anyio.CancelScope(shield=True):
                await finish_research_run(self.factory, run_id, status="FAILED",
                    error="INTERVIEW_BUDGET_EXHAUSTED",
                    output={"harness_version": VERSION, "terminal": False})
            return None
        except BaseException as exc:
            code = getattr(exc, "code", type(exc).__name__)
            with anyio.CancelScope(shield=True):
                await finish_research_run(self.factory, run_id, status="FAILED", error=code,
                    output={"harness_version": VERSION, "terminal": terminal or code == "MAIN_HARNESS_LIMIT"})
            raise

    async def _loop(self, run_id, sid, uid, expected, payload, wire, registry, prompt, provider, aid=None):
        route = self.agent.route
        steps = await self._steps(run_id)
        models = [step for step in steps if step.step_name != TOOL_STEP]
        feedback = [step.output_json for step in steps if step.step_name == TOOL_STEP]
        if any(step.provider_name != provider.provider_name or step.model_name != provider.model_name for step in models):
            raise AppError("MAIN_HARNESS_MODEL_CHANGED", "Main 模型与已开始的决策不一致", 409)
        http_slots = 0
        for step in models:
            invocations = (step.output_json or {}).get("model_invocations", [])
            # If a process vanished before persisting invocation details,
            # conservatively count the reserved slots.
            http_slots += len(invocations) or (step.input_json or {}).get("max_http", 2)
        repaired = http_slots > len(models)
        actions = [MainHarnessAction.model_validate(step.output_json["main_action"]).root for step in models
                   if (step.output_json or {}).get("main_action")]
        if actions and isinstance(actions[-1], FinalAction):
            return actions[-1].selection
        if len(actions) > len(feedback):
            feedback.append(await self._tool_step(run_id, actions[-1], feedback, sid, uid, expected, payload))
        for index in range(len(models), MODEL_LIMIT):
            if http_slots >= HTTP_LIMIT:
                break
            schema = FinalAction if len(feedback) >= TOOL_LIMIT or index == MODEL_LIMIT - 1 else MainHarnessAction
            allow_repair = not repaired and http_slots < HTTP_LIMIT - 1
            packet = dict(wire, tool_feedback=list(feedback), main_harness_version=VERSION)
            output_cap = getattr(provider, "max_output_tokens", settings.interview_model_max_output_tokens)
            schema_bytes = len(json.dumps(schema.model_json_schema(), ensure_ascii=False).encode("utf-8"))
            # The hold includes the capped repair input. ModelGovernance still
            # checks owner admission at every actual send.
            repair_bytes = schema_bytes + 4 * output_cap + 4096 if allow_repair else None
            reservation = 0 if provider.provider_name == "replay" else (
                len(json.dumps(packet, ensure_ascii=False).encode("utf-8")) + len(prompt.system.encode("utf-8")) +
                schema_bytes + output_cap + 2048 + (repair_bytes + output_cap if repair_bytes is not None else 0))
            result = await self.service.with_model_reservation(sid, uid, expected, provider, reservation,
                lambda timeout: ResearchModelCalls(self.factory, self.agent.router, run_id, prompts=registry,
                    timeout_seconds=timeout).call(route, route, packet, schema, checkpoint_action=True,
                    max_repair_input_bytes=repair_bytes, repair_policy=FinalSelectionRepair(payload) if allow_repair else None),
                answer_id=aid)
            if result is None:
                # Same signal as the non-harness path: nothing was sent, so the
                # caller may end the session lawfully instead of retrying.
                raise _AdmissionRefused()
            http_slots += len(result.invocations)
            repaired = repaired or bool(result.structured_retry)
            action = result.parsed.root if isinstance(result.parsed, MainHarnessAction) else result.parsed
            if isinstance(action, FinalAction):
                return action.selection
            feedback.append(await self._tool_step(run_id, action, feedback, sid, uid, expected, payload))
        raise AppError("MAIN_HARNESS_LIMIT", "本轮 Main 已达到执行上限，未提交题目", 409)
