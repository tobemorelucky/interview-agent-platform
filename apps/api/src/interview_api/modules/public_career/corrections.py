"""Controlled correction of a held JD version (M1, minimal scope).

A maintainer may restore page wording that the extraction altered. The rules are
deliberately narrow:

  * the predecessor version is never touched — not its text, not its requirements,
    not its status;
  * the model's original statements stay readable on the predecessor;
  * a corrected statement is only accepted when it is a literal substring of the
    evidence that was already cited for that requirement (same snapshot, same
    offsets, same excerpt). No new evidence is invented, no paraphrase is allowed,
    `supports_literal` semantics are reused rather than relaxed;
  * the successor is created at `WAITING_REVIEW`, never `PUBLISHED`;
  * before/after, reviewer, reason, evidence and time go to `audit_logs`;
  * repeating the same correction returns the same successor (deterministic
    fingerprint), so a double click cannot produce a pile of near-copies.

`apply(dry_run=True)` performs the whole write inside a savepoint and always rolls
it back: it proves what the database would say without leaving a row behind.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from interview_api.core.exceptions import AppError
from interview_api.modules.audit.models import AuditLog
from interview_api.modules.public_career.fingerprints import normalize_text
from interview_api.modules.public_career.models import (
    Claim, ClaimEvidence, JobDescriptionVersion, JobRequirement, SourceSnapshot,
)
from interview_api.modules.users.models import User

CORRECTABLE_STATUSES = {"WAITING_REVIEW", "APPROVED"}
MAX_STATEMENT = 2000


@dataclass(frozen=True)
class Correction:
    requirement_id: int
    statement: str


def literal_within(evidence: list[dict], statement: str) -> bool:
    """`supports_literal`, expressed over already-stored slices.

    Adjacent slices are joined exactly as `EvidenceCatalog.supports_literal` joins
    them; a gap between cited slices is never bridged. A corrected sentence must be
    a substring of one joined run, so restoring page wording works and inventing a
    merge of two unrelated bullets does not.
    """
    groups: list[str] = []
    previous = None
    for item in sorted(evidence, key=lambda row: (row["source_snapshot_id"], row["start"])):
        if (previous is not None and previous["source_snapshot_id"] == item["source_snapshot_id"]
                and previous["end"] == item["start"]):
            groups[-1] += item["excerpt"]
        else:
            groups.append(item["excerpt"])
        previous = item
    value = normalize_text(statement)
    return bool(value) and any(value in normalize_text(group) for group in groups)


class JdCorrectionService:
    def __init__(self, db):
        self.db = db

    # ── loading and validation ────────────────────────────────────────────────
    async def _reviewer(self, reviewer_id: int) -> User:
        reviewer = await self.db.get(User, reviewer_id)
        if not reviewer or reviewer.role != "ADMIN" or not reviewer.is_active:
            raise AppError("ADMIN_REQUIRED", "只有管理员可以修订公共岗位", 403)
        return reviewer

    async def _load(self, jd_id: int) -> dict:
        jd = await self.db.scalar(select(JobDescriptionVersion).where(
            JobDescriptionVersion.id == jd_id).with_for_update())
        if not jd:
            raise AppError("JD_NOT_FOUND", "JD版本不存在", 404)
        requirements = list((await self.db.scalars(select(JobRequirement).where(
            JobRequirement.job_description_version_id == jd.id).order_by(JobRequirement.id))).all())
        if not requirements:
            raise AppError("JD_EVIDENCE_REQUIRED", "该版本没有可修订的要求", 422)
        claims = {c.id: c for c in (await self.db.scalars(select(Claim).where(
            Claim.id.in_({r.claim_id for r in requirements if r.claim_id})))).all()}
        evidence = list((await self.db.scalars(select(ClaimEvidence).where(
            ClaimEvidence.claim_id.in_(set(claims))).order_by(ClaimEvidence.id))).all())
        snapshot = await self.db.get(SourceSnapshot, jd.source_snapshot_id) if jd.source_snapshot_id else None
        return {"jd": jd, "requirements": requirements, "claims": claims,
                "evidence": evidence, "snapshot": snapshot}

    @staticmethod
    def _evidence_for(claim_id: int | None, evidence) -> list[dict]:
        rows = []
        for item in evidence:
            if item.claim_id != claim_id:
                continue
            location = item.source_location_json or {}
            if isinstance(location, str):
                import json
                location = json.loads(location)
            rows.append({"source_snapshot_id": item.source_snapshot_id,
                         "start": location.get("start_char"), "end": location.get("end_char"),
                         "excerpt": item.excerpt, "evidence_key": item.evidence_key})
        return rows

    @staticmethod
    def _check_offsets(snapshot, evidence: list[dict]) -> None:
        if snapshot is None:
            raise AppError("JD_EVIDENCE_INVALID", "缺少来源正文", 422)
        body = snapshot.content_text or ""
        if sha256(body.encode()).hexdigest() != snapshot.content_hash:
            raise AppError("JD_EVIDENCE_INVALID", "来源正文哈希不一致", 422)
        for item in evidence:
            start, end = item.get("start"), item.get("end")
            if (not isinstance(start, int) or not isinstance(end, int)
                    or not 0 <= start < end <= len(body) or body[start:end] != item["excerpt"]):
                raise AppError("JD_EVIDENCE_INVALID", "证据位置与原文不一致", 422)

    async def plan(self, jd_id: int, *, reviewer_id: int, reason: str,
                   corrections: list[Correction]) -> dict:
        await self._reviewer(reviewer_id)
        if not reason or not reason.strip():
            raise AppError("REVIEW_REASON_REQUIRED", "请记录修订依据", 422)
        loaded = await self._load(jd_id)
        jd, requirements = loaded["jd"], loaded["requirements"]
        if jd.publication_status not in CORRECTABLE_STATUSES:
            raise AppError("JD_REVIEW_STATE", "该版本不在可修订状态", 409)
        by_id = {r.id: r for r in requirements}
        if not corrections:
            raise AppError("CORRECTION_EMPTY", "没有提出任何修订", 422)
        if len({c.requirement_id for c in corrections}) != len(corrections):
            raise AppError("CORRECTION_DUPLICATE", "同一要求被修订多次", 422)

        items = []
        for correction in corrections:
            requirement = by_id.get(correction.requirement_id)
            if requirement is None:
                raise AppError("UNKNOWN_REQUIREMENT", "要求不属于该JD版本", 422)
            statement = (correction.statement or "").strip()
            if not statement or len(statement) > MAX_STATEMENT:
                raise AppError("CORRECTION_INVALID", "修订文本为空或过长", 422)
            if normalize_text(statement) == normalize_text(requirement.statement):
                raise AppError("CORRECTION_NO_CHANGE", "修订文本与原文相同", 422)
            evidence = self._evidence_for(requirement.claim_id, loaded["evidence"])
            if not evidence:
                raise AppError("JD_EVIDENCE_REQUIRED", "该要求缺少证据", 422)
            self._check_offsets(loaded["snapshot"], evidence)
            supported = literal_within(evidence, statement)
            items.append({
                "requirement_id": requirement.id, "claim_id": requirement.claim_id,
                "before": requirement.statement, "after": statement,
                "requirement_type": requirement.requirement_type,
                "importance": requirement.importance,
                "normalized_value": requirement.normalized_value,
                "evidence": evidence,
                "before_supported": literal_within(evidence, requirement.statement),
                "after_supported": supported,
            })
            if not supported:
                raise AppError("CORRECTION_NOT_LITERAL",
                               "修订文本不是引用证据的原文子串，拒绝", 422)

        fingerprint = sha256(__import__("json").dumps(
            [jd.id, sorted([[item["requirement_id"], normalize_text(item["after"])]
                            for item in items])], ensure_ascii=False).encode()).hexdigest()
        existing = None
        for version in (await self.db.scalars(select(JobDescriptionVersion).where(
                JobDescriptionVersion.job_id == jd.job_id))).all():
            stored = (version.metadata_json or {}).get("correction") or {}
            if stored.get("fingerprint") == fingerprint:
                existing = version.id
                break
        return {"jd_version_id": jd.id, "job_id": jd.job_id, "status": jd.publication_status,
                "requirements": items, "fingerprint": fingerprint,
                "reusable_version_id": existing}

    # ── write path ────────────────────────────────────────────────────────────
    async def apply(self, jd_id: int, *, reviewer_id: int, reason: str,
                    corrections: list[Correction], dry_run: bool = True) -> dict:
        plan = await self.plan(jd_id, reviewer_id=reviewer_id, reason=reason, corrections=corrections)
        if plan["reusable_version_id"]:
            return {**plan, "reused": True, "applied": False,
                    "jd_version_id_new": plan["reusable_version_id"]}

        loaded = await self._load(jd_id)
        jd = loaded["jd"]
        corrected = {item["requirement_id"]: item for item in plan["requirements"]}
        next_version = 1 + max((v for v in [row.version_number for row in
                                            (await self.db.scalars(select(JobDescriptionVersion).where(
                                                JobDescriptionVersion.job_id == jd.job_id))).all()]), default=0)
        now = datetime.now(timezone.utc)
        outcome: dict = {"plan": plan, "reused": False, "applied": False, "dry_run": dry_run}

        savepoint = await self.db.begin_nested()
        try:
            successor = JobDescriptionVersion(
                job_id=jd.job_id, version_number=next_version, raw_text=jd.raw_text,
                structured_data_json=jd.structured_data_json,
                metadata_json={**(jd.metadata_json or {}), "correction": {
                    "correction_of": jd.id, "fingerprint": plan["fingerprint"],
                    "reviewer_id": reviewer_id, "reason": reason.strip(),
                    "corrected_at": now.isoformat(),
                    "requirements": [{"requirement_id": item["requirement_id"],
                                      "before": item["before"], "after": item["after"]}
                                     for item in plan["requirements"]],
                }},
                observed_at=jd.observed_at, valid_from=jd.valid_from, valid_to=jd.valid_to,
                source_snapshot_id=jd.source_snapshot_id, publication_status="WAITING_REVIEW",
            )
            self.db.add(successor)
            await self.db.flush()

            for original in loaded["requirements"]:
                item = corrected.get(original.id)
                if item is None:
                    self.db.add(JobRequirement(
                        job_description_version_id=successor.id,
                        requirement_type=original.requirement_type,
                        normalized_value=original.normalized_value,
                        statement=original.statement, importance=original.importance,
                        claim_id=original.claim_id))
                    continue
                claim_key = sha256(
                    f"CORRECTION:{jd.id}:{original.id}:{normalize_text(item['after'])}".encode()).hexdigest()
                claim_id = await self.db.scalar(select(Claim.id).where(Claim.claim_key == claim_key))
                if claim_id is None:
                    from sqlalchemy.dialects.postgresql import insert as pg_insert
                    claim_id = await self.db.scalar(pg_insert(Claim).values(
                        claim_key=claim_key, category=(loaded["claims"][original.claim_id].category
                                                       if original.claim_id in loaded["claims"] else "JOB_JD"),
                        statement=item["after"], status="WAITING_REVIEW",
                    ).on_conflict_do_nothing(constraint="uq_claims_claim_key").returning(Claim.id))
                    if claim_id is None:
                        claim_id = await self.db.scalar(select(Claim.id).where(Claim.claim_key == claim_key))
                    from sqlalchemy.dialects.postgresql import insert as pg_insert_evidence
                    for evidence in item["evidence"]:
                        await self.db.execute(pg_insert_evidence(ClaimEvidence).values(
                            claim_id=claim_id, source_snapshot_id=evidence["source_snapshot_id"],
                            evidence_key=evidence["evidence_key"], excerpt=evidence["excerpt"],
                            source_location_json={"start_char": evidence["start"],
                                                  "end_char": evidence["end"],
                                                  "unit": "unicode_codepoint"},
                            support_type="DIRECT",
                        ).on_conflict_do_nothing(constraint="uq_claim_evidence_claim_key"))
                self.db.add(JobRequirement(
                    job_description_version_id=successor.id,
                    requirement_type=original.requirement_type,
                    normalized_value=item["normalized_value"],
                    statement=item["after"], importance=original.importance,
                    claim_id=claim_id))
            await self.db.flush()

            self.db.add(AuditLog(
                actor_user_id=reviewer_id, actor_role="ADMIN",
                action="public_career.jd.correction", resource_type="JobDescriptionVersion",
                resource_id=str(successor.id),
                before_json={"jd_version_id": jd.id, "publication_status": jd.publication_status,
                             "requirements": [{"requirement_id": item["requirement_id"],
                                               "statement": item["before"]}
                                              for item in plan["requirements"]]},
                after_json={"jd_version_id": successor.id, "version_number": next_version,
                            "publication_status": "WAITING_REVIEW",
                            "requirements": [{"requirement_id": item["requirement_id"],
                                              "statement": item["after"]}
                                             for item in plan["requirements"]]},
                metadata_json={"reason": reason.strip(), "fingerprint": plan["fingerprint"],
                               "correction_of": jd.id,
                               "evidence": [{"requirement_id": item["requirement_id"],
                                             "evidence_keys": [e["evidence_key"] for e in item["evidence"]]}
                                            for item in plan["requirements"]]},
                status="SUCCESS"))
            await self.db.flush()
            outcome.update({"applied": True, "jd_version_id_new": successor.id,
                            "version_number": next_version, "publication_status": "WAITING_REVIEW"})
            if dry_run:
                await savepoint.rollback()
                outcome.update({"applied": False, "rolled_back": True,
                                "note": "dry run: the whole write was rolled back"})
            else:
                await savepoint.commit()
        except IntegrityError as exc:
            await savepoint.rollback()
            detail = str(getattr(exc, "orig", exc))
            outcome.update({
                "applied": False, "rolled_back": True,
                "blocked_by": ("SCHEMA_UNIQUE_FINGERPRINT"
                               if "uq_job_description_versions_job_fingerprint" in detail
                               else "INTEGRITY_ERROR"),
                "detail": detail[:300],
            })
        except AppError:
            await savepoint.rollback()
            raise
        return outcome
