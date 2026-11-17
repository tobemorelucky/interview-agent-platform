from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class MatchFilters(StrictModel):
    campus_types: list[str] = Field(default_factory=list, max_length=10)
    job_families: list[str] = Field(default_factory=list, max_length=10)
    locations: list[str] = Field(default_factory=list, max_length=10)
    tags: list[str] = Field(default_factory=list, max_length=20)
    exclude_company_ids: list[int] = Field(default_factory=list, max_length=1000)
    exclude_job_ids: list[int] = Field(default_factory=list, max_length=1000)
    exclude_tags: list[str] = Field(default_factory=list, max_length=20)


class MatchRequest(StrictModel):
    request_key: str = Field(min_length=1, max_length=100)
    candidate_profile_version_id: int | None = None
    filters: MatchFilters = Field(default_factory=MatchFilters)


JobRef = Annotated[str, Field(pattern=r"^job:[1-9][0-9]*$")]
AlignmentRef = Annotated[str, Field(pattern=r"^alignment:[1-9][0-9]*$")]


class AlignmentReason(StrictModel):
    alignment_id: AlignmentRef
    candidate_fact_id: Annotated[str, Field(pattern=r"^fact:[1-9][0-9]*$")]
    job_requirement_id: Annotated[str, Field(pattern=r"^requirement:[1-9][0-9]*$")]
    evidence_ids: list[str] = Field(min_length=1, max_length=30)
    # Explanation must cite one frozen, program-generated support statement. The
    # model chooses associations, not new skill claims or paraphrased evidence.
    explanation: str = Field(min_length=1, max_length=4000)


class MatchRecommendationArtifact(StrictModel):
    job_id: JobRef
    fit_band: Literal["STRONG_MATCH", "GOOD_MATCH", "EXPLORATORY", "UNCERTAIN"]
    matched_strengths: list[AlignmentReason] = Field(max_length=30)
    alignment_reasons: list[AlignmentReason] = Field(max_length=30)
    current_evidence_gaps: list[AlignmentRef] = Field(max_length=100)
    preparation_focus: list[AlignmentRef] = Field(max_length=100)
    unknowns_to_confirm: list[str] = Field(max_length=30)


class MatchArtifact(StrictModel):
    ordered_candidate_job_ids: list[JobRef] = Field(min_length=1, max_length=50)
    recommendations: list[MatchRecommendationArtifact] = Field(min_length=1, max_length=50)


SupportRef = Annotated[str, Field(pattern=r"^support:[0-9a-f]{12}$")]


class SupportSelection(StrictModel):
    """One association, referenced — never re-typed.

    The server owns the fact, the requirement, the evidence keys and the exact
    explanation. The model only chooses which association supports this job.
    """
    support_ref: SupportRef


class MatchRecommendationV2(StrictModel):
    job_id: JobRef
    fit_band: Literal["STRONG_MATCH", "GOOD_MATCH", "EXPLORATORY", "UNCERTAIN"]
    matched_strengths: list[SupportSelection] = Field(max_length=3)
    alignment_reasons: list[SupportSelection] = Field(max_length=3)
    current_evidence_gaps: list[AlignmentRef] = Field(max_length=100)
    preparation_focus: list[AlignmentRef] = Field(max_length=100)
    unknowns_to_confirm: list[str] = Field(max_length=30)


class MatchArtifactV2(StrictModel):
    ordered_candidate_job_ids: list[JobRef] = Field(min_length=1, max_length=50)
    recommendations: list[MatchRecommendationV2] = Field(min_length=1, max_length=50)
