"""Requirement reference identity: labels are mapped once, never coerced at runtime.

The database contract for ``interview_session_questions.job_requirement_id`` is a
positive integer. An imported dataset that carries ``"R2"`` must be mapped at
import time, explicitly and reversibly, so that a wrong binding cannot hide behind
a plausible-looking session.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from interview_api.core.exceptions import AppError
from interview_api.modules.interview.context import build_context, target_requirements
from interview_api.modules.interview.schemas import CreateSessionRequest
from interview_api.modules.job_target.models import UserJobTarget
from interview_api.modules.job_target.requirement_refs import (
    normalize_external_requirements, validate_requirement_refs,
)
from test_job_target import create_target, seed_user


def test_labels_map_once_in_order_and_stay_reversible():
    rows = [{'id': 'R1', 'statement': 'a'}, {'id': 'R2', 'statement': 'b'}, {'id': 'R3', 'statement': 'c'}]
    out, mapping = normalize_external_requirements(rows, jd_fingerprint='fp-1')
    assert [row['id'] for row in out] == [1, 2, 3]
    assert [row['external_ref'] for row in out] == ['R1', 'R2', 'R3']
    assert mapping == {'R1': 1, 'R2': 2, 'R3': 3}
    assert all(row['jd_fingerprint'] == 'fp-1' for row in out)
    again, mapping_again = normalize_external_requirements(rows, jd_fingerprint='fp-1')
    assert again == out and mapping_again == mapping  # deterministic, no id drift


def test_numeric_string_is_ambiguous_and_refused():
    with pytest.raises(AppError) as error:
        normalize_external_requirements([{'id': '2', 'statement': 'a'}])
    assert error.value.code == 'REQUIREMENT_REF_AMBIGUOUS'


def test_mixed_identities_are_refused_instead_of_guessed():
    with pytest.raises(AppError) as error:
        normalize_external_requirements([{'id': 1, 'statement': 'a'}, {'id': 'R2', 'statement': 'b'}])
    assert error.value.code == 'REQUIREMENT_REF_MIXED'


def test_duplicate_labels_and_duplicate_ids_are_refused():
    with pytest.raises(AppError) as error:
        normalize_external_requirements([{'id': 'R1'}, {'id': 'R1'}])
    assert error.value.code == 'REQUIREMENT_REF_DUPLICATE'
    with pytest.raises(AppError) as error:
        validate_requirement_refs([{'id': 2}, {'id': 2}])
    assert error.value.code == 'REQUIREMENT_REF_DUPLICATE'


@pytest.mark.parametrize('value', ['R2', None, 0, -1, 1.5, True])
def test_runtime_validation_rejects_non_identity_values(value):
    with pytest.raises(AppError) as error:
        validate_requirement_refs([{'id': value, 'statement': 'a'}])
    assert error.value.code == 'REQUIREMENT_REF_TYPE_INVALID'
    assert 'normalize_external_requirements' in error.value.message


def test_runtime_validation_rejects_out_of_scope_and_foreign_versions():
    with pytest.raises(AppError) as error:
        validate_requirement_refs([{'id': 3}], allowed_ids={1, 2})
    assert error.value.code == 'REQUIREMENT_REF_OUT_OF_SCOPE'
    with pytest.raises(AppError) as error:
        validate_requirement_refs([{'id': 1, 'jd_fingerprint': 'other'}], jd_fingerprint='current')
    assert error.value.code == 'REQUIREMENT_REF_OUT_OF_SCOPE'


def test_integer_identities_pass_unchanged():
    rows = [{'id': 1, 'external_ref': 'R1'}, {'id': 2}]
    assert validate_requirement_refs(rows, jd_fingerprint='fp', allowed_ids={1, 2}) == [1, 2]


def test_target_requirements_rejects_a_legacy_string_id_before_any_model_call():
    target = SimpleNamespace(content_fingerprint='fp-1', requirements_json=[
        {'id': 'R1', 'statement': '熟悉 Python', 'importance': 'REQUIRED'}])
    with pytest.raises(AppError) as error:
        target_requirements(target)
    assert error.value.code == 'REQUIREMENT_REF_TYPE_INVALID'


def test_private_target_with_label_ids_fails_before_a_session_exists(database_url):
    """No session row, no agent run, no admission: the import is refused up front."""
    async def run():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            user_id = await seed_user(factory)
            target_id = await create_target(factory, user_id)
            async with factory() as db:
                target = await db.get(UserJobTarget, target_id)
                legacy = [dict(row, id=f'R{index}') for index, row in
                          enumerate(target.requirements_json, start=1)]
                fingerprint = target.content_fingerprint
                await db.execute(text(
                    "UPDATE user_job_targets SET requirements_json = CAST(:j AS jsonb) WHERE id = :i"),
                    {'j': json.dumps(legacy, ensure_ascii=False), 'i': target_id})
                await db.commit()
            async with factory() as db:
                sessions_before = await db.scalar(text("SELECT count(*) FROM interview_sessions"))
                runs_before = await db.scalar(text("SELECT count(*) FROM experience_agent_runs"))
                admissions_before = await db.scalar(text("SELECT count(*) FROM model_admissions"))
            async with factory() as db:
                with pytest.raises(AppError) as error:
                    await build_context(db, user_id, CreateSessionRequest(
                        job_target_id=target_id, question_count=3,
                        interviewer_skill_id='ai_agent_application', adaptive_interview=True))
                await db.rollback()
            assert error.value.code == 'REQUIREMENT_REF_TYPE_INVALID'
            async with factory() as db:
                assert await db.scalar(text("SELECT count(*) FROM interview_sessions")) == sessions_before
                assert await db.scalar(text("SELECT count(*) FROM experience_agent_runs")) == runs_before
                assert await db.scalar(text("SELECT count(*) FROM model_admissions")) == admissions_before

            # The explicit mapping is the only accepted repair, and it is stored.
            mapped, mapping = normalize_external_requirements(legacy, jd_fingerprint=fingerprint)
            assert mapping == {f'R{index}': index for index in range(1, len(legacy) + 1)}
            assert [row['id'] for row in mapped] == list(range(1, len(legacy) + 1))
            async with factory() as db:
                await db.execute(text(
                    "UPDATE user_job_targets SET requirements_json = CAST(:j AS jsonb) WHERE id = :i"),
                    {'j': json.dumps(mapped, ensure_ascii=False), 'i': target_id})
                await db.commit()
            async with factory() as db:
                context, _, _ = await build_context(db, user_id, CreateSessionRequest(
                    job_target_id=target_id, question_count=3,
                    interviewer_skill_id='ai_agent_application', adaptive_interview=True))
            assert [row['id'] for row in context['requirements']] == list(range(1, len(legacy) + 1))
            async with factory() as db:
                target = await db.get(UserJobTarget, target_id)
                assert target.requirements_json[0]['external_ref'] == 'R1'
        finally:
            await engine.dispose()
    asyncio.run(run())
