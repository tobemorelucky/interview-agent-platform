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
