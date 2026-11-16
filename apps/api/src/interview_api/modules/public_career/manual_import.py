"""Maintainer-only acquisition adapter. No Search/Fetch, final-entity SQL or LLM."""
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from interview_api.core.url_safety import canonicalize_public_url
from interview_api.modules.public_career.fingerprints import compute_content_fingerprint, normalize_text
from interview_api.modules.public_career.models import Source, SourceSnapshot, PublicDataRefreshTask


ACQUISITION_MODE = "MANUAL_SOURCE_IMPORT"
TARGET_PREFIX = ACQUISITION_MODE + ":"
MAX_FILE_BYTES = 2 * 1024 * 1024


class ManualImportError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ManualDocument:
    source_url: str
    source_type: str
    document_type: str
    company: str
    job_title: str
    content: str
    file_name: str
    acquired_at: datetime

    def __post_init__(self):
        canonicalize_public_url(self.source_url)
        if self.source_type not in {"official", "community"} or self.document_type not in {"job_jd", "interview_experience"}:
            raise ManualImportError("INVALID_MANUAL_DOCUMENT_TYPE")
        if not normalize_text(self.company) or not normalize_text(self.job_title) or max(len(self.company), len(self.job_title)) > 255:
            raise ManualImportError("MANUAL_IDENTITY_REQUIRED")
        if not self.content.strip() or "\x00" in self.content or len(self.content.encode("utf-8")) > MAX_FILE_BYTES:
            raise ManualImportError("INVALID_MANUAL_BODY")
        if self.acquired_at.tzinfo is None:
            raise ManualImportError("ACQUISITION_TIME_REQUIRES_TIMEZONE")

    @classmethod
    def from_file(cls, *, file, source_url, source_type, document_type, company, job_title):
        path = Path(file)
        if not path.is_file() or path.suffix.lower() not in {".txt", ".md"} or path.stat().st_size > MAX_FILE_BYTES:
            raise ManualImportError("MANUAL_FILE_MUST_BE_UTF8_TEXT")
        try:
            content = path.read_bytes().decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ManualImportError("MANUAL_FILE_MUST_BE_UTF8_TEXT") from exc
        # File mtime is local acquisition/save time, NOT remote publication/fetch.
        return cls(source_url, source_type, document_type, company.strip(), job_title.strip(),
                   content, path.name, datetime.fromtimestamp(path.stat().st_mtime, timezone.utc))

    @property
    def canonical_url(self):
        return canonicalize_public_url(self.source_url)

    @property
    def content_hash(self):
        # Same raw-text hash as existing SourceSnapshot persistence.
        return sha256(self.content.encode("utf-8")).hexdigest()

    @property
    def key(self):
        return "manual:" + sha256(json.dumps([self.canonical_url, self.content_hash, self.document_type]).encode()).hexdigest()


def manual_manifest(task):
    if not (task.target_ref or "").startswith(TARGET_PREFIX):
        return None
    try:
        manifest = json.loads(task.reason)
        assert manifest["acquisition_mode"] == ACQUISITION_MODE and manifest["schema_version"] == 1
        assert task.target_ref == TARGET_PREFIX + str(manifest["source_snapshot_id"])
        assert manifest["source_type"] in {"official", "community"}
        assert manifest["document_type"] in {"job_jd", "interview_experience"}
        assert task.target_type == ("JOB_JD" if manifest["document_type"] == "job_jd" else "INTERVIEW_DATA")
        return manifest
    except (ValueError, TypeError, KeyError, AssertionError) as exc:
        raise ManualImportError("INVALID_MANUAL_SOURCE_MANIFEST") from exc


async def resolve_manual_snapshot(db, task):
    manifest = manual_manifest(task)
    if manifest is None:
        return None
    snapshot = await db.get(SourceSnapshot, manifest["source_snapshot_id"])
    source = await db.get(Source, snapshot.source_id) if snapshot else None
    event = (snapshot.metadata_json.get("manual_source_imports", {}) if snapshot else {}).get(str(task.id))
    if (not source or event != manifest or source.canonical_url != manifest["canonical_url"]
            or snapshot.content_hash != manifest["content_sha256"]
            or sha256((snapshot.content_text or "").encode()).hexdigest() != manifest["content_sha256"]):
        raise ManualImportError("MANUAL_SNAPSHOT_PROVENANCE_MISMATCH")
    return [snapshot]


