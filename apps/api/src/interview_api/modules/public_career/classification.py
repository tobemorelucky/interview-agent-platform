"""Deterministic job-direction classification: title first, duties second.

The old public filter matched a direction keyword anywhere in the JD body, so a
backend job that merely mentioned "Agent" landed under AI, and "JavaScript" hit
"Java". A filter is only useful if the job really is that kind of job, so this
module classifies instead of matching:

* the **title** carries the main direction (it is what the employer called the
  role);
* the **core duties** section confirms or supplies it when the title is generic;
* the **qualifications / bonus** section can only add a secondary tag, never
  decide the primary direction;
* Java and JavaScript are separated explicitly, and a test-development title is
  never overruled by an AI word in its duties.

No model, no network: the same JD text always yields the same labels, and every
label carries the terms that produced it so the UI can explain itself.
"""
import re

TITLE_WEIGHT = 8
DUTY_WEIGHT = 2
QUALIFICATION_WEIGHT = 1
# A secondary direction must be a real part of the job, not one passing mention.
SECONDARY_RATIO = 0.6

TITLE, DUTY, QUALIFICATION = "TITLE", "DUTY", "QUALIFICATION"

RULES = {
    "ai_agent": dict(label="AI / Agent 应用",
        terms=("ai", "大模型应用", "大模型", "llm", "agent", "智能体", "rag", "检索增强", "langchain",
               "langgraph", "机器学习", "深度学习", "算法工程", "算法", "nlp", "推荐系统", "ai应用", "ai 应用")),
    "java": dict(label="Java 后端",
        terms=("java", "jvm", "spring", "springboot", "spring boot", "mybatis", "dubbo")),
    "backend": dict(label="后端开发",
        terms=("后端", "服务端", "后台开发", "微服务")),
    "python": dict(label="Python",
        terms=("python", "django", "flask", "fastapi")),
    "frontend": dict(label="前端",
        terms=("前端", "vue", "react", "angular", "h5", "小程序", "typescript", "css")),
    "test": dict(label="测试开发",
        terms=("测试开发", "自动化测试", "测试工程", "性能测试", "接口测试", "测试", "qa", "质量保障")),
    "data": dict(label="数据",
        terms=("数据开发", "数据仓库", "数仓", "大数据", "hadoop", "spark", "flink", "etl",
               "数据分析", "数据挖掘", "bi", "指标")),
    "embedded": dict(label="嵌入式 / 系统",
        terms=("嵌入式", "单片机", "驱动开发", "内核", "rtos", "c++", "c语言", "底层软件")),
    "ops": dict(label="运维 / SRE",
        terms=("运维", "sre", "devops", "kubernetes", "k8s", "容器平台")),
    "security": dict(label="安全",
        terms=("安全", "渗透测试", "漏洞", "风控算法")),
    "mobile": dict(label="客户端",
        terms=("android", "ios", "客户端开发", "鸿蒙")),
}

# Terms that would otherwise be a false hit. They are removed before matching so
# "JavaScript" can never satisfy "Java", and "Agentic" is not read as "Agent".
DECOYS = {
    "java": re.compile(r"javascript|java\s*script|node\.?js", re.I),
    "ai_agent": re.compile(r"agentic", re.I),
    "test": re.compile(r"考点|测试环境搭建$", re.I),
}

_ASCII_BOUNDARY = re.compile(r"[a-z0-9]")


def _normalise(text):
    return (text or "").casefold()


def _matches(text, term, direction):
    clean = DECOYS[direction].sub(" ", text) if direction in DECOYS else text
    start = 0
    while True:
        index = clean.find(term, start)
        if index < 0:
            return False
        # ASCII terms need a boundary so "java" does not match "javafx"-style noise.
        if _ASCII_BOUNDARY.match(term[:1]) and term.isascii():
            before = clean[index - 1] if index else " "
            after = clean[index + len(term)] if index + len(term) < len(clean) else " "
            if _ASCII_BOUNDARY.match(before) or _ASCII_BOUNDARY.match(after):
                start = index + 1
                continue
        return True


def _hits(text, direction):
    return [term for term in RULES[direction]["terms"] if _matches(_normalise(text), term, direction)]


# A role that is explicitly a testing role stays a testing role even when its
# title also says "AI": 测试开发 and AI 应用开发 are different jobs.
TITLE_ROLE_ANCHORS = ((re.compile(r"测试|qa|质量保障", re.I), "test"),)
# "后端/服务端" describes a layer, not a stack. When the title also names a real
# stack, that stack is what a user filtering by direction is looking for.
GENERIC_DIRECTIONS = {"backend"}
_HEAD = re.compile(r"^(?P<head>[^（(]*)(?P<tail>[（(].*)?$", re.S)


def _title_parts(title):
    """The role noun outranks the parenthetical qualifier."""
    match = _HEAD.match(title or "")
    return (match.group("head") or ""), (match.group("tail") or "")


def _anchor_bonus(title, direction):
    return TITLE_WEIGHT if any(key == direction and pattern.search(title or "")
                               for pattern, key in TITLE_ROLE_ANCHORS) else 0


def classify(title, responsibilities=(), qualifications=()):
    """Return the main direction plus the labels the job genuinely carries."""
    duty_text = " ".join(responsibilities or ())
    qualification_text = " ".join(qualifications or ())
    head, tail = _title_parts(title)
    scores, evidence = {}, {}
    for direction in RULES:
        head_hits = _hits(head, direction)
        tail_hits = _hits(tail, direction)
        title_hits = list(dict.fromkeys(head_hits + tail_hits))
        duty_hits = _hits(duty_text, direction)
        qualification_hits = _hits(qualification_text, direction)
        score = (TITLE_WEIGHT * len(head_hits) + TITLE_WEIGHT // 2 * len(tail_hits)
                 + _anchor_bonus(title, direction)
                 + DUTY_WEIGHT * len(duty_hits) + QUALIFICATION_WEIGHT * len(qualification_hits))
        if score:
            scores[direction] = score
            evidence[direction] = dict(title=title_hits, duties=duty_hits, qualifications=qualification_hits)
    if not scores:
        return dict(primary=None, label=None, directions=[], scores={}, evidence={},
                    basis="UNCLASSIFIED", reason="岗位名称与职责都没有可识别的方向词")
    primary = max(scores, key=lambda key: (scores[key], key not in GENERIC_DIRECTIONS,
                                           -list(RULES).index(key)))
    top = scores[primary]
    # The primary is always a label; a second label has to be a real part of the job.
    directions = [primary] + [key for key in sorted(scores, key=lambda key: -scores[key])
                              if key != primary and scores[key] >= max(DUTY_WEIGHT * 2, top * SECONDARY_RATIO)]
    basis = TITLE if evidence[primary]["title"] else DUTY if evidence[primary]["duties"] else QUALIFICATION
    return dict(primary=primary, label=RULES[primary]["label"], directions=directions,
                scores=scores, evidence=evidence, basis=basis,
                reason=f"主要方向按{'岗位名称' if basis == TITLE else '核心职责'}判定")


def direction_labels(job):
    return [RULES[key]["label"] for key in job.get("directions") or ()]


def matches(direction, classified):
    """Filter on the job's directions, not on a keyword inside its text.

    A multi-direction job answers each of its real directions; a single passing
    mention in the duties is neither a primary direction nor a label.
    """
    return not direction or direction in (classified.get("directions") or ())
