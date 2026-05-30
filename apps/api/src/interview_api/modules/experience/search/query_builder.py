"""Build SearXNG search queries from admin collection tasks."""

from dataclasses import dataclass


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
    """Generate discovery queries without combining job and company keywords."""
    del time_window_hours  # Provider receives it separately.

    scope = search_scope.upper()
    if scope == "JOB":
        keywords = _clean_keywords(job_keywords_json)
        reason = "job_keyword"
    elif scope == "COMPANY":
        keywords = _clean_keywords(company_keywords_json)
        reason = "company_keyword"
    else:
        raise ValueError("search_scope must be JOB or COMPANY")

    platforms = _clean_platforms(platforms_json)
    queries: list[SearchQuery] = []

    for keyword in keywords:
        if not platforms:
            templates = _generic_job_templates(keyword) if scope == "JOB" else _generic_company_templates(keyword)
            for query in templates:
                queries.append(
                    SearchQuery(
                        query=query,
                        platform="",
                        keyword=keyword,
                        reason=reason,
                    )
                )
            continue

        if "牛客" in platforms:
            for template in _nowcoder_templates():
                queries.append(
                    SearchQuery(
                        query=template.format(kw=keyword),
                        platform="牛客",
                        keyword=keyword,
                        reason="platform_nowcoder",
                    )
                )

        if "小红书" in platforms:
            for template in _xiaohongshu_templates():
                queries.append(
                    SearchQuery(
                        query=template.format(kw=keyword),
                        platform="小红书",
                        keyword=keyword,
                        reason="platform_xiaohongshu",
                    )
                )

        if "抖音" in platforms:
            for template in _douyin_templates():
                queries.append(
                    SearchQuery(
                        query=template.format(kw=keyword),
                        platform="抖音",
                        keyword=keyword,
                        reason="platform_douyin",
                    )
                )

    return _deduplicate_queries(queries)


def _generic_job_templates(keyword: str) -> list[str]:
    base = [
        f"{keyword} 面经",
        f"{keyword} 面试经验",
        f"{keyword} 面试题",
        f"{keyword} 一面",
        f"{keyword} 二面",
        f"{keyword} 校招面经",
        f"{keyword} 实习面试",
    ]
    if keyword == "Java":
        base.extend(["Java 后端 面经", "Spring Boot 面试题", "JVM 面试题"])
    elif keyword == "后端":
        base.extend(["服务端 面经", "后台开发 面经", "backend interview experience"])
    else:
        aliases = [alias for alias in JOB_ALIASES.get(keyword, []) if alias != keyword]
        base.extend(f"{alias} 面经" for alias in aliases[:3])
    return base


def _generic_company_templates(keyword: str) -> list[str]:
    base = [
        f"{keyword} 面经",
        f"{keyword} 面试经验",
        f"{keyword} 面试题",
        f"{keyword} 一面",
        f"{keyword} 二面",
        f"{keyword} 校招面经",
        f"{keyword} 实习面试",
        f"{keyword} 后端 面经",
        f"{keyword} 技术面",
    ]
    aliases = [alias for alias in COMPANY_ALIASES.get(keyword, []) if alias != keyword]
    for alias in aliases:
        if _is_ascii_text(alias):
            base.extend([f"{alias} interview experience", f"{alias} interview questions"])
        else:
            base.append(f"{alias} 面经")
    return base


def _nowcoder_templates() -> list[str]:
    return [
        "site:nowcoder.com {kw} 面经",
        "site:nowcoder.com {kw} 面试经验",
        "site:www.nowcoder.com {kw} 面经",
        "site:www.nowcoder.com/discuss {kw} 面经",
        "site:www.nowcoder.com/feed/main/detail {kw} 面经",
        "牛客 {kw} 面经",
        "牛客 {kw} 面试经验",
        "{kw} 牛客 面经",
        "nowcoder {kw} 面经",
        "nowcoder {kw} interview",
    ]


def _xiaohongshu_templates() -> list[str]:
    return [
        "小红书 {kw} 面经",
        "小红书 {kw} 面试经验",
        "site:xiaohongshu.com {kw} 面经",
        "site:xiaohongshu.com {kw} 面试经验",
        "{kw} 小红书 面经",
    ]


def _douyin_templates() -> list[str]:
    return [
        "抖音 {kw} 面经",
        "抖音 {kw} 面试经验",
        "site:douyin.com {kw} 面经",
        "site:douyin.com {kw} 面试经验",
        "{kw} 抖音 面经",
    ]


def _clean_keywords(values: list[str] | None) -> list[str]:
    cleaned: list[str] = []
    for value in values or []:
        text = str(value).strip()
        if text:
            cleaned.append(text)
    return cleaned


def _clean_platforms(values: list[str] | None) -> list[str]:
    allowed = ("牛客", "小红书", "抖音")
    platforms: list[str] = []
    for value in values or []:
        text = str(value).strip()
        for name in allowed:
            if name in text and name not in platforms:
                platforms.append(name)
    return platforms


def _deduplicate_queries(queries: list[SearchQuery]) -> list[SearchQuery]:
    seen: set[tuple[str, str]] = set()
    result: list[SearchQuery] = []
    for item in queries:
        key = (item.platform, item.query)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _is_ascii_text(text: str) -> bool:
    return all(ord(ch) < 128 for ch in text)
