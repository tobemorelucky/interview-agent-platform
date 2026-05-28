"""Phase 4: Experience collection enums."""


class ExperiencePresetType:
    COMPANY = "COMPANY"
    JOB = "JOB"
    PLATFORM = "PLATFORM"


class ExperienceTaskStatus:
    PENDING = "PENDING"
    SEARCHING = "SEARCHING"
    FETCHING = "FETCHING"
    EXTRACTING = "EXTRACTING"
    ROUTING = "ROUTING"
    SCORING = "SCORING"
    DEDUPING = "DEDUPING"
    WAITING_REVIEW = "WAITING_REVIEW"
    APPROVED = "APPROVED"
    INDEXING = "INDEXING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ExperienceReviewMode:
    MANUAL = "MANUAL"
    AUTO_PUBLISH = "AUTO_PUBLISH"


class ExperienceSourceStatus:
    DISCOVERED = "DISCOVERED"
    FETCHED = "FETCHED"
    FETCH_FAILED = "FETCH_FAILED"
    EXTRACTED = "EXTRACTED"
    NOT_EXPERIENCE = "NOT_EXPERIENCE"
    WAITING_REVIEW = "WAITING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    INDEXED = "INDEXED"


class ExperienceReviewStatus:
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    AUTO_APPROVED = "AUTO_APPROVED"


class ExperienceReliabilityLevel:
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class InterviewQuestionIndexStatus:
    NOT_INDEXED = "NOT_INDEXED"
    PENDING = "PENDING"
    INDEXED = "INDEXED"
    FAILED = "FAILED"


class AnswerSource:
    ORIGINAL = "ORIGINAL"
    LLM_GENERATED = "LLM_GENERATED"
    HYBRID = "HYBRID"
