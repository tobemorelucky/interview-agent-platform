"""Detached model DTO and exact-input reuse identity. Canonical snapshots stay intact."""
from copy import deepcopy
from interview_api.modules.candidate_profile.evidence import digest


def reuse_identity(snapshot):
    value = deepcopy(snapshot)
    value['request'].pop('request_key', None)
    # Explicit version selection and "current" may resolve to the SAME pinned version.
    # The real version and facts are already part of snapshot.profile, never ignored.
    value['request'].pop('candidate_profile_version_id', None)
    return digest(value)


def model_context(snapshot):
    value = deepcopy(snapshot)
    value.pop('excluded_candidates', None)
    value['profile']['evidence'] = [dict(key=e['key'], kind=e.get('kind'), section=e.get('section'),
        excerpt=e['excerpt']) for e in value['profile']['evidence']]
    # All facts, eligibility, requirements and authoritative support associations remain.
    # Remove server-only fingerprints/location and duplicate identifiers, not their content.
    value.pop('input_fingerprint', None)
    for c in value['candidates']:
        c.pop('input_fingerprint', None)
        c.pop('jd_fingerprint', None)
    return value
