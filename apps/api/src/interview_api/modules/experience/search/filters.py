"""Simple non-LLM URL candidate filtering."""

from dataclasses import dataclass
from urllib.parse import urlsplit

from interview_api.modules.experience.search.base import SearchResult


PLATFORM_ALLOWED_DOMAINS = {
    "牛客": ["nowcoder.com"],
    "小红书": ["xiaohongshu.com", "xhslink.com"],
    "抖音": ["douyin.com"],
}

TITLE_REQUIRED_TERMS = (
    "面经",
    "面试经验",
    "一面",
    "二面",
    "三面",
    "笔试",
    "面试题",
    "面试真题",
    "校招",
    "实习面试",
    "offer",
)

STRONG_INTERVIEW_TERMS = (
    "面经",
    "面试经验",
    "一面",
    "二面",
    "三面",
    "面试题",
    "面试真题",
)

NEGATIVE_TITLE_TERMS = (
    "公务员",
    "事业单位",
    "法官助理",
    "考试录用",
    "省考",
    "职位表",
    "拟录用",
    "招聘公告",
    "成绩查询",
    "帮助中心",
    "使用手册",
    "腾讯会议",
    "下载",
    "官网首页",
)

UNRELATED_URL_TOKENS = (
    "login",
    "signin",
    "signup",
    "register",
    "passport",
    "search",
    "advert",
    "ad.",
    "/ad/",
    "promotion",
)


@dataclass(frozen=True)
class CandidateFilterResult:
    accepted: bool
    reason: str


def evaluate_candidate(
    result: SearchResult,
    *,
    keyword: str,
    platform: str,
) -> CandidateFilterResult:
    if not result.url:
        return CandidateFilterResult(False, "empty_url")

    title = (result.title or "").strip()
    if not title:
        return CandidateFilterResult(False, "empty_title")

    parsed = urlsplit(result.url)
    host = parsed.netloc.lower()
    url_text = f"{host}{parsed.path}".lower()
    if any(token in url_text for token in UNRELATED_URL_TOKENS):
        return CandidateFilterResult(False, "unrelated_url")

    platform_name = _normalize_platform(platform)
    allowed_domains = PLATFORM_ALLOWED_DOMAINS.get(platform_name)
    if allowed_domains and not _host_matches_any(host, allowed_domains):
        return CandidateFilterResult(False, f"platform_domain_mismatch:{host}")

    has_title_topic = _contains_any(title, TITLE_REQUIRED_TERMS)
    if not has_title_topic:
        return CandidateFilterResult(False, "title_missing_interview_topic")

    has_strong_interview_topic = _contains_any(title, STRONG_INTERVIEW_TERMS)
    if _contains_any(title, NEGATIVE_TITLE_TERMS) and not has_strong_interview_topic:
        return CandidateFilterResult(False, "negative_title_term")

    keyword_text = (keyword or "").strip()
    if platform_name == "全网" and keyword_text and keyword_text.lower() not in title.lower():
        return CandidateFilterResult(False, "keyword_missing_in_title")

    if allowed_domains:
        return CandidateFilterResult(True, f"platform_domain_and_title_topic:{platform_name}")
    return CandidateFilterResult(True, "title_keyword_and_topic")


def is_relevant_candidate(result: SearchResult, *, keyword: str) -> bool:
    return evaluate_candidate(result, keyword=keyword, platform="全网").accepted


def _normalize_platform(platform: str | None) -> str:
    text = (platform or "").strip()
    for name in ("全网", "牛客", "小红书", "抖音"):
        if name in text:
            return name
    return text


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lower_text = text.lower()
    return any(term.lower() in lower_text for term in terms)


def _host_matches_any(host: str, domains: list[str]) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)
