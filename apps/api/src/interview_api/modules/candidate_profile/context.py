"""Call-local Profile context; atomic evidence and database provenance stay intact.

Report fields are untrusted grouping hints, never candidate facts. Classification
requires exact normalized text anchors. No embeddings, fuzzy matching or new text.
"""
from copy import deepcopy
from dataclasses import dataclass
import re

from interview_api.modules.candidate_profile.evidence import HEADINGS, ProfileEvidenceError, literal_failure_diagnostics
from interview_api.modules.candidate_profile.schemas import CandidateProfileDelta
from interview_api.modules.public_career.fingerprints import normalize_text


CONTEXT_VERSION = "1.0.0"
CATEGORIES = ("skills", "projects", "education", "work_experience", "internship_experience", "strengths")
SECTIONS = {"projects", "education", "skills", "work", "internship", "general"}
_HEADINGS = {alias for aliases in HEADINGS.values() for alias in aliases}
_CONTACT = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d)")


def _facts(snapshot):
    if snapshot.get("candidate_summary"):
        yield snapshot["candidate_summary"]
    for category in CATEGORIES:
        yield from snapshot.get(category, [])


def _strings(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _exact_anchor(value, excerpt, minimum):
    value, excerpt = normalize_text(value), normalize_text(excerpt)
    return (len(value) >= minimum and
            (value in excerpt or (len(excerpt) >= 12 and excerpt in value)))


def grouped_catalog(catalog, report):
    """Return a derived catalog and groups; never mutate source rows/caller data.

    Unique identity anchors outrank descriptive anchors. Shared/contradictory
    anchors remain general. Gaps are filled only BETWEEN two anchors of the same
    project/work group; competing anchors, headings and identity/contact lines
    are barriers. Report technology names alone cannot establish project usage.
    """
    report = report if isinstance(report, dict) else {}
    ordered = sorted(catalog.values(), key=lambda e: (
        e.get("resume_id", 0), e.get("start_offset", 0), e.get("end_offset", 0), e["key"]))
    result = {item["key"]: deepcopy(item) for item in ordered}
    basic = report.get("basic_info") or {}
    names = {normalize_text(v) for v in _strings(basic.get("name")) if v.strip()} if isinstance(basic, dict) else set()
    descriptors = []
    group_technologies = {}
    for field, section, prefix, identity in (
        ("projects", "projects", "P", "name"),
        ("education", "education", "D", "school"),
        ("internships", "internship", "I", "company"),
        ("work_experience", "work", "W", "company"),
    ):
        values = report.get(field) or []
        if not isinstance(values, list):
            continue
        for index, item in enumerate(values, 1):
            if not isinstance(item, dict):
                continue
            anchors = [(v, 3, f"{field}.{index-1}.{identity}", 4) for v in _strings(item.get(identity))]
            for name in ("description", "key_contributions", "quantitative_results", "responsibilities"):
                anchors += [(v, 2, f"{field}.{index-1}.{name}", 12) for v in _strings(item.get(name))]
            if section == "education":
                anchors += [(v, 1, f"{field}.{index-1}.major", 4) for v in _strings(item.get("major"))]
            descriptors.append((f"{prefix}{index:02}", section, anchors))
            group_technologies[f"{prefix}{index:02}"] = _strings(item.get("tech_stack"))
    skills = report.get("skills") or {}
    skill_labels = (_strings(skills) if isinstance(skills, list) else
                    [s for values in skills.values() for s in _strings(values)] if isinstance(skills, dict) else [])
    assignments, basis, blocked, ambiguous = {}, {}, set(), set()
    headings = [normalize_text(item["excerpt"]).strip(" #:：-•") in _HEADINGS for item in ordered]
    fallback_sections = {}
    active_section = "general"
    for index, item in enumerate(ordered):
        key, excerpt = item["key"], item["excerpt"]
        normalized = normalize_text(excerpt)
        if headings[index]:
            # A burst of adjacent PDF headings has no reliable forward scope.
            adjacent = ((index > 0 and headings[index - 1]) or
                        (index + 1 < len(ordered) and headings[index + 1]))
            active_section = "general" if adjacent else next(
                section for section, aliases in HEADINGS.items()
                if normalized.strip(" #:：-•") in aliases)
        fallback_sections[key] = active_section
        publication = any(_exact_anchor(value, excerpt, 12) for value in _strings(report.get("publications")))
        if headings[index] or _CONTACT.search(excerpt) or normalized in names or publication:
            blocked.add(index)
            assignments[key], basis[key] = ("G", "general"), ["heading_or_identity_or_contact"]
            continue
        hits = {}
        for group, section, anchors in descriptors:
            matched = [(weight, path) for value, weight, path, minimum in anchors if _exact_anchor(value, excerpt, minimum)]
            if matched:
                hits[group] = (section, max(weight for weight, _ in matched), sorted({path for _, path in matched}))
        if hits:
            highest = max(hit[1] for hit in hits.values())
            winners = [(group, hit) for group, hit in hits.items() if hit[1] == highest]
            if len(winners) == 1:
                group, (section, _, paths) = winners[0]
                assignments[key], basis[key] = (group, section), paths
            else:
                ambiguous.add(index)
                assignments[key], basis[key] = ("G", "general"), ["ambiguous_exact_anchors"]
    # Span fill is deliberately bounded by same-group anchors, not report order
    # or a preceding heading at the end of a PDF's extracted reading order.
    for group, section, group_anchors in descriptors:
        if section not in {"projects", "work", "internship"}:
            continue
        anchors = [i for i, item in enumerate(ordered) if assignments.get(item["key"]) == (group, section)]
        for left, right in zip(anchors, anchors[1:]):
            interval = range(left + 1, right)
            if any(i in blocked or i in ambiguous or
                   (ordered[i]["key"] in assignments and assignments[ordered[i]["key"]] != (group, section)) for i in interval):
                continue
            for i in interval:
                item = ordered[i]
                if item.get("resume_id") != ordered[left].get("resume_id"):
                    continue
                # Unknown intervening text is NOT automatically a project fact.
                # Only exact report continuations or a multi-term technology
                # list can be attached within this already bounded span.
                normalized = normalize_text(item["excerpt"])
                continuation = len(normalized) >= 4 and any(
                    normalized in normalize_text(value) for value, weight, path, minimum in group_anchors
                    if len(normalize_text(value)) >= minimum)
                technologies = {normalize_text(v) for v in group_technologies[group] if normalize_text(v)}
                technical_list = len({v for v in technologies if v in normalized}) >= 2
                if continuation or technical_list:
                    assignments[item["key"]], basis[item["key"]] = (group, section), ["bounded_exact_continuation_or_technology_list"]
    for item in ordered:
        key = item["key"]
        if key not in assignments:
            section = fallback_sections[key]
            section = section if section in SECTIONS else "general"
            # Exact standalone skill hints may classify mentions, NEVER create
            # project/work evidence from a technology label in the report.
            if section == "general" and any(_exact_anchor(label, item["excerpt"], 2) for label in skill_labels):
                section = "skills"
            assignments[key] = (f"RAW_{section}", section)
            basis[key] = ["validated_heading_scope" if section == fallback_sections[key] else "exact_skill_mention"]
        group, section = assignments[key]
        result[key]["source_section"] = item.get("section")
        result[key]["section"] = section
        result[key]["group_id"] = group
        result[key]["grouping_basis"] = basis[key]
    return result


def apply_section_map(catalog, sections):
    """Use a server-generated/frozen classification with real immutable evidence.

    This map is not accepted from any model or public request. Ownership/excerpts
    still come from the DB catalog and the unchanged evidence validator.
    """
    result = deepcopy(catalog)
    for key, section in sections.items():
        if section not in SECTIONS:
            raise ValueError("INVALID_PROFILE_EVIDENCE_GROUP")
        if key in result and result[key]["kind"] == "RESUME":
            result[key]["source_section"] = result[key]["section"]
            result[key]["section"] = section
    return result


@dataclass(frozen=True)
class ProfileContext:
    payload: dict
    catalog: dict
    alias_to_key: dict[str, str]

    def resolve_selection(self, selection):
        """Exact provenance, literal labels, one untouched representative excerpt.

        Preserve ALL selected links. Select the longest cited excerpt (ties use
        stable source position/key); for skills select among label-containing
        excerpts. Never concatenate, paraphrase, or repair the model's citations.
        User-confirmed correction snapshots bypass this model-boundary adapter.
        """
        snapshot = {"unknowns": list(selection.unknowns)}
        aliases = {key: alias for alias, key in self.alias_to_key.items()}
        for category in CATEGORIES[:-1]:
            snapshot[category] = []
            for index, selected in enumerate(getattr(selection, category)):
                path = f"{category}.{index}"
                unknown = [alias for alias in selected.evidence_ids if alias not in self.alias_to_key]
                if unknown:
                    raise ProfileEvidenceError("UNKNOWN_CANDIDATE_EVIDENCE", {
                        "field_path": path + ".evidence_ids", "validation_rule": "CALL_LOCAL_EVIDENCE_ALIAS",
                        "unknown_aliases": unknown})
                keys = sorted({self.alias_to_key[alias] for alias in selected.evidence_ids}, key=lambda key: (
                    self.catalog[key].get("resume_id", 0), self.catalog[key].get("start_offset", 0),
                    self.catalog[key].get("end_offset", 0), key))
                supported = [key for key in keys if normalize_text(selected.label) in normalize_text(self.catalog[key]["excerpt"])]
                if not supported:
                    raise ProfileEvidenceError("UNSUPPORTED_CANDIDATE_FACT", literal_failure_diagnostics(
                        selected.label, category=category, path=path, field="label", keys=keys,
                        evidence=self.catalog, aliases=aliases))
                choices = supported if category == "skills" else keys
                representative = min(choices, key=lambda key: (
                    -len(self.catalog[key]["excerpt"]), self.catalog[key].get("resume_id", 0),
                    self.catalog[key].get("start_offset", 0), self.catalog[key].get("end_offset", 0), key))
                fact = dict(label=selected.label, statement=self.catalog[representative]["excerpt"], evidence_ids=keys)
                if category == "skills":
                    fact["evidence_state"] = selected.proposed_evidence_state
                snapshot[category].append(fact)
        return CandidateProfileDelta.model_validate(snapshot)

    def resolve_aliases(self, delta):
        resolved = delta.model_copy(deep=True)
        for fact in _facts(resolved.model_dump()):
            # Verify all references before mutating the typed result.
            unknown = [alias for alias in fact["evidence_ids"] if alias not in self.alias_to_key]
            if unknown:
                raise ProfileEvidenceError("UNKNOWN_CANDIDATE_EVIDENCE", {
                    "validation_rule": "CALL_LOCAL_EVIDENCE_ALIAS", "unknown_aliases": unknown})
        facts = ([resolved.candidate_summary] if resolved.candidate_summary else [])
        facts += [fact for category in CATEGORIES for fact in getattr(resolved, category)]
        for fact in facts:
            fact.evidence_ids = [self.alias_to_key[alias] for alias in fact.evidence_ids]
        return resolved

    def audit(self):
        return {"context_version": CONTEXT_VERSION, "alias_to_key": self.alias_to_key,
                "section_map": {key: item["section"] for key, item in self.catalog.items()},
                "grouping_basis": {key: item["grouping_basis"] for key, item in self.catalog.items()}}


def build_profile_context(catalog, report, current_profile=None):
    derived = grouped_catalog(catalog, report)
    alias_to_key = {f"E{index:02}": key for index, key in enumerate(derived, 1)}
    key_to_alias = {key: alias for alias, key in alias_to_key.items()}
    groups = {}
    for key, item in derived.items():
        group = groups.setdefault(item["group_id"], dict(section=item["section"], evidence_ids=[]))
        group["evidence_ids"].append(key_to_alias[key])
    current = deepcopy(current_profile)
    if current:
        # Legacy display synthesis is not extraction input or stable evidence.
        current["candidate_summary"], current["strengths"] = None, []
        for fact in _facts(current):
            # Older evidence is context only; never expose another call's real
            # keys or make uncited historical statements eligible as new facts.
            fact["evidence_ids"] = [key_to_alias[key] for key in fact["evidence_ids"] if key in key_to_alias]
    payload = dict(
        resume_evidence=[dict(evidence_id=alias, section=derived[key]["section"], excerpt=derived[key]["excerpt"])
                         for alias, key in alias_to_key.items()],
        evidence_groups=list(groups.values()), current_profile=current,
    )
    return ProfileContext(payload, derived, alias_to_key)
