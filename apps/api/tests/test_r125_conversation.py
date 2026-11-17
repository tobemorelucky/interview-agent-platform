"""R125 P0-B: the mock interview must sound like an interviewer talking.

Three scripted conversations, each at least five interviewer turns, all driven by
the real session state: the opening comes from a real resume project, every
follow-up is decided from the previous answer and its evaluation, and every
question still passes the frozen decision contract.

Zero model HTTP: the wording is scripted, the decisions are not.
"""
import asyncio
import json

from interview_api.infrastructure.models.router import ModelRouter
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.schemas import CreateSessionRequest
from r125_support import ScriptedEvaluation, ScriptedInterviewer, converse, seed_job, seed_persona

RAG_RESUME = """Demo candidate — R125 脚本夹具，非真实个人信息
技能
Python LangChain Milvus Prompt Engineering
项目经历
智能客服知识库：用 LangChain 做检索增强，接 Milvus 向量库，由 Agent 调度工具回答售后问题
教育背景
示例大学 计算机科学 本科
工作经历
示例公司 AI 应用开发
"""

JAVA_RESUME = """Demo candidate — R125 脚本夹具，非真实个人信息
技能
Java Spring Boot MySQL Redis Kafka
项目经历
订单秒杀系统：使用 Java Spring Boot 与 Redis 做库存扣减，用 Kafka 异步削峰，处理重复消费与补偿
教育背景
示例大学 计算机科学 本科
工作经历
示例公司 后端开发
"""

RAG_FACTS = dict(
    skills=[("LangChain", "LangChain Milvus")],
    projects=[("智能客服知识库", "智能客服知识库")],
    education=[("示例大学", "示例大学 计算机科学 本科")],
    work_experience=[("示例公司", "示例公司 AI 应用开发")],
)
JAVA_FACTS = dict(
    skills=[("Java", "Java Spring Boot MySQL Redis Kafka")],
    projects=[("订单秒杀系统", "订单秒杀系统")],
    education=[("示例大学", "示例大学 计算机科学 本科")],
    work_experience=[("示例公司", "示例公司 后端开发")],
)

RAG_JD = ["熟悉 RAG 检索增强与向量检索", "能用 LangGraph 或 Agent 编排工具调用",
          "熟悉大模型提示词与结构化输出", "熟悉 Python 异步编程与工程可靠性"]
JAVA_JD = ["熟悉 Java 并发与 JVM", "熟悉 MySQL 事务与索引", "熟悉 Redis 缓存与分布式锁",
           "熟悉 Kafka 消息可靠性与重复消费"]


async def _run(factory, *, resume, facts, jd, job_title, skill, plan, rules, answers):
    """Run one continuous conversation against a real session."""
    user_id = await seed_persona(factory, resume, facts)
    job = await seed_job(factory, job_title, jd)
    main, evaluator = ScriptedInterviewer(plan), ScriptedEvaluation(rules)
    core = MockInterviewService(factory, ModelRouter({"interview_main": main, "answer_evaluation": evaluator}))
    session = await core.create(user_id, CreateSessionRequest(company_id=job["company_id"],
        job_id=job["job_id"], interviewer_skill_id=skill, adaptive_interview=True, question_count=len(plan)))
    transcript = await converse(core, user_id, session["id"], answers)
    return dict(transcript=transcript, main=main, evaluator=evaluator, core=core,
                user_id=user_id, session=session)


