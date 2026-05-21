"""Worker configuration — reuses API Settings so both read the same .env file."""

from interview_api.core.config import settings

# Re-export the shared settings object so existing `from interview_worker.config import settings`
# imports continue to work without changes in other modules.
__all__ = ["settings"]
