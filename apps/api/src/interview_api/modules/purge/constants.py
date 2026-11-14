"""Round 23 / L3: the state machine and the agreed purge-scope contract."""

# REQUESTED -> FENCED -> CLEANING -> VERIFYING -> COMPLETED
# FAILED is recoverable (retry keeps completed_steps); BLOCKED needs a human.
REQUESTED = "REQUESTED"
FENCED = "FENCED"
CLEANING = "CLEANING"
VERIFYING = "VERIFYING"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
BLOCKED = "BLOCKED"

STATES = (REQUESTED, FENCED, CLEANING, VERIFYING, COMPLETED, FAILED, BLOCKED)
# Any existing row is a fence. COMPLETED/FAILED/BLOCKED stay fenced too: a
# failed purge must never silently re-enable the resource.
FENCE_ACTIVE_STATES = STATES
TERMINAL_OK = COMPLETED

RESUME = "RESUME"
PRIVATE_JD = "PRIVATE_JD"
RESOURCE_TYPES = (RESUME, PRIVATE_JD)

# Bump this when the set of tables/columns changes. Stored on every request so
# the verifier can prove it checked exactly the scope that was cleaned.
POLICY_VERSION = "r23-l3-1"

# Steps run in this exact order. Database content is erased FIRST (transaction,
# reversible by rollback); only then the external object (irreversible) and
# then the best-effort cache. Never the other way round.
STEP_DB_CONTENT = "db_content_erased"
STEP_MINIO_OBJECT = "minio_object_removed"
STEP_REDIS_CACHE = "redis_cache_cleared"
STEP_VERIFIED = "independently_verified"
STEPS = (STEP_DB_CONTENT, STEP_MINIO_OBJECT, STEP_REDIS_CACHE, STEP_VERIFIED)

# Irreversible content replacement. NOT a soft delete: the original bytes are
# overwritten with a marker, not hidden behind a flag or moved aside.
# Short markers: several target columns are varchar(20). A marker longer than
# the column would abort the whole erase transaction with a truncation error.
ERASED_TEXT = "[PURGED]"
ERASED_JSON = {"erased": True}
ERASED_HASH = "erased"

# Diagnostics must never contain resource text. These are fixed, coded strings.
ERR_OBJECT_REMOVE = "MINIO_OBJECT_REMOVE_FAILED"
ERR_CACHE_CLEAR = "REDIS_CACHE_CLEAR_FAILED"
ERR_VERIFY = "INDEPENDENT_VERIFICATION_FAILED"
ERR_UNRESOLVED_MODEL = "UNRESOLVED_MODEL_OUTCOME"
ERR_DEPENDENCY = "UNSAFE_DEPENDENCY"
ERR_UNKNOWN = "UNEXPECTED_CLEANER_FAILURE"