# ── Scenario 1: AI Agent / RAG, a shallow answer that has to be opened up ──
RAG_PLAN = [
    dict(strategy="NEXT", topic="rag_retrieval", source="CANDIDATE_PROFILE",
         text="先聊聊你简历里那个智能客服知识库吧，你在这个项目里主要负责哪一部分？"),
    dict(strategy="FOLLOW_UP", topic="rag_retrieval",
         text=lambda p: "那你们整体是怎么跑起来的？从用户提问到给出回答，中间经过哪几步？",
         reason="ANSWER_TOO_GENERAL"),
    dict(strategy="FOLLOW_UP", topic="rag_retrieval",
         text=lambda p: "检索回来的内容是直接给模型的吗？大概给几条，有没有做过筛选？",
         reason="MISSING_MECHANISM"),
    dict(strategy="NEXT", topic="agent",
         text="换个方向。你们这个客服系统里，Agent 是怎么决定这次要调哪个工具的？"),
    dict(strategy="FOLLOW_UP", topic="agent",
         text=lambda p: "如果它选错了工具，或者调用的参数不对，你们现在会怎么兜住？",
         reason="VERIFY_TRADEOFF"),
]
RAG_RULES = [
    dict(match="信息检索", state="INSUFFICIENT_EVIDENCE", score=2,
         evaluation="回答停在结论层面，还没有说明自己负责的范围。",
         missing=["你在这个项目里具体负责的范围"], missing_mechanisms=["候选人本人负责的模块与整体链路"]),
    dict(match="我负责检索", state="INSUFFICIENT_EVIDENCE", score=2,
         evaluation="描述了岗位分工，但整体流程还是一句话。",
         missing=["从提问到回答的完整链路"], missing_mechanisms=["检索结果如何进入生成"]),
    dict(match="拼到 prompt", state="PARTIALLY_DEMONSTRATED", score=3,
         evaluation="说清了注入方式，没有说筛选和约束。",
         missing=["不相关内容如何过滤", "如何约束模型不编造"], missing_mechanisms=["无关片段的过滤策略"]),
    dict(match="LangGraph", state="PARTIALLY_DEMONSTRATED", score=3,
         evaluation="提到了编排方式，工具选择的判断依据还不清楚。",
         missing=["工具选择的判断依据"], missing_mechanisms=["工具选择与失败兜底"]),
    dict(match="兜底", state="PARTIALLY_DEMONSTRATED", score=4,
         evaluation="兜底路径清楚，异常判定条件还可以再具体。",
         missing=["异常判定条件"], missing_mechanisms=[]),
]
RAG_ANSWERS = [
    "我就是做了一个 RAG，然后可以信息检索。",
    "我主要负责检索这块，用户问问题就去向量库里找相关的内容，然后交给模型回答。",
    "检索出来直接拼到 prompt 里，一般给 top3，怎么保证不乱编……就是让它根据材料回答。",
    "用 LangGraph 编排的，先把问题分类，再决定走哪个工具。",
    "有兜底，工具调不通就走人工，另外我们会记日志。",
]


def test_shallow_rag_answer_is_clarified_before_it_is_deepened(database_url):
    async def run(factory):
        result = await _run(factory, resume=RAG_RESUME, facts=RAG_FACTS, jd=RAG_JD,
                            job_title="AI Agent 应用开发工程师", skill="ai_agent_application",
                            plan=RAG_PLAN, rules=RAG_RULES, answers=RAG_ANSWERS)
        transcript = result["transcript"]
        interviewer = [row for row in transcript if row["role"] == "interviewer"]
        assert len(interviewer) >= 5, transcript

        opening = interviewer[0]["text"]
        # Opens from the candidate's own project, not from the job description.
        assert "项目" in opening or "简历" in opening or "知识库" in opening
        for statement in RAG_JD:
            assert statement not in opening
        assert "岗位要求" not in opening
        # One main question, not a four-part interrogation.
        assert opening.count("？") == 1

        # A shallow "I did a RAG" is first met by asking about scope and flow.
        second = interviewer[1]["text"]
        assert any(word in second for word in ("负责", "流程", "跑起来", "哪一步"))
        assert "缺失" not in second and "缺口" not in second
        # The gap wording of the evaluation never becomes the next question.
        for row in interviewer[1:]:
            assert "检索结果如何进入生成" not in row["text"]
        # The topic still moved on instead of looping on one knowledge point.
        assert len({row["kind"] for row in interviewer}) >= 2
        assert result["main"].made[0]["source"] == "CANDIDATE_PROFILE"
        assert result["main"].made[0]["strategy"] == "NEXT"
        # A deeper question is earned, not assumed: it only appears after the flow.
        assert any("prompt" in row["text"] or "几条" in row["text"] for row in interviewer)
    asyncio.run(_with_db(database_url, run))


