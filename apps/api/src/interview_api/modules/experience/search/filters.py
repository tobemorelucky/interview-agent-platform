"""Simple non-LLM URL candidate filtering for Step 5 discovery."""

from dataclasses import dataclass
from urllib.parse import urlsplit

from interview_api.modules.experience.search.base import SearchResult


PLATFORM_ALLOWED_DOMAINS = {
    "牛客": ["nowcoder.com"],
    "小红书": ["xiaohongshu.com", "xhslink.com"],
    "抖音": ["douyin.com"],
}

JOB_ALIASES: dict[str, list[str]] = {
    "Java": ["Java", "Java后端", "Spring", "Spring Boot", "JVM"],
    "后端": ["后端", "服务端", "后台开发", "backend"],
    "AI应用开发": ["AI应用", "大模型应用", "RAG", "Agent", "LLM应用"],
    "前端": ["前端", "Vue", "React", "Web前端"],
}

COMPANY_ALIASES: dict[str, list[str]] = {
    "腾讯": ["腾讯", "腾讯云", "微信", "Tencent"],
    "字节": ["字节", "字节跳动", "抖音", "ByteDance", "TikTok"],
    "阿里": ["阿里", "阿里巴巴", "淘天", "阿里云", "Alibaba"],
    "美团": ["美团", "Meituan"],
    "百度": ["百度", "百度智能云", "Baidu"],
}

INTERVIEW_TOPIC_TERMS = (
    "面经",
    "面试经验",
    "面试题",
    "面试真题",
    "一面",
    "二面",
    "三面",
    "技术面",
    "HR面",
    "笔试",
    "算法题",
    "八股",
    "offer",
    "oc",
    "校招面经",
    "实习面试",
)

STRONG_INTERVIEW_TERMS = (
    "面经",
    "面试经验",
    "面试题",
    "面试真题",
    "一面",
    "二面",
    "三面",
    "技术面",
    "笔试",
    "校招面经",
    "实习面试",
)

STRONG_NEGATIVE_TERMS = (
    "公务员",
    "事业单位",
    "法官助理",
    "考试录用",
    "省考",
    "国考",
    "拟录用",
    "成绩查询",
    "帮助中心",
    "使用手册",
    "腾讯会议",
    "下载",
    "官网首页",
)

WEAK_NEGATIVE_TERMS = (
    "招聘公告",
    "职位表",
    "岗位职责",
    "薪资待遇",
    "报名入口",
    "准考证",
    "资格复审",
)

UNRELATED_URL_TOKENS = (
    "login",
    "signin",
    "signup",
    "register",
    "passport",
    "search",
    "download",
    "help",
    "support",
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

    snippet = (result.snippet or "").strip()
    parsed = urlsplit(result.url)
    host = parsed.netloc.lower()
    url_text = f"{host}{parsed.path}".lower()
    if any(token in url_text for token in UNRELATED_URL_TOKENS):
        return CandidateFilterResult(False, "rejected_unrelated_url")

    platform_name = _normalize_platform(platform)
    allowed_domains = PLATFORM_ALLOWED_DOMAINS.get(platform_name)
    if allowed_domains and not _host_matches_any(host, allowed_domains):
        return CandidateFilterResult(False, "rejected_domain_mismatch")

    searchable_text = f"{title}\n{snippet}"
    has_topic = _contains_any(searchable_text, INTERVIEW_TOPIC_TERMS)
    if not has_topic:
        return CandidateFilterResult(False, "rejected_missing_interview_topic")

    has_strong_topic = _contains_any(searchable_text, STRONG_INTERVIEW_TERMS)
    if _contains_any(searchable_text, STRONG_NEGATIVE_TERMS):
        return CandidateFilterResult(False, "rejected_strong_negative")
    if _contains_any(searchable_text, WEAK_NEGATIVE_TERMS) and not has_strong_topic:
        return CandidateFilterResult(False, "rejected_weak_negative")

    if allowed_domains:
        return CandidateFilterResult(True, "platform_domain_topic")

    if not _contains_any(searchable_text, tuple(_keyword_aliases(keyword))):
        return CandidateFilterResult(False, "rejected_missing_keyword")
    return CandidateFilterResult(True, "general_topic_and_keyword")


def is_relevant_candidate(result: SearchResult, *, keyword: str) -> bool:
    return evaluate_candidate(result, keyword=keyword, platform="").accepted


def _normalize_platform(platform: str | None) -> str:
    text = (platform or "").strip()
    for name in ("牛客", "小红书", "抖音"):
        if name in text:
            return name
    return ""


def _keyword_aliases(keyword: str | None) -> list[str]:
    text = (keyword or "").strip()
    if not text:
        return []
    aliases = [text]
    for mapping in (JOB_ALIASES, COMPANY_ALIASES):
        if text in mapping:
            aliases.extend(mapping[text])
            continue
        for key, values in mapping.items():
            if text in values:
                aliases.append(key)
                aliases.extend(values)
    return _deduplicate_terms(aliases)


def _contains_any(text: str, terms: tuple[str, ...] | list[str]) -> bool:
    lower_text = text.lower()
    return any(_term_in_text(lower_text, term.lower()) for term in terms if term)


def _term_in_text(lower_text: str, lower_term: str) -> bool:
    if lower_term == "oc":
        index = lower_text.find(lower_term)
        while index >= 0:
            before = lower_text[index - 1] if index > 0 else ""
            after_index = index + len(lower_term)
            after = lower_text[after_index] if after_index < len(lower_text) else ""
            if not before.isalnum() and not after.isalnum():
                return True
            index = lower_text.find(lower_term, index + 1)
        return False
    return lower_term in lower_text


def _host_matches_any(host: str, domains: list[str]) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def _deduplicate_terms(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = value.strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result
