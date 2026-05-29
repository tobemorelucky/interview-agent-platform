"""Simple non-LLM URL candidate filtering."""

from urllib.parse import urlsplit

from interview_api.modules.experience.search.base import SearchResult


RELEVANT_TERMS = ("面经", "面试", "一面", "二面", "笔试", "offer", "实习", "校招", "社招")
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


def is_relevant_candidate(result: SearchResult, *, keyword: str) -> bool:
    if not result.url:
        return False

    title = (result.title or "").strip()
    snippet = (result.snippet or "").strip()
    if not title and not snippet:
        return False

    parsed = urlsplit(result.url)
    url_text = f"{parsed.netloc}{parsed.path}".lower()
    if any(token in url_text for token in UNRELATED_URL_TOKENS):
        return False

    haystack = f"{title} {snippet}".lower()
    keyword_text = (keyword or "").strip().lower()
    if keyword_text and keyword_text in haystack:
        return True
    return any(term.lower() in haystack for term in RELEVANT_TERMS)