# ── Scenario 2: Java backend with real depth, then a deliberate topic switch ──
JAVA_PLAN = [
    dict(strategy="NEXT", topic="redis", source="CANDIDATE_PROFILE",
         text="你简历里那个订单秒杀系统，聊聊你负责的那部分吧，库存这块是怎么扛住并发的？"),
    dict(strategy="NEXT", topic="kafka_reliability",
         text="你刚提到用 Kafka 削峰，那重复消费这块你们是怎么处理的？"),
    dict(strategy="FOLLOW_UP", topic="kafka_reliability",
         text=lambda p: "幂等这块再确认一下：如果消费成功、但下游事务还没提交就挂了，会怎样？",
         reason="VERIFY_TRADEOFF"),
    dict(strategy="DEEP_DIVE", topic="kafka_reliability",
         text=lambda p: "去重表本身也会成为热点，你们怎么避免它变成瓶颈？",
         reason="MISSING_MECHANISM"),
    dict(strategy="NEXT", topic="mysql_consistency",
         text="换个话题。你们 MySQL 的慢查询，一般怎么定位到具体是哪条 SQL？"),
]
JAVA_RULES = [
    # A well-answered goal closes: the next turn must switch, not mine the answer.
    dict(match="Lua", state="DEMONSTRATED_IN_SESSION", score=5,
         evaluation="并发扣减的原子性和限流都说清了。",
         covered=["Lua 原子扣减", "库存预热"], missing=[], missing_mechanisms=[]),
    dict(match="幂等", state="PARTIALLY_DEMONSTRATED", score=4,
         evaluation="幂等思路清楚，事务边界没讲透。",
         missing=["ACK 与事务提交的边界"], missing_mechanisms=["ACK 与事务提交边界"]),
    dict(match="事务没提交", state="PARTIALLY_DEMONSTRATED", score=3,
         evaluation="说清了失败场景，恢复路径还模糊。",
         missing=["消息重投后的恢复路径"], missing_mechanisms=["重投后的恢复路径"]),
    dict(match="去重表", state="PARTIALLY_DEMONSTRATED", score=4,
         evaluation="热点问题意识到位，分片方案还可以更具体。",
         missing=["去重表的分片方式"], missing_mechanisms=[]),
    dict(match="慢查询", state="PARTIALLY_DEMONSTRATED", score=4,
         evaluation="定位手段完整。", missing=["索引选择性的判断"], missing_mechanisms=[]),
]
JAVA_ANSWERS = [
    "库存我用了 Redis 的 Lua 脚本做原子扣减，前面还有一层预校验，热点商品会先预热到本地缓存。",
    "重复消费我们用订单号做幂等键，先在去重表里插一条，唯一索引冲突就直接跳过。",
    "消费成功但事务没提交就挂了的话，消息会重投，重投时去重表已经有记录了，就会跳过，这块其实有漏的风险，我们后面加了状态机。",
    "去重表按订单号哈希分片，热点订单会单独走一个本地缓存加短锁，批量的走异步。",
    "慢查询先看 slow log，按执行时间和扫描行数排序，再用 explain 看是不是走了索引，最后结合业务看能不能改 SQL 或者加联合索引。",
]


def test_rich_java_answer_is_deepened_then_the_topic_moves_on(database_url):
    async def run(factory):
        result = await _run(factory, resume=JAVA_RESUME, facts=JAVA_FACTS, jd=JAVA_JD,
                            job_title="Java 后端开发工程师", skill="java_backend",
                            plan=JAVA_PLAN, rules=JAVA_RULES, answers=JAVA_ANSWERS)
        interviewer = [row for row in result["transcript"] if row["role"] == "interviewer"]
        assert len(interviewer) >= 5, result["transcript"]
        made = result["main"].made
        topics = [row["topic"] for row in made]
        assert len(set(topics)) >= 3, topics
        # A rich answer closes its goal: the next turn switches instead of asking
        # two scripted follow-ups to fill rounds.
        assert made[0]["source"] == "CANDIDATE_PROFILE"
        assert made[1]["strategy"] == "NEXT" and made[1]["topic"] != topics[0]
        # A thinner area is then deepened on the same topic before moving on.
        assert made[2]["strategy"] == "FOLLOW_UP" and made[3]["strategy"] == "DEEP_DIVE"
        assert topics[2] == topics[3] == topics[1] != topics[4]
        # No mechanical four-part template and no repeated opening formula.
        starts = {row["text"][:6] for row in interviewer}
        assert len(starts) == len(interviewer)
        for row in interviewer:
            assert row["text"].count("？") == 1
    asyncio.run(_with_db(database_url, run))


