"""Explicit model attribution: who is billed, on which route, in which database.

Three things were previously implicit and are now checked before a request is sent:

* **Owner and route** come from the caller's own context, not from a process-wide
  fallback. ``governed_call`` refuses to guess, because a guess is what produced
  ``route=legacy_llm`` admissions and let one user's scope leak into another's.
* **Ledger database** is the database the business row lives in. A scope whose
  session factory points somewhere else is rejected instead of quietly billing a
  different database — the failure mode that let an external harness write its
  admissions into the production ledger.
* **Request isolation** uses a ``ContextVar`` set for the duration of one call and
  reset afterwards, so concurrent tasks cannot overwrite each other's owner.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy.engine import make_url

from interview_api.core.config import settings
from interview_api.core.errors import AppError


@dataclass(frozen=True)
class ModelScope:
    factory: object
    owner: str
    user_id: int | None
    route: str
    ledger_database: str


_current: ContextVar[ModelScope | None] = ContextVar("model_scope", default=None)


def database_of(factory) -> str:
    """Read the database name from a session factory without opening a connection."""
    bind = getattr(factory, "kw", {}).get("bind") if isinstance(getattr(factory, "kw", None), dict) else None
    if bind is None:
        bind = getattr(factory, "bind", None)
    url = getattr(bind, "url", None)
    if url is None:
        raise AppError("MODEL_LEDGER_TARGET_UNKNOWN",
                       "模型账本目标无法从会话工厂解析，未发送请求", 500)
    return make_url(str(url)).database or ""


def expected_ledger_database() -> str:
    """The database this process declares as its ledger target.

    ``MODEL_LEDGER_DATABASE`` exists so a harness or worker that runs against a
    sandbox must say so; silence means "the configured DATABASE_URL", which is the
    only target a normal API or worker process has any business writing to.
    """
    configured = settings.model_ledger_database.strip()
    if configured:
        return configured
    return make_url(settings.database_url).database or ""


def assert_ledger_target(scope: ModelScope) -> None:
    expected = expected_ledger_database()
    if not expected:
        raise AppError("MODEL_LEDGER_TARGET_UNKNOWN",
                       "进程未声明模型账本目标，未发送请求", 500)
    if scope.ledger_database != expected:
        raise AppError("MODEL_LEDGER_TARGET_MISMATCH",
                       f"模型账本目标 {scope.ledger_database!r} 与进程声明 {expected!r} 不一致，未发送请求", 409)


def current_scope() -> ModelScope | None:
    return _current.get()


def ledger_factory(db=None):
    """A short-transaction factory on the same engine as an open session.

    When the caller holds a session (a request or a worker step), the ledger must
    be written through that session's engine: that is the database the business row
    lives in. Only a caller with no session at all falls back to the process
    factory, and the target check still has to agree with it.
    """
    if db is not None:
        try:
            bind = db.get_bind()
            engine = getattr(bind, "engine", bind)
            from sqlalchemy.ext.asyncio import async_sessionmaker
            return async_sessionmaker(engine, expire_on_commit=False)
        except Exception:  # pragma: no cover - only for exotic binds
            pass
    from interview_api.infrastructure.db.session import async_session_factory
    return async_session_factory


def require_scope() -> ModelScope:
    scope = _current.get()
    if scope is None:
        raise AppError("MODEL_SCOPE_REQUIRED",
                       "模型调用缺少明确的归属作用域（用户/路由/账本库），未发送请求", 403)
    return scope


@contextmanager
def model_scope_for(factory, *, route: str, user_id: int | None = None, owner: str | None = None,
                    ledger_database: str | None = None):
    """Bind owner/route/ledger target for exactly the enclosed model calls.

    ``ledger_database`` is normally derived from the factory's bind. A caller that
    wraps the factory (retry, outage injection, tenant routing) can name it
    explicitly; the gateway still refuses unless it equals the process target, so an
    explicit value cannot smuggle a different database in.
    """
    if factory is None:
        raise AppError("MODEL_SCOPE_REQUIRED", "模型调用缺少会话工厂，未发送请求", 403)
    owner = owner or (f"user:{user_id}" if user_id is not None else "")
    if not owner:
        raise AppError("MODEL_IDENTITY_REQUIRED", "模型调用缺少可信归属关系，未发送请求", 403)
    scope = ModelScope(factory=factory, owner=owner, user_id=user_id, route=route,
                       ledger_database=ledger_database or database_of(factory))
    token = _current.set(scope)
    try:
        yield scope
    finally:
        _current.reset(token)