class ManualImportService:
    def __init__(self, factory):
        self.factory = factory

    async def prepare(self, document):
        """One short transaction: dedupe, provenance and READY; never final entities."""
        async with self.factory() as db:
            async with db.begin():
                url_hash = sha256(document.canonical_url.encode()).hexdigest()
                await db.execute(insert(Source).values(canonical_url=document.canonical_url, url_hash=url_hash,
                    domain=urlsplit(document.canonical_url).hostname, source_type=document.source_type,
                    metadata_json={"first_seen_via": ACQUISITION_MODE}).on_conflict_do_nothing(constraint="uq_sources_url_hash"))
                source = await db.scalar(select(Source).where(Source.url_hash == url_hash).with_for_update())
                await db.execute(insert(SourceSnapshot).values(source_id=source.id, content_hash=document.content_hash,
                    content_text=document.content, normalized_content=normalize_text(document.content),
                    fetched_at=document.acquired_at, title=None, created_by_tool_invocation_id=None,
                    metadata_json={"acquisition_mode": ACQUISITION_MODE,
                        "fetched_at_semantics": "LOCAL_ACQUIRED_AT_NOT_NETWORK_FETCH",
                        "content_fingerprint": compute_content_fingerprint(document.content)}
                ).on_conflict_do_nothing(constraint="uq_source_snapshots_source_content"))
                snapshot = await db.scalar(select(SourceSnapshot).where(SourceSnapshot.source_id == source.id,
                    SourceSnapshot.content_hash == document.content_hash))
                target = "JOB_JD" if document.document_type == "job_jd" else "INTERVIEW_DATA"
                manifest = dict(schema_version=1, acquisition_mode=ACQUISITION_MODE,
                    source_snapshot_id=snapshot.id, canonical_url=document.canonical_url, source_type=document.source_type,
                    document_type=document.document_type, company=document.company, job_title=document.job_title,
                    content_sha256=document.content_hash, content_fingerprint=compute_content_fingerprint(document.content),
                    acquired_at=document.acquired_at.isoformat(), acquisition_timestamp_semantics="LOCAL_BODY_SAVE_TIME",
                    file_name=document.file_name)
                task = await db.scalar(select(PublicDataRefreshTask).where(PublicDataRefreshTask.idempotency_key == document.key))
                reused = task is not None
                if task:
                    old = manual_manifest(task)
                    if not old or any(old[k] != manifest[k] for k in ("canonical_url", "source_type", "document_type", "company", "job_title", "content_sha256")):
                        raise ManualImportError("MANUAL_IMPORT_IDENTITY_CONFLICT")
                    await resolve_manual_snapshot(db, task)
                else:
                    task = PublicDataRefreshTask(target_type=target, target_ref=TARGET_PREFIX + str(snapshot.id),
                        reason=json.dumps(manifest, ensure_ascii=False), idempotency_key=document.key,
                        status="READY_FOR_EXTRACTION", started_at=datetime.now(timezone.utc))
                    db.add(task)
                    await db.flush()
                    # Preserve the original acquisition metadata if this snapshot
                    # already came from Fetch or a different manual document task.
                    metadata = dict(snapshot.metadata_json)
                    metadata["manual_source_imports"] = {**metadata.get("manual_source_imports", {}), str(task.id): manifest}
                    snapshot.metadata_json = metadata
                return dict(refresh_task_id=task.id, source_id=source.id, source_snapshot_id=snapshot.id,
                    status=task.status, reused=reused, acquisition_mode=ACQUISITION_MODE,
                    source_type=document.source_type, content_sha256=document.content_hash)

    async def prepare_identity_revision(self, document, *, predecessor_task_id, reason, authorized_by):
        """Explicitly authorised identity revision of a FAILED task.

        A failed extraction freezes a manifest whose printed label turned out not
        to be the page's own wording. Editing that manifest would rewrite history,
        and changing the URL or body hash to dodge the idempotency key would fake a
        new acquisition. Instead this creates a *new* task:

          * same canonical URL, same content hash, same document type (asserted),
          * a label that occurs verbatim in the stored snapshot,
          * a label that still contains the old one once whitespace is ignored,
            so a channel prefix may be added but the posting may not be swapped,
          * a deterministic key derived from the predecessor, so a second submit
            returns the same revision instead of a pile of near-copies.

        The predecessor keeps its FAILED status, its frozen manifest and its audit
        trail; the revision points back at it.
        """
        from interview_api.modules.audit.models import AuditLog

        async with self.factory() as db:
            async with db.begin():
                predecessor = await db.scalar(select(PublicDataRefreshTask).where(
                    PublicDataRefreshTask.id == predecessor_task_id).with_for_update())
                if predecessor is None:
                    raise ManualImportError("MANUAL_REVISION_PREDECESSOR_NOT_FOUND")
                if predecessor.status != "FAILED":
                    raise ManualImportError("MANUAL_REVISION_PREDECESSOR_NOT_FAILED")
                old = manual_manifest(predecessor)
                if not old:
                    raise ManualImportError("INVALID_MANUAL_SOURCE_MANIFEST")
                if (old["canonical_url"] != document.canonical_url
                        or old["content_sha256"] != document.content_hash
                        or old["document_type"] != document.document_type):
                    raise ManualImportError("MANUAL_REVISION_SOURCE_CHANGED")
                if normalize_text(old["company"]) != normalize_text(document.company):
                    raise ManualImportError("MANUAL_REVISION_COMPANY_CHANGED")
                if normalize_text(old["job_title"]) == normalize_text(document.job_title):
                    raise ManualImportError("MANUAL_REVISION_LABEL_UNCHANGED")
                snapshot = await db.get(SourceSnapshot, old["source_snapshot_id"])
                body = (snapshot.content_text if snapshot else "") or ""
                if document.content_hash != sha256(body.encode()).hexdigest():
                    raise ManualImportError("MANUAL_SNAPSHOT_PROVENANCE_MISMATCH")
                if not _occurs_in_text(document.job_title, body):
                    # The new label must be quoted from the page, not invented.
                    raise ManualImportError("MANUAL_REVISION_LABEL_NOT_IN_SNAPSHOT")
                if _compact(old["job_title"]) not in _compact(document.job_title):
                    raise ManualImportError("MANUAL_REVISION_LABEL_UNRELATED")

                key = "manual-rev:%s" % sha256(json.dumps([
                    predecessor.id, document.canonical_url, document.content_hash,
                    document.document_type, normalize_text(document.job_title)]).encode()).hexdigest()
                task = await db.scalar(select(PublicDataRefreshTask).where(
                    PublicDataRefreshTask.idempotency_key == key))
                reused = task is not None
                if task:
                    if task.id == predecessor.id:
                        raise ManualImportError("MANUAL_REVISION_KEY_COLLISION")
                    return dict(refresh_task_id=task.id, status=task.status, reused=True,
                                revision_of=predecessor.id, source_snapshot_id=old["source_snapshot_id"],
                                job_title=document.job_title, content_sha256=document.content_hash)

                manifest = dict(old, job_title=document.job_title, identity_revision=dict(
                    of_task_id=predecessor.id, of_job_title=old["job_title"], reason=reason,
                    authorized_by=authorized_by, authorized_at=datetime.now(timezone.utc).isoformat(),
                    previous_error=(predecessor.error_message or "")[:500],
                    same_canonical_url=True, same_content_sha256=True,
                    label_occurs_in_snapshot=True))
                task = PublicDataRefreshTask(
                    target_type=predecessor.target_type, target_ref=predecessor.target_ref,
                    reason=json.dumps(manifest, ensure_ascii=False), idempotency_key=key,
                    status="READY_FOR_EXTRACTION", started_at=datetime.now(timezone.utc))
                db.add(task)
                await db.flush()
                metadata = dict(snapshot.metadata_json)
                metadata["manual_source_imports"] = {
                    **metadata.get("manual_source_imports", {}), str(task.id): manifest}
                snapshot.metadata_json = metadata
                db.add(AuditLog(actor_user_id=None, actor_role="SYSTEM",
                                action="public_career.jd.identity_revision",
                                resource_type="PublicDataRefreshTask", resource_id=str(task.id),
                                before_json={"task_id": predecessor.id, "job_title": old["job_title"],
                                             "status": predecessor.status},
                                after_json={"task_id": task.id, "job_title": document.job_title,
                                            "status": task.status},
                                metadata_json={"reason": reason, "authorized_by": authorized_by},
                                status="SUCCESS"))
                await db.flush()
                return dict(refresh_task_id=task.id, status=task.status, reused=False,
                            revision_of=predecessor.id, source_snapshot_id=old["source_snapshot_id"],
                            job_title=document.job_title, content_sha256=document.content_hash)

    async def execute(self, prepared, *, router):
        """Completed/failed/busy tasks never automatically trigger another model run."""
        if prepared["status"] != "READY_FOR_EXTRACTION":
            return prepared
        from interview_api.modules.public_career.research.agent import PublicDataResearchAgent
        result = await PublicDataResearchAgent(self.factory, router).run(prepared["refresh_task_id"])
        return {**prepared, **result}


