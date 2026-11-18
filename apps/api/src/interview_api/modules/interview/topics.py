"""Deterministic pacing from persisted questions, never model-maintained counts.

This is interview presentation/selection policy, NOT candidate eligibility or a
semantic evidence validator. Unknown subjects remain unknown. No mutable resume
or public data is read while a session is running.
"""
from collections import Counter
import re

from interview_api.core.exceptions import AppError
from interview_api.modules.public_career.fingerprints import compute_content_fingerprint

POLICY_VERSION = "technical-topics@1.0.0"
MAX_CONSECUTIVE = 3
TECHNICAL_WEIGHTS = {"PROJECT": .4, "FOUNDATION": .3, "JOB_TECHNICAL": .2, "SCENARIO": .1}
TOPICS = {
    "data_security": r"数据安全|敏感信息|脱敏|权限|授权|认证|审计|访问控制",
    "rag_retrieval": r"\brag\b|\brrf\b|\bbm25\b|\bdense\b|检索|召回|向量|重排|排名融合",
    "kafka_reliability": r"\bkafka\b|消息队列|消息可靠|消息消费|异步下单|重复消费|不丢单",
    "redis": r"\bredis\b|缓存|秒杀|分布式锁",
    "mysql_consistency": r"\bmysql\b|数据库|事务|隔离级别|一致性|索引",
    "agent": r"\bagent\b|工具调用|工具工作流|工具执行|执行工具|多智能体|智能体",
    "llm": r"大模型|语言模型|\btransformer\b|注意力|微调|\bllm\b",
    "java": r"\bjava\b|\bjvm\b|\bspring\b|线程|垃圾回收",
    "python": r"\bpython\b|\bgil\b|协程",
    "system_design": r"系统设计|故障|排查|限流|熔断|可用性|部署|docker|监控",
    # R126: ground the seven new Skills can legally test. Specific phrases only —
    # a bare "网络"/"测试" would swallow unrelated questions, and "排序" belongs to
    # both recommendation and algorithms, so neither pattern may use it alone.
    "frontend": r"\bvue\b|\breact\b|\btypescript\b|\bjavascript\b|前端|浏览器|组件库|首屏",
    "testing": r"测试用例|自动化测试|单元测试|接口测试|性能测试|回归测试|压力测试|测试开发|质量保障|\bselenium\b|\bpytest\b|\bjmeter\b",
    "data_engineering": r"数仓|数据仓库|\betl\b|\bspark\b|\bflink\b|\bhive\b|数据建模|数据管道|实时计算|离线任务|数据治理",
    "recommendation": r"推荐系统|推荐算法|协同过滤|召回策略|\bctr\b|特征工程|排序模型|机器学习|深度学习",
    "network_os": r"三次握手|四次挥手|\btcp\b|\budp\b|操作系统|进程|线程池|内存管理|文件系统|\blinux\b|内核|虚拟内存|网络协议",
    "algorithm": r"动态规划|二叉树|链表|哈希表|时间复杂度|空间复杂度|排序算法|数据结构|算法题",
}
# The literal words the NEXT-question gate actually accepts, per topic. They are
# published to the interviewer model so "explicitly name the topic" is a
# checkable instruction rather than a guess; the gate itself is unchanged.
TOPIC_TERMS = {
    "data_security": ["数据安全", "敏感信息", "脱敏", "权限", "审计"],
    "rag_retrieval": ["RAG", "检索", "召回", "向量", "重排"],
    "kafka_reliability": ["Kafka", "消息队列", "消息可靠性", "重复消费"],
    "redis": ["Redis", "缓存", "分布式锁"],
    "mysql_consistency": ["MySQL", "数据库", "事务", "隔离级别", "索引"],
    "agent": ["Agent", "工具调用", "多智能体"],
    "llm": ["大模型", "LLM", "Transformer", "微调", "注意力"],
    "java": ["Java", "JVM", "Spring", "线程", "垃圾回收"],
    "python": ["Python", "GIL", "协程"],
    "system_design": ["系统设计", "故障", "排查", "限流", "熔断", "可用性", "监控"],
    "frontend": ["前端", "Vue", "React", "TypeScript", "浏览器"],
    "testing": ["测试用例", "自动化测试", "接口测试", "性能测试", "质量保障"],
    "data_engineering": ["数仓", "ETL", "Spark", "Flink", "数据建模"],
    "recommendation": ["推荐系统", "协同过滤", "特征工程", "排序模型", "机器学习"],
    "network_os": ["操作系统", "TCP", "进程", "线程池", "内存管理"],
    "algorithm": ["数据结构", "动态规划", "时间复杂度", "哈希表"],
}
BACKGROUND = {
    "education": r"学历|学校|毕业院校|哪所大学|本科|硕士|博士|研究生|教育背景|专业背景|什么专业|就读|所学专业",
    "motivation": r"为什么(?:投|应聘|加入|学计算机|转行做计算机)|投递动机|求职动机|职业规划",
    "introduction": r"自我介绍|介绍一下自己|介绍下自己|个人背景|求职背景",
    "behavior": r"优缺点|个人优势|个人劣势|团队合作|团队协作|抗压|工作压力|冲突处理|加班|工作制度|工作强度",
}
HARD_CONDITION = r"学历|学位|博士|硕士|本科|毕业|院校|专业要求|专业背景|专业为|相关专业|\bdegree\b|\bmajor\b"
CHOICE = r"为什么(?:会|要|想)?选择([^，。？！；,\n.?!;]*)"
CAREER_CHOICE = (
    r"^(?:加入|应聘|投递|入职|(?:我们|贵|本)(?:公司|团队)(?!的))"
    r"|(?:公司|岗位|职位|团队|雇主|职业|行业|专业|学校)\s*$"
)
TOPIC_SIGNOFF = re.compile(r"^\s*[^，。？！；,.?!;\n]{1,24}?(?:这块|这部分|这个话题)"
                           r"先(?:聊到这(?:里)?|放一放|告一段落)[，。,.]\s*(?=\S)")
