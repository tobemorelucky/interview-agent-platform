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
    if policy.get("source_type") == "CANDIDATE_PROFILE" and target in TOPICS and target not in mentioned_topics(artifact.question):
        raise AppError("INTERVIEW_TOPIC_REQUIRED", "新问题未围绕已选择的技术主题", 422)
    if wording_fingerprint(artifact.question) in pacing["asked_wording_fingerprints"]:
        raise AppError("INTERVIEW_REPEATED_QUESTION", "不能重复已经问过的问题", 422)
