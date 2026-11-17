"""Deterministic technical-term extraction for evidence matching.

Why this exists: the 50 curated JDs were imported without `normalized_value`
and without JobTag rows. The previous matcher therefore compared a candidate fact
against the *entire* requirement sentence ("掌握Python/C++/Java之一"), which almost
never appears verbatim, so every technical requirement looked unsupported and the
retrieval order fell back to `job.id`.

This module only recognises technology names that are literally present in the
text. It never infers proficiency, seniority or "used in production": callers keep
deciding SUPPORTED/PARTIAL from the fact's own category and evidence state.

OR chains ("Python/C++/Java之一") need no special parsing: each alternative is
extracted as its own term, so matching *any* of them is a match.
"""
import re
from interview_api.modules.public_career.fingerprints import normalize_text

# canonical term -> literal surface forms. ASCII forms are matched with word
# boundaries so "Java" never matches "JavaScript"; Chinese forms are plain
# substrings because those scripts have no word separator.
TERMS: dict[str, tuple[str, ...]] = {
    "java": ("java",),
    "python": ("python",),
    "go": ("golang", "go语言"),
    "c++": ("c++", "cpp"),
    "c": ("c语言",),
    "c#": ("c#", "csharp"),
    "javascript": ("javascript", "js"),
    "typescript": ("typescript", "ts"),
    "spring": ("spring", "springboot", "spring boot", "spring cloud"),
    "fastapi": ("fastapi",),
    "django": ("django",), "flask": ("flask",),
    "mysql": ("mysql",),
    "postgresql": ("postgresql", "postgres", "pgsql"),
    "redis": ("redis",),
    "kafka": ("kafka",),
    "rabbitmq": ("rabbitmq",),
    "elasticsearch": ("elasticsearch",),
    "mongodb": ("mongodb", "mongo"),
    "clickhouse": ("clickhouse",),
    "docker": ("docker",),
    "kubernetes": ("kubernetes", "k8s"),
    "linux": ("linux",),
    "git": ("git",),
    "vue": ("vue", "vue3"),
    "react": ("react",),
    "unity": ("unity",),
    "llm": ("大模型", "llm", "transformer", "注意力机制"),
    "rag": ("rag", "检索增强", "向量检索", "混合检索", "bm25", "rrf", "rerank", "重排"),
    "agent": ("agent", "智能体", "mcp", "tool calling", "function calling", "langgraph"),
    "langchain": ("langchain",),
    "vector_db": ("milvus", "faiss", "向量数据库", "qdrant", "pinecone"),
    "prompt": ("prompt", "提示词"),
    "fine_tuning": ("微调", "sft", "lora", "强化学习", "rlhf"),
    "machine_learning": ("机器学习", "深度学习", "pytorch", "tensorflow"),
    "algorithm": ("算法", "数据结构", "leetcode"),
    "test_automation": ("自动化测试", "pytest", "selenium", "jmeter", "接口测试", "测试开发", "单元测试"),
    "network": ("tcp", "http协议", "网络协议", "osi"),
    "os": ("操作系统", "内核", "驱动"),
    "security": ("安全", "渗透测试", "漏洞", "加密"),
    "embedded": ("嵌入式", "单片机", "rtos"),
    "big_data": ("hadoop", "spark", "flink", "hive"),
    "message_queue": ("消息队列", "消息中间件", "异步削峰"),
    "microservice": ("微服务", "分布式", "服务治理"),
    "performance": ("性能优化", "高并发", "压测", "jvm调优"),
}

_ASCII = re.compile(r"^[a-z0-9+#.]+$")


def _pattern(form: str) -> re.Pattern:
    escaped = re.escape(form)
    if _ASCII.match(form):
        return re.compile(r"(?<![a-z0-9_])" + escaped + r"(?![a-z0-9_])")
    return re.compile(escaped)


PATTERNS = {term: [(_pattern(form), form) for form in forms] for term, forms in TERMS.items()}


def technical_terms(text) -> set[str]:
    """Canonical technology names literally present in `text`."""
    folded = normalize_text(text or "")
    if not folded:
        return set()
    found = set()
    for term, pairs in PATTERNS.items():
        if any(pattern.search(folded) for pattern, _ in pairs):
            found.add(term)
    return found


def requirement_topics(requirement) -> set[str]:
    """Explicit normalized topics win; otherwise the statement's own wording."""
    explicit = requirement.get("normalized_value")
    if explicit:
        terms = technical_terms(explicit)
        if terms:
            return terms
    return technical_terms(requirement.get("statement", ""))


def profile_topics(facts) -> set[str]:
    """Every technology the candidate's own material literally names."""
    terms = set()
    for fact in facts:
        terms |= technical_terms(fact.get("statement", ""))
        label = fact.get("label")
        if label:
            terms |= technical_terms(label)
    return terms