# Resume sections that can legitimately open an interview. Education/contact
# facts stay out: a project opening must come from real work, not from a degree.
PROJECT_FIELDS = ("projects", "work_experience", "internship_experience")
# What makes a first question a *project* opening rather than a topic switch:
# it visibly refers to something the candidate actually did. The frozen fact it
# is bound to still supplies the technical target, so this is a reference test,
# not a replacement for provenance.
PROJECT_REFERENCE = re.compile(
    r"项目|作品|实习|工作经历|简历|这段经历|你(?:做过|写的|实现的|负责的)")


def is_project_fact(field_path):
    return str(field_path or "").split(".")[0] in PROJECT_FIELDS


def refers_to_candidate_work(text):
    return bool(PROJECT_REFERENCE.search((text or "").casefold()))


def classify(text):
    text = (text or "").casefold()
    for topic, pattern in BACKGROUND.items():
        if re.search(pattern, text):
            return {"category": "BACKGROUND", "topic": topic}
        if topic == "motivation" and any(
            re.search(CAREER_CHOICE, choice.strip()) or not mentioned_topics(choice)
            for choice in re.findall(CHOICE, text)
        ):
            # Only technical evidence in the object of this choice can exempt it.
            # An unrelated technical phrase elsewhere cannot exempt a career question.
            return {"category": "BACKGROUND", "topic": topic}
    text = TOPIC_SIGNOFF.sub("", text, count=1)
    # A project opening can mention several stacks; its intent is still one opening.
    if re.search(r"介绍.*项目|项目.*(?:背景|职责|负责|技术栈)|代表性.*项目", text):
        return {"category": "PROJECT", "topic": "project_opening"}
    hits = [(match.start(), topic) for topic, pattern in TOPICS.items()
            if (match := re.search(pattern, text, re.ASCII))]
    topic = min(hits)[1] if hits else "unknown"
    category = "SCENARIO" if re.search(r"故障|排查|如果|如何设计|trade.?off|取舍|失败|异常", text) else "FOUNDATION"
    return {"category": category, "topic": topic}


def is_hard_condition(text):
    return bool(re.search(HARD_CONDITION, (text or "").casefold()))


def mentioned_topics(text):
    # Technical ASCII names commonly touch Chinese text (e.g. Kafka的可靠性).
    # Unicode word boundaries treat both as word characters and miss the topic.
    text = TOPIC_SIGNOFF.sub("", (text or "").casefold(), count=1)
    return [topic for topic, pattern in TOPICS.items() if re.search(pattern, text, re.ASCII)]


def topic_terms(topic):
    """Literal wording the NEXT gate accepts for this topic (may be empty)."""
    return list(TOPIC_TERMS.get(topic, []))


def requirement_opening_question(statement, label):
    """Deterministic opening question: frozen requirement + frozen target label.

    Used only by the explicit REQUIREMENT fallback, so it must not invent any
    candidate experience. The label keeps the question inside the target's own
    topic, which is what the NEXT gate checks.
    """
    return (f"岗位要求里有一条：「{(statement or '').strip()}」。请结合你做过的一个项目，"
            f"讲讲你在{label}方面的具体做法，并说明遇到过一次什么问题、最后怎么解决的。")


