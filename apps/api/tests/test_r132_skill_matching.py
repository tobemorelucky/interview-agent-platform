"""R132: routing must not read generic words as a profession.

Agent Harness JDs mention "状态管理" and "性能优化"; a Java JD lists three
languages and one concurrency phrase. Those words are shared vocabulary, not
evidence of a frontend or Java-only profession.
"""
from interview_api.modules.interview.skill_recommendation import recommend


def job(title, requirements, responsibilities=()):
    return dict(job_id=1, title=title, job_family=None,
                responsibilities=list(responsibilities),
                requirements=[dict(id=i + 1, statement=text) for i, text in enumerate(requirements)],
                tags=[])


def test_agent_harness_is_not_frontend():
    result = recommend(job("Agent Harness研发工程师",
        ["理解ReAct、CoT、Plan-and-Execute及Agent基本原理",
         "熟悉PyTorch、LangChain或AutoGen等", "具备Agent系统设计和工程落地能力"],
        ["研发Agent Harness架构和状态管理、沙箱、执行约束",
         "实现上下文压缩、人机反馈闭环和长期任务可控执行"]))
    assert result["recommended_skill_id"] == "ai_agent_application"
    assert result["recommended_skill_id"] != "frontend_web"


def test_ai_model_agent_role_is_not_frontend():
    result = recommend(job("AI模型工程师-Agent技术",
        ["需要理解LLM Agent、Memory、多Agent协同及向量检索技术",
         "具备Agent框架与后端工程能力"],
        ["研发Agent短期上下文和长期记忆及动态检索",
         "设计多Agent任务拆分、状态同步、冲突消解和协作执行"]))
    assert result["recommended_skill_id"] == "ai_agent_application"


def test_java_backend_with_multi_language_requirement_is_recommended():
    result = recommend(job("JAVA后端开发工程师",
        ["Java/Python/JavaScript至少一种", "具有集群、分布式、高并发项目经验优先",
         "掌握数据结构与算法"]))
    assert result["recommended_skill_id"] == "java_backend"


def test_generic_words_alone_do_not_pick_frontend():
    result = recommend(job("服务端开发工程师",
        ["负责系统性能优化与状态管理"],
        ["参与平台性能优化和状态同步"]))
    assert result["recommended_skill_id"] != "frontend_web"
