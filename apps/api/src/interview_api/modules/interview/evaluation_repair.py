"""One quote-count repair, not an evaluation rewrite or semantic validator."""
from copy import deepcopy
from dataclasses import dataclass
import json
from pydantic import TypeAdapter, ValidationError

from interview_api.core.exceptions import AppError
from interview_api.modules.interview.adaptive import AdaptiveEvaluation, KnowledgeObservation, ShortText, resolve_observations


@dataclass(frozen=True)
class EvaluationQuotesRepair:
    payload: dict

    def prepare(self, original, error):
        if not isinstance(error, ValidationError):
            return None
        errors = error.errors()
        if len(errors) != 1 or errors[0]['loc'] != ('knowledge_observations', 0, 'answer_quotes') or errors[0]['type'] != 'too_long':
            return None
        observation = original['knowledge_observations'][0]
        quotes = observation['answer_quotes']
        try:
            TypeAdapter(list[ShortText]).validate_python(quotes)
        except ValidationError:
            return None
        if not all(isinstance(q, str) and q.strip() and q in self.payload['answer'] for q in quotes):
            return None
        # The sole structural error is already proven above. Check ALL original
        # quote/gap/target associations without accepting this overlength object.
        # construct is used only for this probe; final output must fully validate.
        probe = AdaptiveEvaluation.model_construct(**{**original,
            'knowledge_observations': [KnowledgeObservation.model_construct(**observation)]})
        try:
            resolve_observations(probe, self.payload)
        except AppError:
            return None
        limit = KnowledgeObservation.model_json_schema()['properties']['answer_quotes']['maxItems']
        return dict(policy='EVALUATION_QUOTE_SUBSET_V1', field=['knowledge_observations', 0, 'answer_quotes'],
                    max_items=limit, selectable_quotes=quotes)

    def validate(self, original, repaired):
        # Re-establish the original scope rather than trusting a model plan.
        try:
            AdaptiveEvaluation.model_validate(original)
        except ValidationError as error:
            plan = self.prepare(original, error)
        else:
            plan = None
        if plan is None:
            raise ValueError('Quote subset repair has no valid original scope')
        before, after = deepcopy(original), deepcopy(repaired)
        quotes = after['knowledge_observations'][0].pop('answer_quotes')
        before['knowledge_observations'][0].pop('answer_quotes')
        if json.dumps(before, sort_keys=True, ensure_ascii=False) != json.dumps(after, sort_keys=True, ensure_ascii=False):
            raise ValueError('Quote subset repair changed evaluation fields')
        if not quotes or len(quotes) > plan['max_items'] or len(set(quotes)) != len(quotes) or set(quotes) - set(plan['selectable_quotes']):
            raise ValueError('Quote subset repair must select existing literal evidence only')
        resolve_observations(AdaptiveEvaluation.model_validate(repaired), self.payload)
        # Counts and policy only: do not persist sensitive Answer excerpts.
        return dict(policy=plan['policy'], original_count=len(plan['selectable_quotes']), retained_count=len(quotes))
