"""Detached model DTO and exact-input reuse identity. Canonical snapshots stay intact."""
from copy import deepcopy
from interview_api.modules.candidate_profile.evidence import digest

# Bumped whenever the projection below changes shape. It is recorded with the run
# so a bounded model view is never confused with an older full-matrix input.
PROJECTION_VERSION = "2.1.0"

# The prompt lets the model pick at most one association per requirement, so two
# candidates per requirement is enough for a real choice. The remaining
# associations stay in the frozen snapshot and drive the deterministic matrix,
# the UI and recommendation resolution.
MAX_SUPPORTS_PER_REQUIREMENT = 2

_SUPPORT_FIELDS = ("support_ref", "candidate_fact_id", "fact_ref", "status", "matched_topics", "explanation")
_MATRIX_FIELDS = ("id", "requirement_ref", "statement", "importance", "status", "message", "matched_topics")
_CANDIDATE_FIELDS = ("job_id", "company_name", "title", "job_family", "campus_type", "tags",
                     "eligibility_status", "unknown_constraints", "requirements")


def support_ref(row, support):
    """The one token a model needs to select an association.

    Run 5 failed with finish_reason=length because the old contract made the
    model re-emit server-owned text — 64-char evidence keys and the exact
    explanation — for 20 jobs; the minimum compliant output was ~34KB against a
    6000-token cap. A deterministic ref derived from the frozen matrix removes
    that copying while keeping every association verifiable: the resolver
    recomputes the ref from the full snapshot, so a model can only ever point at
    associations that actually exist.
    """
    from interview_api.modules.candidate_profile.evidence import digest
    return "support:" + digest([row["id"], support["fact_ref"]])[:12]


def reuse_identity(snapshot):
    value = deepcopy(snapshot)
    value['request'].pop('request_key', None)
    # Explicit version selection and "current" may resolve to the SAME pinned version.
    # The real version and facts are already part of snapshot.profile, never ignored.
    value['request'].pop('candidate_profile_version_id', None)
    return digest(value)


def _bounded_matrix(candidate):
    """Keep every requirement row; bound only how many associations each row offers.

    ``evidence_ids`` stay out of the projected view: the model no longer copies
    them into its output, and the frozen snapshot keeps the full mapping for the
    resolver. Repeating 64-char keys for every association was the single
    largest reason the input and the expected output both blew their budgets.
    """
    rows = []
    for row in candidate.get('evidence_matrix') or []:
        entry = {key: row[key] for key in _MATRIX_FIELDS if key in row}
        supports = [dict(s) for s in (row.get('supports') or [])] if row.get('status') != 'NOT_APPLICABLE' else []
        # Strongest first so the cap never hides the only SUPPORTED association.
        supports.sort(key=lambda s: (0 if s.get('status') == 'SUPPORTED' else 1, s.get('candidate_fact_id') or 0))
        entry['supports'] = [{**{k: s[k] for k in _SUPPORT_FIELDS if k in s},
                              'support_ref': support_ref(row, s)}
                             for s in supports[:MAX_SUPPORTS_PER_REQUIREMENT]]
        rows.append(entry)
    return rows


def model_context(snapshot):
    """Bounded model view: same evidence, only one deterministic copy of it.

    Server-computed ranking artifacts (ranking_components / job_terms / topic
    overlap / fingerprints) are dropped because their inputs are already in
    this payload; repeating them inflated the envelope past the input budget
    without adding evidence the model could use.
    """
    value = deepcopy(snapshot)
    value.pop('excluded_candidates', None)
    value.pop('input_fingerprint', None)
    value['profile']['evidence'] = [dict(key=e['key'], kind=e.get('kind'), section=e.get('section'),
        excerpt=e['excerpt']) for e in value['profile']['evidence']]
    candidates = []
    for candidate in value['candidates']:
        entry = {key: candidate[key] for key in _CANDIDATE_FIELDS if key in candidate}
        components = candidate.get('ranking_components') or {}
        # The ceiling is a constraint the prompt must respect; the counts let the
        # model see how much real support exists without the per-row duplicates.
        entry['fit_band_ceiling'] = components.get('fit_band_ceiling')
        entry['evidence_counts'] = components.get('required_counts')
        entry['evidence_matrix'] = _bounded_matrix(candidate)
        candidates.append(entry)
    value['candidates'] = candidates
    value['projection_version'] = PROJECTION_VERSION
    return value
