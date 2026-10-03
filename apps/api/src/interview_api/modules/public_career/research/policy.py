"""Publication is deterministic and official-domain trust is operator-owned."""

from dataclasses import dataclass
from urllib.parse import urlsplit

from interview_api.modules.public_career.fingerprints import normalize_text


@dataclass(frozen=True)
class PublicationDecision:
    status: str
    official: bool
    reasons: tuple[str, ...]


def job_publication_gate(*, company_name: str, urls: list[str],
                         evidence_complete: bool, suitable: bool,
                         risk_flags: list[str], trusted_domains: dict[str, list[str]],
                         official_domain: str | None = None) -> PublicationDecision:
    domains = set(trusted_domains.get(normalize_text(company_name), []))
    if official_domain:
        domains.add(official_domain)
    domains = {d.lower().strip().rstrip(".") for d in domains if d}
    hosts = [urlsplit(url).hostname or "" for url in urls]
    official = bool(hosts) and all(
        any(host == domain or host.endswith("." + domain) for domain in domains)
        for host in hosts
    )
    if not evidence_complete:
        return PublicationDecision("REJECTED", official, ("missing_evidence",))
    if not suitable or risk_flags:
        return PublicationDecision("WAITING_REVIEW", official, ("quality_risk_requires_review",))
    if official:
        return PublicationDecision("APPROVED", True, ("official_domain_and_complete_evidence",))
    return PublicationDecision("WAITING_REVIEW", False, ("unverified_or_community_source",))