# ── Scenario 3: repeated "I don't know" must not be re-asked forever ──
UNKNOWN_PLAN = [
    dict(strategy="NEXT", topic="redis", source="CANDIDATE_PROFILE",
         text="简历里这个订单秒杀项目，缓存这块你实际做过吗？大概是怎么用的？"),
    dict(strategy="FOLLOW_UP", topic="redis",
         text=lambda p: "没关系。那这个项目里你主要参与了哪一块，能说个大概就行？",
         reason="ANSWER_TOO_GENERAL"),
    dict(strategy="FOLLOW_UP", topic="redis",
         text=lambda p: "好，那我们换成看问题的角度：如果让你设计，你会先考虑什么？",
         reason="NEEDS_EXAMPLE"),
    dict(strategy="NEXT", topic="mysql_consistency",
         text="那聊个别的。MySQL 的事务隔离级别，你平时用得多吗？"),
    dict(strategy="NEXT", topic="java",
         text="再换一个，Java 里你比较熟的是哪部分？线程池用过吗？"),
]
UNKNOWN_RULES = [
    dict(match="记不清", state="INSUFFICIENT_EVIDENCE", score=2,
         evaluation="候选人还没有给出可评价的技术内容。",
         missing=["本人参与的具体范围"], missing_mechanisms=["本人参与范围"]),
    dict(match="不知道", state="INSUFFICIENT_EVIDENCE", score=1,
         evaluation="本题没有得到技术内容。", missing=["与缓存相关的一次实际处理"],
         missing_mechanisms=["缓存使用经验"]),
    dict(match="不太清楚", state="INSUFFICIENT_EVIDENCE", score=1,
         evaluation="本题仍没有技术内容。", missing=["设计思路"], missing_mechanisms=["设计思路"]),
    dict(match="没做过", state="INSUFFICIENT_EVIDENCE", score=1,
         evaluation="没有相关经历，不宜继续追问。", missing=["事务隔离级别的使用"],
         missing_mechanisms=["事务隔离级别使用"]),
    dict(match="线程池", state="PARTIALLY_DEMONSTRATED", score=3,
         evaluation="说出了参数，缺少拒绝策略的取舍。", missing=["拒绝策略的选择"],
         missing_mechanisms=["拒绝策略"]),
]
UNKNOWN_ANSWERS = [
    "这块我参与得不多，具体细节记不清了。",
    "不知道。",
    "不太清楚，没印象了。",
    "这个我没做过。",
    "线程池用过，核心线程数、队列、最大线程数这些参数配过，拒绝策略没太注意。",
]


def test_repeated_unknown_is_dropped_instead_of_reworded(database_url):
    async def run(factory):
        result = await _run(factory, resume=JAVA_RESUME, facts=JAVA_FACTS, jd=JAVA_JD,
                            job_title="Java 后端开发工程师", skill="java_backend",
                            plan=UNKNOWN_PLAN, rules=UNKNOWN_RULES, answers=UNKNOWN_ANSWERS)
        interviewer = [row for row in result["transcript"] if row["role"] == "interviewer"]
        assert len(interviewer) >= 5, result["transcript"]
        topics = [row["topic"] for row in result["main"].made]
        # Two follow-ups on the same point are the budget; after that it switches.
        assert topics[:3] == ["redis"] * 3
        assert topics[3] != "redis" and topics[4] != topics[3]
        # The wording never repeats, so a "reworded" loop cannot masquerade as progress.
        assert len({row["text"] for row in interviewer}) == len(interviewer)
        # At most two follow-ups per knowledge point, by the pinned budget.
        assert sum(1 for row in result["main"].made[:3] if row["strategy"] == "FOLLOW_UP") <= 2
    asyncio.run(_with_db(database_url, run))


# ── Prompt versioning: new sessions only ──
def test_new_session_freezes_the_conversational_prompt_and_old_ones_keep_theirs(database_url):
    async def run(factory):
        user_id = await seed_persona(factory, RAG_RESUME, RAG_FACTS)
        job = await seed_job(factory, "AI Agent 应用开发工程师", RAG_JD)
        main, evaluator = ScriptedInterviewer(RAG_PLAN), ScriptedEvaluation(RAG_RULES)
        core = MockInterviewService(factory, ModelRouter({"interview_main": main, "answer_evaluation": evaluator}))
        session = await core.create(user_id, CreateSessionRequest(company_id=job["company_id"],
            job_id=job["job_id"], interviewer_skill_id="ai_agent_application",
            adaptive_interview=True, question_count=6))
        policy = session["context_snapshot"]["adaptive_policy"]
        assert policy["prompts"]["interview_main"]["version"] == "2.5.0"
        # The grading contract is deliberately untouched by this round.
        assert policy["prompts"]["answer_evaluation"]["version"] == "2.3.4"
        assert policy["fingerprint"] if "fingerprint" in policy else True

        from interview_api.modules.interview.adaptive_prompts import AdaptivePromptRegistry
        prompt = AdaptivePromptRegistry(policy).get("interview_main")
        assert "project" in prompt.system and "one main question" in prompt.system
        # An existing session keeps the text it was created with, even after this round.
        from interview_api.modules.interview.adaptive_prompts import AdaptivePromptRegistry
        from interview_api.modules.public_career.research.prompts import PromptDefinition
        frozen = PromptDefinition("interview_main", "2.3.3", "frozen 2.3.3 body")
        legacy = dict(policy, prompts={**policy["prompts"], "interview_main": dict(
            version=frozen.version, system=frozen.system, fingerprint=frozen.content_hash)})
        assert AdaptivePromptRegistry(legacy).get("interview_main").version == "2.3.3"
        assert AdaptivePromptRegistry(legacy).get("interview_main").system == "frozen 2.3.3 body"
    asyncio.run(_with_db(database_url, run))


async def _with_db(database_url, fn):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        return await fn(factory)
    finally:
        await engine.dispose()