# The scripted self-introduction opening. Server-authored, zero-model, and
# explicitly not a scored technical question: it exists so a real interview
# starts with "who are you" instead of a JD line, and it is excluded from
# technical aggregates by the same mechanism as end requests.
INTRODUCTION_QUESTION = "你好，我们先简单认识一下。请做一个简单的自我介绍，可以重点讲讲你做过哪些项目或实习。"


def introduction_opening_question():
    return INTRODUCTION_QUESTION


def topic_diagnostic(question, target_topic, **extra):
    """Everything needed to tell "model drifted" from "gate missed the wording".

    The raw model response is deliberately not persisted, so a rejected question
    used to leave no trace at all. This record keeps the wording that failed,
    without turning the audit trail into a transcript of model output.
    """
    import hashlib
    text = question or ""
    result = dict(target_topic=target_topic, expected_terms=topic_terms(target_topic),
                  matched_topics=mentioned_topics(text), question_excerpt=text[:160],
                  question_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())
    result.update(extra)
    return result


def question_topic(question):
    refs = getattr(question, "candidate_refs_json", None) or {}
    return refs.get("topic_policy") or classify(getattr(question, "question", ""))


def wording_fingerprint(text):
    return compute_content_fingerprint(re.sub(r"[\W_]+", "", text.casefold()))


def coverage(questions, budget):
    ordered = sorted(questions, key=lambda q: (getattr(q, "question_index", 0), q.id or 0))
    entries = []
    by_id = {}
    for q in ordered:
        entry = dict(question_topic(q))
        parent = by_id.get(q.parent_question_id)
        # A parent-bound follow-up often says “刚才的重试” without repeating
        # Kafka. Keep the parent's established subject, not a fresh unknown.
        if (parent and parent['topic'] in TOPICS and entry['category'] != 'BACKGROUND'
                and q.question_kind in ('FOLLOW_UP', 'DEEP_DIVE')):
            entry['topic'] = parent['topic']
        entries.append(entry)
        by_id[q.id] = entry
    main = [entry for q, entry in zip(ordered, entries) if not q.parent_question_id]
    recent = entries[-1]["topic"] if entries else None
    consecutive = 0
    for entry in reversed(entries):
        if entry["topic"] != recent:
            break
        consecutive += 1
    background_count = sum(entry["category"] == "BACKGROUND" for entry in main)
    background_limit = min(2, max(1, int(budget * .15)))
    # First five main questions: at most one, even in a long session.
    if len(main) < 5:
        background_limit = min(background_limit, 1)
    blocked = sorted({e["topic"] for e in entries if e["category"] == "BACKGROUND"})
    if recent != "unknown" and consecutive >= MAX_CONSECUTIVE:
        blocked = sorted(set(blocked) | {recent})
    return dict(policy_version=POLICY_VERSION, category_counts=dict(Counter(e["category"] for e in main)),
        topic_counts=dict(Counter(e["topic"] for e in entries)), main_question_count=len(main),
        background_count=background_count, background_limit=background_limit,
        recent_topic=recent, consecutive_count=consecutive, blocked_topics=blocked,
        recent_questions=[getattr(q, "question", "") for q in ordered[-4:]],
        asked_wording_fingerprints=[wording_fingerprint(getattr(q, "question", "")) for q in ordered],
        background_allowed=background_count < background_limit,
        priorities=["LATEST_ANSWER", "EVALUATION_GAPS", "PROJECT", "JD_TECHNICAL", "UNCOVERED_TECHNICAL", "PUBLIC_INTENT", "BACKGROUND_LAST"],
        category_weights=TECHNICAL_WEIGHTS)


def validate_topic(artifact, policy):
    pacing = policy.get("topic_coverage")
    if not pacing:
        return
    actual = classify(artifact.question)
    if actual["category"] == "BACKGROUND":
        if (actual["topic"] == "education" or not pacing["background_allowed"] or actual["topic"] in pacing["blocked_topics"] or
                policy.get("target_category") != "BACKGROUND"):
            raise AppError("INTERVIEW_BACKGROUND_LIMIT", "本场背景类题目已覆盖，请切换技术问题", 422)
    if actual["topic"] in pacing["blocked_topics"]:
        raise AppError("INTERVIEW_TOPIC_COOLDOWN", "该主题已连续深入，请切换其他技术主题", 422)
    target = policy.get("target_topic")
    if (policy.get("source_type") == "CANDIDATE_PROFILE" and target in TOPICS
            and target not in mentioned_topics(artifact.question) and not policy.get("project_opening")):
        raise AppError("INTERVIEW_TOPIC_REQUIRED", "新问题未围绕已选择的技术主题", 422)
    if wording_fingerprint(artifact.question) in pacing["asked_wording_fingerprints"]:
        raise AppError("INTERVIEW_REPEATED_QUESTION", "不能重复已经问过的问题", 422)
