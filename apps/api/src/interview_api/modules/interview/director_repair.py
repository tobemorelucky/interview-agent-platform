"""One narrow Director reference-selection repair; never rewrites facts or provenance."""
from dataclasses import dataclass
from pydantic import ValidationError
from interview_api.modules.interview.adaptive import AdaptiveSelection, uses_pairs


@dataclass(frozen=True)
class SupportingRefsRepair:
    payload: dict

    def _constraints(self, original):
        if not uses_pairs(self.payload) or not isinstance(original, dict):
            return None
        adaptive = self.payload['adaptive']
        refs = original.get('supporting_refs')
        if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
            return None
        if set(refs) - set(adaptive['allowed_refs']):
            return None
        pair = next((c for c in adaptive['candidates'] if c['candidate_id'] == original.get('candidate_id')), None)
        if not pair or original.get('strategy') not in pair['strategies']:
            return None
        source = pair['source']['ref']
        if source not in refs:
            return None
        current = [r for r in (adaptive.get('current_answer_ref'), adaptive.get('current_evaluation_ref')) if r and r in refs]
        if self.payload.get('answer') is not None and not current:
            return None
        limit = AdaptiveSelection.model_json_schema()['properties']['supporting_refs']['maxItems']
        return dict(policy='DIRECTOR_SUPPORT_SUBSET_V1', field='supporting_refs', max_items=limit,
                    selectable_refs=refs, required_source_ref=source, required_current_any=current)

    def prepare(self, original, error):
        if not isinstance(error, ValidationError):
            return None
        errors = error.errors()
        if len(errors) != 1 or errors[0]['loc'] != ('supporting_refs',) or errors[0]['type'] != 'too_long':
            return None
        return self._constraints(original)

    def validate(self, original, repaired):
        plan = self._constraints(original)
        if not plan or not isinstance(repaired, dict):
            raise ValueError('Reference subset repair has no valid original scope')
        if {k:v for k,v in original.items() if k != 'supporting_refs'} != {k:v for k,v in repaired.items() if k != 'supporting_refs'}:
            raise ValueError('Reference subset repair changed the decision or question')
        refs = repaired['supporting_refs']
        if len(set(refs)) != len(refs) or set(refs) - set(original['supporting_refs']):
            raise ValueError('Reference subset repair added or duplicated references')
        if plan['required_source_ref'] not in refs:
            raise ValueError('Reference subset repair dropped the selected source')
        if plan['required_current_any'] and not set(refs).intersection(plan['required_current_any']):
            raise ValueError('Reference subset repair dropped current answer/evaluation support')
        return dict(policy=plan['policy'], original_count=len(original['supporting_refs']), retained_count=len(refs),
                    original_refs=original['supporting_refs'], retained_refs=refs,
                    candidate_id=original['candidate_id'], strategy=original['strategy'])


# The hosted model intermittently flattens the nested question draft into
# sibling keys. Every string then already exists in the malformed output, but at
# a different path, so the generic "facts keep their claim path" check rejects
# the very structural repair it asks for. This policy authorises exactly that
# normalisation and nothing else: the repaired draft must equal a draft assembled
# from the original strings, and every other field must be untouched.
FLAT_QUESTION_KEYS = ("proposed_question_dimension", "proposed_question_difficulty",
                      "proposed_question_expected_points", "proposed_question_meta")


@dataclass(frozen=True)
class FlattenedQuestionRepair:
    payload: dict

    def _draft(self, original):
        if not isinstance(original, dict) or not uses_pairs(self.payload):
            return None
        question = original.get("proposed_question")
        if not isinstance(question, str) or not question.strip():
            return None
        if not any(key in original for key in FLAT_QUESTION_KEYS):
            return None
        meta = original.get("proposed_question_meta")
        meta = meta if isinstance(meta, dict) else {}
        draft = dict(question=question,
            expected_points=original.get("proposed_question_expected_points", meta.get("expected_points")),
            dimension=original.get("proposed_question_dimension", meta.get("dimension")),
            difficulty=original.get("proposed_question_difficulty", meta.get("difficulty")))
        if not isinstance(draft["expected_points"], list) or not draft["expected_points"]:
            return None
        if not all(isinstance(point, str) and point for point in draft["expected_points"]):
            return None
        if not isinstance(draft["dimension"], str) or not draft["dimension"]:
            return None
        if draft["difficulty"] not in ("EASY", "MEDIUM", "HARD"):
            return None
        return draft

    def prepare(self, original, error):
        if not isinstance(error, ValidationError):
            return None
        errors = error.errors()
        if not errors or any(err.get("loc", (None,))[0] not in
                             ("proposed_question",) + FLAT_QUESTION_KEYS for err in errors):
            return None
        draft = self._draft(original)
        return dict(policy="FLATTENED_QUESTION_DRAFT_V1", draft=draft) if draft else None

    def validate(self, original, repaired):
        draft = self._draft(original)
        if not draft or not isinstance(repaired, dict):
            raise ValueError("No flattened question draft to normalise")
        rest = {k: v for k, v in original.items() if k != "proposed_question" and k not in FLAT_QUESTION_KEYS}
        other = {k: v for k, v in repaired.items() if k != "proposed_question"}
        if rest != other:
            raise ValueError("Flattened question repair changed another field")
        if repaired.get("proposed_question") != draft:
            raise ValueError("Flattened question repair invented or altered question content")
        return dict(policy="FLATTENED_QUESTION_DRAFT_V1", fields=sorted(draft))


@dataclass(frozen=True)
class MainSelectionRepair:
    """At most one repair, and only for a malformed shape the original supports."""

    payload: dict

    def _policies(self):
        return (FlattenedQuestionRepair(self.payload), SupportingRefsRepair(self.payload))

    def prepare(self, original, error):
        for policy in self._policies():
            plan = policy.prepare(original, error)
            if plan is not None:
                return plan
        return None

    def validate(self, original, repaired):
        reasons = []
        for policy in self._policies():
            try:
                return policy.validate(original, repaired)
            except ValueError as exc:
                reasons.append(str(exc))
        raise ValueError("; ".join(reasons))
