"""V2.2 rule contract: no model, no clock sleeps, no mastery probabilities."""
from datetime import datetime, timedelta, timezone

from interview_api.modules.memory.capability_rules import project, memory_plan

NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)


def observation(n, *, good=False, assisted=False, scope="backend", day=0):
    return dict(evaluation_id=n, session_id=n, question_id=n, topic="kafka_reliability",
        scope=scope, observed_at=(NOW + timedelta(days=day)).isoformat(),
        state="DEMONSTRATED_IN_SESSION" if good else "INSUFFICIENT_EVIDENCE",
        open_gaps=[] if good else ["ACK 与事务边界"], assisted=assisted,
        sufficient_quote=good, disputed=False)


def test_three_sessions_do_not_mistake_prompted_learning_for_mastery():
    a = observation(1)
    b = observation(2, good=True, assisted=True, day=1)
    c = observation(3, good=True, scope="new_scenario", day=2)
    assert project([a], NOW)["state"] == "GAP_SUSPECTED"
    assert project([a, b], NOW + timedelta(days=1))["state"] == "PRACTICING"
    result = project([a, b, c], NOW + timedelta(days=2))
    assert result["state"] == "PRACTICING" and result["independent_sessions"] == 1
    assert result["last_verified_at"] is None
    assert project([a, b, c, observation(4, good=True, day=3)], NOW + timedelta(days=3))["state"] == "VERIFIED"


def test_opposite_same_scope_is_conflict_not_overwrite_and_retest_can_resolve():
    rows = [observation(1, good=True), observation(2, day=1)]
    assert project(rows, NOW + timedelta(days=1))["state"] == "CONFLICTED"
    rows += [observation(3, good=True, day=2), observation(4, good=True, day=3)]
    result = project(rows, NOW + timedelta(days=3))
    assert result["state"] == "VERIFIED"
    assert result["conflicts"][0]["status"] == "RESOLVED_BY_INDEPENDENT_RECHECK"


def test_scope_difference_and_hint_dependence_are_not_global_conflict():
    result = project([observation(1, good=True), observation(2, scope="other", day=1)], NOW + timedelta(days=1))
    assert result["state"] != "CONFLICTED" and result["scope_differences"]
    result = project([observation(1, good=True, assisted=True), observation(2, day=1)], NOW + timedelta(days=1))
    assert result["state"] == "GAP_SUSPECTED" and "HINT_DEPENDENCE" in result["reason_tags"]


def test_freshness_is_not_score_decay_and_clock_is_injectable():
    rows = [observation(1, good=True), observation(2, good=True, scope="other")]
    assert project(rows, NOW + timedelta(days=1))["state"] == "VERIFIED"
    assert project(rows, NOW + timedelta(days=30))["state"] == "VERIFIED"
    old = project(rows, NOW + timedelta(days=90))
    assert old["state"] == "RECHECK_DUE" and old["independent_sessions"] == 2
    assert len(old["observations"]) == 2


def test_dedup_revoke_exclude_and_model_dispute():
    row = observation(1, good=True)
    assert project([row, row], NOW)["independent_sessions"] == 1
    assert project([row], NOW, excluded=[1])["state"] == "UNKNOWN"
    assert project([row], NOW, revoked=True)["state"] == "UNKNOWN"
    row["disputed"] = True
    assert project([row], NOW)["state"] == "CONFLICTED"


def test_plan_only_relevant_bounded_non_revoked_and_verified_not_repeated():
    states = [dict(topic="kafka_reliability", revision=1, revoked=False,
        **project([observation(1)], NOW))]
    targets = [dict(knowledge_id="java_backend:kafka_reliability", topic="kafka_reliability", priority=4)]
    plan = memory_plan(states, targets, NOW)
    assert plan["items"][0]["knowledge_id"] == targets[0]["knowledge_id"]
    assert plan["items"][0]["intent"] == "INDEPENDENT_VERIFICATION"
    assert not memory_plan(states, [], NOW)["items"]
    states[0]["revoked"] = True
    assert not memory_plan(states, targets, NOW)["items"]


def test_different_rubrics_or_sources_are_not_claimed_to_be_identical_conditions():
    from types import SimpleNamespace
    from interview_api.modules.memory.capability_service import observation_scope
    snapshot = SimpleNamespace(job_id=1, context_json={})
    q = SimpleNamespace(id=1, question="Kafka 事务与 ACK？", candidate_refs_json=dict(adaptive_decision=dict(question_source_ref="requirement:1")), difficulty="MEDIUM", standard_answer="ACK\n事务边界")
    original = observation_scope(snapshot, q)
    q.standard_answer = "事务边界\nACK"
    assert observation_scope(snapshot, q) == original
    q.standard_answer = "重平衡\n分区顺序"
    assert observation_scope(snapshot, q) != original
    q.standard_answer = "ACK\n事务边界"
    q.question = "Kafka 重平衡下如何保持分区顺序？"
    assert observation_scope(snapshot, q) != original


def test_old_irrelevant_memories_drop_from_retrieval_not_from_history():
    target = dict(knowledge_id="java_backend:kafka_reliability", priority=0)
    state = dict(topic="kafka_reliability", revision=1, revoked=False, **project([observation(1)], NOW))
    assert not memory_plan([state], [target], NOW + timedelta(days=181))["items"]
    assert state["observations"]  # Historical mistakes weren't deleted.
    target["priority"] = 4
    assert memory_plan([state], [target], NOW + timedelta(days=181))["items"]
    verified = dict(topic="kafka_reliability", revision=2, revoked=False,
        **project([observation(2, good=True), observation(3, good=True)], NOW))
    assert not memory_plan([verified], [target], NOW)["items"]


def test_disputed_success_is_not_a_new_verification_and_hints_cannot_restore_mastery():
    rows = [observation(1, good=True), observation(2, day=1), observation(3, good=True, day=2)]
    value = project(rows, NOW + timedelta(days=2))
    assert value["state"] == "CONFLICTED" and value["last_verified_at"] is None
    rows = [observation(1, good=True), observation(2, good=True, day=1), observation(3, good=True, assisted=True, day=2)]
    assert project(rows, NOW + timedelta(days=2))["state"] == "PRACTICING"


def test_fresh_verification_changes_priority_not_source_permissions():
    from test_v21_adaptive import context
    from interview_api.modules.interview.adaptive import build_blueprint
    ctx = context()
    before = build_blueprint(ctx)
    after = build_blueprint(ctx, deprioritized_topics={"kafka_reliability"})
    assert before["targets"][0]["topic"] == "kafka_reliability"
    assert after["targets"][0]["topic"] != "kafka_reliability"
    assert {t["knowledge_id"]: t["sources"] for t in before["targets"]} == {t["knowledge_id"]: t["sources"] for t in after["targets"]}