def require_live_router(router):
    from interview_api.infrastructure.models.provider import CompatibleModelProvider
    from interview_api.infrastructure.models.router import EXTRACT_ROUTE, QUALITY_ROUTE
    for route in (EXTRACT_ROUTE, QUALITY_ROUTE):
        if not isinstance(router.primary(route), CompatibleModelProvider):
            raise ManualImportError("MANUAL_CLI_REQUIRES_LIVE_MODEL")
    if any(not isinstance(provider, CompatibleModelProvider) for provider in router.fallback_routes.values()):
        raise ManualImportError("MANUAL_CLI_REQUIRES_LIVE_MODEL")


def _compact(value: str) -> str:
    """Whitespace-free skeleton, used only to relate two labels of one posting."""
    return "".join(normalize_text(value or "").split())


def _occurs_in_text(label: str, body: str) -> bool:
    """True when the label is quoted from the body, allowing line wrapping only."""
    if not label or not body:
        return False
    if label in body:
        return True
    return _compact(label) in _compact(body)


def validate_expected_identity(manifest, company, job_title):
    if manifest and (normalize_text(company or "") != normalize_text(manifest["company"])
                     or normalize_text(job_title or "") != normalize_text(manifest["job_title"])):
        raise ManualImportError("MANUAL_EXTRACTED_IDENTITY_MISMATCH")
