"""Build SearXNG search queries from admin collection tasks."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchQuery:
    query: str
    platform: str
    keyword: str
    reason: str


def build_search_queries(
    *,
    search_scope: str,
    job_keywords_json: list[str],
    company_keywords_json: list[str],
    platforms_json: list[str],
    time_window_hours: int,
) -> list[SearchQuery]:
    """Generate broad discovery queries without combining job and company keywords."""
    del time_window_hours  # Reserved for later tuning; provider receives it separately.

    scope = search_scope.upper()
    if scope == "JOB":
        keywords = _clean_keywords(job_keywords_json)
        generic_templates = ["{kw} 面经", "{kw} 面试经验", "{kw} 一面 面经"]
        reason = "job_keyword"
    elif scope == "COMPANY":
        keywords = _clean_keywords(company_keywords_json)
        generic_templates = ["{kw} 面经", "{kw} 面试经验", "{kw} 一面 面经"]
        reason = "company_keyword"
    else:
        raise ValueError("search_scope must be JOB or COMPANY")

    platforms = _clean_keywords(platforms_json)
    queries: list[SearchQuery] = []

    for keyword in keywords:
        if _has_platform(platforms, "全网"):
            for template in generic_templates:
                queries.append(
                    SearchQuery(
                        query=template.format(kw=keyword),
                        platform="全网",
                        keyword=keyword,
                        reason=reason,
                    )
                )

        if _has_platform(platforms, "牛客"):
            nowcoder_templates = [
                "site:nowcoder.com {kw} 面经",
                "site:nowcoder.com {kw} 面试经验",
                "牛客 {kw} 面经",
                "牛客 {kw} 面试经验",
                "{kw} 牛客 面经",
            ]
            for template in nowcoder_templates:
                queries.append(
                    SearchQuery(
                        query=template.format(kw=keyword),
                        platform="牛客",
                        keyword=keyword,
                        reason="platform_nowcoder",
                    )
                )

        if _has_platform(platforms, "小红书"):
            templates = (
                ["小红书 {kw} 面经"]
                if scope == "JOB"
                else ["小红书 {kw} 面经", "小红书 {kw} 面试经验"]
            )
            for template in templates:
                queries.append(
                    SearchQuery(
                        query=template.format(kw=keyword),
                        platform="小红书",
                        keyword=keyword,
                        reason="platform_xiaohongshu",
                    )
                )

        if _has_platform(platforms, "抖音"):
            templates = (
                ["抖音 {kw} 面试经验"]
                if scope == "JOB"
                else ["抖音 {kw} 面经", "抖音 {kw} 面试经验"]
            )
            for template in templates:
                queries.append(
                    SearchQuery(
                        query=template.format(kw=keyword),
                        platform="抖音",
                        keyword=keyword,
                        reason="platform_douyin",
                    )
                )

    return _deduplicate_queries(queries)


def _clean_keywords(values: list[str] | None) -> list[str]:
    cleaned: list[str] = []
    for value in values or []:
        text = str(value).strip()
        if text:
            cleaned.append(text)
    return cleaned


def _has_platform(platforms: list[str], name: str) -> bool:
    return any(name in platform for platform in platforms)


def _deduplicate_queries(queries: list[SearchQuery]) -> list[SearchQuery]:
    seen: set[str] = set()
    result: list[SearchQuery] = []
    for item in queries:
        if item.query in seen:
            continue
        seen.add(item.query)
        result.append(item)
    return result
