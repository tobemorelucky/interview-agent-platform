"""Requirement reference identity: external labels are mapped once, explicitly.

Two identities must never be conflated:

* the **external label** an imported dataset uses ("R2", "req-rag");
* the **internal identity** the database requires: a positive integer that
  ``interview_session_questions.job_requirement_id`` can reference.

Mapping happens at import time (:func:`normalize_external_requirements`) and is
recorded next to the row (``external_ref``, and optionally ``jd_fingerprint``).
The interview runtime only *validates* (:func:`validate_requirement_refs`): it
never coerces a label into an integer, because a wrong coercion silently binds a
question to an unrelated requirement while the persisted record still looks
legitimate.

A numeric string such as ``"2"`` is refused rather than parsed: it is impossible
to tell a label from an identity, and guessing is exactly the failure this module
exists to prevent.
"""
import re

from interview_api.core.exceptions import AppError

# Human/import labels: a letter first, then a bounded set of separators.
LABEL = re.compile(r'^[A-Za-z][A-Za-z0-9_.:-]{0,31}$')


def _is_id(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _fail(code, message):
    raise AppError(code, message, 422)


def validate_requirement_refs(rows, *, jd_fingerprint=None, allowed_ids=None):
    """Reject invalid / duplicate / out-of-scope requirement identities.

    Returns the integer ids in row order. Raises :class:`AppError` (422) before
    any model call can be made, so an invalid import costs no tokens.
    """
    ids, seen = [], set()
    for index, row in enumerate(rows or [], start=1):
        value = row.get('id') if isinstance(row, dict) else row
        if not _is_id(value) or value < 1:
            _fail('REQUIREMENT_REF_TYPE_INVALID',
                  f'岗位要求第 {index} 条的 id 不是正整数（{type(value).__name__}: {value!r}）。'
                  '外部数据集的字符串标识必须在导入阶段用 normalize_external_requirements '
                  '建立明确映射；运行时不会自动转换。')
        if value in seen:
            _fail('REQUIREMENT_REF_DUPLICATE', f'岗位要求 id 重复：{value}。同一 id 不能指向两条要求。')
        seen.add(value)
        if isinstance(row, dict):
            label = row.get('external_ref')
            if label is not None and (not isinstance(label, str) or not LABEL.match(label)):
                _fail('REQUIREMENT_REF_LABEL_INVALID',
                      f'岗位要求 {value} 的外部标识不合法：{label!r}。')
            recorded = row.get('jd_fingerprint')
            if jd_fingerprint and recorded and recorded != jd_fingerprint:
                _fail('REQUIREMENT_REF_OUT_OF_SCOPE',
                      f'岗位要求 {value} 记录的是另一版 JD（{str(recorded)[:12]}…），'
                      f'不属于本场冻结版本（{str(jd_fingerprint)[:12]}…）。')
        ids.append(value)
    if allowed_ids is not None:
        outside = sorted(set(ids) - set(allowed_ids))
        if outside:
            _fail('REQUIREMENT_REF_OUT_OF_SCOPE',
                  f'岗位要求 {outside} 不属于当前 JD 版本，未猜测绑定。')
    return ids


def normalize_external_requirements(rows, *, jd_fingerprint=None):
    """Map external labels to internal integer ids once, reversibly.

    Returns ``(rows, mapping)`` where every row carries an integer ``id`` and the
    original label in ``external_ref``; ``mapping`` is ``{label: id}`` for audit.
    Ids are assigned in the order of appearance, so the same input always yields
    the same mapping. Mixed or ambiguous input is refused, never coerced.
    """
    rows = [dict(row) if isinstance(row, dict) else {'statement': str(row)} for row in rows or []]
    if not rows:
        return [], {}
    values = [row.get('id') for row in rows]
    if all(_is_id(value) and value >= 1 for value in values):
        validate_requirement_refs(rows, jd_fingerprint=jd_fingerprint)
        mapping = {(row.get('external_ref') or str(row['id'])): row['id'] for row in rows}
        return rows, mapping
    if any(_is_id(value) for value in values):
        _fail('REQUIREMENT_REF_MIXED',
              '岗位要求 id 同时出现整数与字符串标识，无法确定映射，未做强制转换。')
    labels = []
    for value in values:
        if not isinstance(value, str):
            _fail('REQUIREMENT_REF_LABEL_INVALID', f'外部标识不合法：{value!r}。')
        if value.isdigit():
            _fail('REQUIREMENT_REF_AMBIGUOUS',
                  f'外部标识 {value!r} 全为数字，无法区分“标签”与“内部 id”，未做猜测转换。')
        if not LABEL.match(value):
            _fail('REQUIREMENT_REF_LABEL_INVALID', f'外部标识不合法：{value!r}。')
        if value in labels:
            _fail('REQUIREMENT_REF_DUPLICATE', f'外部标识重复：{value!r}。')
        labels.append(value)
    mapping, out = {}, []
    for index, row in enumerate(rows, start=1):
        row['external_ref'] = row['id']
        row['id'] = index
        if jd_fingerprint:
            row['jd_fingerprint'] = jd_fingerprint
        mapping[row['external_ref']] = index
        out.append(row)
    return out, mapping
