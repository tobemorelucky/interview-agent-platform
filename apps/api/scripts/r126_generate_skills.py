# -*- coding: utf-8 -*-
"""R126: generate the seven new interviewer Skills.

Each Skill is three immutable files under interviewer_skills/<id>/1.0.0/:
manifest.json (matching rules), skill.json (考察范围/锚点), SKILL.md (instructions).
The registry quarantines anything malformed, so this script validates its own
output with the real SkillManifest / SkillDefinition models before finishing.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "interview_api" / "modules" / "interview" / "interviewer_skills"
sys.path.insert(0, str(ROOT.parents[1] / "src"))

SKILLS = [
    dict(
        id="python_backend", label="Python 后端开发", category="python_backend",
        roles=["Python后端工程师", "Python开发工程师", "服务端开发"],
        tags=["Python", "Django", "FastAPI", "Flask", "异步"],
        scope="Python 服务端工程：Web 框架、异步并发、数据访问与服务可靠性，不等同于数据科学或脚本自动化",
        categories=["python_backend", "Python后端", "Python开发"],
        aliases=["Python工程师", "Python后端", "Python服务端", "后端开发（Python）"],
        responsibilities=["Python开发", "后端服务", "接口开发", "Web服务", "Django", "FastAPI", "Flask", "异步编程"],
        capability_groups=[["Python", "FastAPI", "Django", "Flask"], ["异步", "asyncio", "协程"], ["MySQL", "PostgreSQL", "Redis"], ["Celery", "任务队列", "消息队列"]],
        exclusions=["数据标注", "数据分析（纯业务）", "爬虫外包"],
        topics=["python", "mysql_consistency", "redis", "kafka_reliability", "system_design", "data_security"],
        domains=["Python 异步与并发模型", "Web 框架与接口设计", "数据访问与事务边界", "服务可靠性：超时、重试与降级"],
        key_caps=["解释 GIL/协程/线程的真实边界", "说明接口契约与错误处理", "讲清数据库事务与缓存一致性", "处理超时、重试与幂等"],
        project_strategy=["只追问简历证据或本轮回答建立的项目", "沿一个接口或任务链路选一个实现细节", "不把框架名当作掌握原理的证明"],
        anchors=dict(technical_correctness="机制与术语是否准确，是否区分语言、框架与运行时责任", coverage="是否覆盖当前问题要求的链路与关键步骤", depth="是否说明设计理由、失败边界与验证依据", relevance="是否针对实际问题和已建立的项目背景", communication="是否清晰表达流程、因果与取舍"),
        body="考察可解释的 Python 服务端工程，而不是框架背诵。优先结合真实项目选择接口设计、并发模型、数据访问或可靠性中的一个问题。\n入门检查输入输出与基本流程；中阶追问实现机制与选型理由；深入时询问失败边界、并发正确性与验证方法。只在回答或候选证据建立相关事实后追问具体技术，不假设用户用过某框架或生产架构。\nFOLLOW_UP 补当前问题中未解释的一点；DEEP_DIVE 深入已经说出的一个技术决策。缺失点补齐后切换主题。主题次数、预算与结束由 Controller 决定。\n评价遵循统一五维 1–5 量表，专业锚点解释当前问题如何评分，不引入长期能力分或风格加减分。",
    ),
    dict(
        id="rag_retrieval", label="RAG 与检索工程", category="rag_retrieval",
        roles=["RAG工程师", "检索工程师", "知识库工程师", "大模型应用（检索方向）"],
        tags=["RAG", "向量检索", "Embedding", "重排", "知识库"],
        scope="检索增强与检索系统工程：切分、召回、融合、重排与证据约束，不等同于模型训练或纯算法研究",
        categories=["rag_retrieval", "检索工程", "知识工程"],
        aliases=["RAG", "检索工程师", "向量检索", "知识库", "Embedding"],
        responsibilities=["RAG", "检索增强", "向量检索", "知识库", "Embedding", "重排", "召回", "语义检索"],
        capability_groups=[["RAG", "检索增强", "语义检索"], ["Milvus", "Elasticsearch", "BM25", "向量数据库"], ["Embedding", "BGE", "重排", "RRF"], ["知识库", "文档切分", "索引"]],
        exclusions=["纯推荐系统", "传统数据库运维"],
        topics=["rag_retrieval", "agent", "llm", "python", "system_design", "data_security"],
        domains=["文档切分与索引设计", "多路召回与融合排序", "证据约束与答案可信度", "检索质量评估与失败排查"],
        key_caps=["解释召回-融合-重排链路与 k 的作用", "区分模型生成与检索证据", "说明坏检索如何变成坏答案", "给出检索质量的评估方式"],
        project_strategy=["只追问简历证据或本轮回答建立的项目", "沿索引、召回、融合、验证链路选一个实现细节", "不把框架名当作掌握原理的证明"],
        anchors=dict(technical_correctness="机制和术语是否准确，是否区分检索证据与模型生成", coverage="是否覆盖当前问题要求的链路与关键步骤", depth="是否说明设计理由、失败边界和验证依据", relevance="是否针对实际问题和已建立的项目背景", communication="是否清晰表达流程、因果和取舍"),
        body="考察可解释的检索增强工程，而不是框架背诵。优先结合真实项目选择切分索引、多路召回、融合重排或证据约束中的一个问题。\n入门检查输入输出与链路理解；中阶追问实际机制及参数取舍；深入时询问失败边界、坏检索的后果与评估方法。只在回答或候选证据建立相关事实后追问具体技术。\nFOLLOW_UP 补当前问题中未解释的一点；DEEP_DIVE 深入已经说出的一个技术决策。缺失点补齐后切换主题。主题次数、预算与结束由 Controller 决定。\n评价遵循统一五维 1–5 量表，专业锚点解释当前问题如何评分，不引入长期能力分或风格加减分。",
    ),
    dict(
        id="recommendation_ml", label="推荐系统与机器学习", category="recommendation_ml",
        roles=["推荐算法工程师", "推荐系统工程师", "机器学习工程师", "算法工程师（应用方向）"],
        tags=["推荐系统", "召回排序", "特征工程", "CTR", "机器学习"],
        scope="工业界推荐与机器学习应用：召回排序、特征、离线在线一致性与评估，不等同于理论机器学习研究",
        categories=["recommendation_ml", "推荐系统", "机器学习应用"],
        aliases=["推荐算法", "推荐系统", "机器学习工程师", "算法工程师", "排序模型"],
        responsibilities=["推荐系统", "推荐算法", "召回", "排序", "特征工程", "CTR预估", "模型训练", "A/B测试", "机器学习"],
        capability_groups=[["推荐系统", "协同过滤", "召回排序"], ["特征工程", "样本构建", "离线在线一致性"], ["CTR", "排序模型", "DeepFM", "双塔"], ["A/B测试", "离线评估", "在线指标"]],
        exclusions=["纯CV研究", "纯NLP理论研究"],
        topics=["recommendation", "algorithm", "python", "mysql_consistency", "system_design", "llm"],
        domains=["召回与排序链路", "特征与样本工程", "离线评估与在线实验", "工程一致性：训练-服务偏差"],
        key_caps=["解释召回多路融合与排序目标", "说明特征泄漏与离线在线不一致", "讲清实验设计与指标取舍", "区分模型能力与工程实现"],
        project_strategy=["只追问简历证据或本轮回答建立的项目", "沿样本、特征、模型、评估链路选一个实现细节", "不把调包经历当作掌握原理的证明"],
        anchors=dict(technical_correctness="机制和术语是否准确，是否区分模型假设与工程实现", coverage="是否覆盖当前问题要求的链路与关键步骤", depth="是否说明设计理由、失败边界和验证依据", relevance="是否针对实际问题和已建立的项目背景", communication="是否清晰表达流程、因果和取舍"),
        body="考察可解释的推荐与机器学习应用工程，而不是论文背诵。优先结合真实项目选择样本特征、召回排序、评估实验或线上服务中的一个问题。\n入门检查输入输出与流程理解；中阶追问机制与参数取舍；深入时询问失败边界、指标陷阱与验证方法。只在回答或候选证据建立相关事实后追问具体技术。\nFOLLOW_UP 补当前问题中未解释的一点；DEEP_DIVE 深入已经说出的一个技术决策。缺失点补齐后切换主题。主题次数、预算与结束由 Controller 决定。\n评价遵循统一五维 1–5 量表，专业锚点解释当前问题如何评分，不引入长期能力分或风格加减分。",
    ),
    dict(
        id="frontend_web", label="前端开发", category="frontend",
        roles=["前端工程师", "Web前端开发", "H5开发工程师"],
        tags=["Vue", "React", "TypeScript", "性能优化"],
        scope="Web 前端工程：框架、状态与数据流、渲染性能与工程化，不等同于 UI 视觉设计",
        categories=["frontend", "前端", "Web前端"],
        aliases=["前端", "前端工程师", "Web开发", "H5", "Vue", "React"],
        responsibilities=["前端开发", "Web开发", "页面开发", "组件开发", "Vue", "React", "小程序前端", "性能优化"],
        capability_groups=[["Vue", "React", "TypeScript", "JavaScript"], ["状态管理", "Pinia", "Vuex", "Redux"], ["性能优化", "首屏", "懒加载", "打包构建"], ["浏览器渲染", "跨端", "小程序"]],
        exclusions=["纯UI设计师", "平面设计"],
        topics=["frontend", "network_os", "algorithm", "system_design", "python"],
        domains=["框架与组件模型", "状态管理与数据流", "渲染性能与包体优化", "浏览器与网络基础"],
        key_caps=["解释响应式/虚拟DOM的真实机制", "说明状态设计与数据流边界", "定位首屏与交互性能问题", "区分语言、框架与浏览器行为"],
        project_strategy=["只追问简历证据或本轮回答建立的项目", "沿组件、状态、渲染、构建链路选一个实现细节", "不把框架名当作掌握原理的证明"],
        anchors=dict(technical_correctness="机制和术语是否准确，是否区分语言、框架与浏览器责任", coverage="是否覆盖当前问题要求的链路与关键步骤", depth="是否说明设计理由、失败边界和验证依据", relevance="是否针对实际问题和已建立的项目背景", communication="是否清晰表达流程、因果和取舍"),
        body="考察可解释的 Web 前端工程，而不是框架背诵。优先结合真实项目选择组件模型、状态与数据流、渲染性能或工程化中的一个问题。\n入门检查输入输出与基本流程；中阶追问实现机制与选型理由；深入时询问失败边界、性能归因与验证方法。只在回答或候选证据建立相关事实后追问具体技术。\nFOLLOW_UP 补当前问题中未解释的一点；DEEP_DIVE 深入已经说出的一个技术决策。缺失点补齐后切换主题。主题次数、预算与结束由 Controller 决定。\n评价遵循统一五维 1–5 量表，专业锚点解释当前问题如何评分，不引入长期能力分或风格加减分。",
    ),
    dict(
        id="testing_qa", label="测试开发", category="testing_qa",
        roles=["测试开发工程师", "自动化测试工程师", "QA工程师", "测试工程师"],
        tags=["自动化测试", "用例设计", "pytest", "质量保障"],
        scope="测试与质量工程：用例设计、自动化、稳定性与效能工具，不等同于 AI 应用开发",
        categories=["testing_qa", "测试开发", "质量保障"],
        aliases=["测试开发", "测试工程师", "QA", "自动化测试", "质量保障"],
        responsibilities=["测试", "测试开发", "自动化测试", "用例设计", "质量保障", "测试平台", "性能测试", "接口测试"],
        capability_groups=[["测试用例", "用例设计", "边界值"], ["pytest", "unittest", "Selenium", "自动化框架"], ["接口测试", "JMeter", "压测", "性能测试"], ["CI", "流水线", "测试平台", "质量度量"]],
        exclusions=["AI应用开发", "大模型应用开发"],
        topics=["testing", "python", "mysql_consistency", "network_os", "system_design"],
        domains=["用例设计与覆盖", "自动化框架与稳定性", "接口与性能测试", "质量度量与效能工具"],
        key_caps=["解释用例设计与覆盖的真实边界", "定位不稳定用例的原因", "讲清分层测试与投入取舍", "用数据说明质量状态"],
        project_strategy=["只追问简历证据或本轮回答建立的项目", "沿用例、框架、执行、度量链路选一个实现细节", "不把工具名当作掌握方法的证明"],
        anchors=dict(technical_correctness="机制和方法是否准确，是否区分测试方法与工具使用", coverage="是否覆盖当前问题要求的链路与关键步骤", depth="是否说明设计理由、失败边界和验证依据", relevance="是否针对实际问题和已建立的项目背景", communication="是否清晰表达流程、因果和取舍"),
        body="考察可解释的测试与质量工程，而不是工具罗列。优先结合真实项目选择用例设计、自动化框架、接口/性能测试或效能工具中的一个问题。\n入门检查输入输出与基本流程；中阶追问方法与实现细节；深入时询问失败边界、稳定性归因与度量方法。只在回答或候选证据建立相关事实后追问具体技术。\nFOLLOW_UP 补当前问题中未解释的一点；DEEP_DIVE 深入已经说出的一个技术决策。缺失点补齐后切换主题。主题次数、预算与结束由 Controller 决定。\n评价遵循统一五维 1–5 量表，专业锚点解释当前问题如何评分，不引入长期能力分或风格加减分。",
    ),
    dict(
        id="data_engineering", label="数据工程", category="data_engineering",
        roles=["数据工程师", "大数据开发工程师", "数仓工程师"],
        tags=["数仓", "Spark", "Flink", "ETL"],
        scope="数据工程：建模、集成、批流计算与数据质量，不等同于算法研究或纯 BI 报表",
        categories=["data_engineering", "数据工程", "大数据开发"],
        aliases=["数据开发", "大数据", "数仓", "ETL", "数据平台"],
        responsibilities=["数据仓库", "数据建模", "ETL", "数据集成", "Spark", "Flink", "Hive", "实时计算", "数据治理"],
        capability_groups=[["数仓", "数据仓库", "维度建模"], ["Spark", "Flink", "Hive", "批流计算"], ["ETL", "数据集成", "调度", "Airflow", "DolphinScheduler"], ["数据质量", "数据治理", "元数据"]],
        exclusions=["纯BI报表", "数据标注"],
        topics=["data_engineering", "python", "kafka_reliability", "mysql_consistency", "system_design", "data_security"],
        domains=["维度建模与分层设计", "批流一体与计算引擎", "调度、幂等与回溯", "数据质量与治理"],
        key_caps=["解释分层建模与粒度选择", "说明批流一致性 Exactly-once 边界", "处理延迟、乱序与幂等重跑", "用指标说明数据质量"],
        project_strategy=["只追问简历证据或本轮回答建立的项目", "沿建模、集成、计算、质量链路选一个实现细节", "不把引擎名当作掌握原理的证明"],
        anchors=dict(technical_correctness="机制和术语是否准确，是否区分引擎能力与业务建模", coverage="是否覆盖当前问题要求的链路与关键步骤", depth="是否说明设计理由、失败边界和验证依据", relevance="是否针对实际问题和已建立的项目背景", communication="是否清晰表达流程、因果和取舍"),
        body="考察可解释的数据工程，而不是引擎罗列。优先结合真实项目选择建模分层、批流计算、调度幂等或数据质量中的一个问题。\n入门检查输入输出与基本流程；中阶追问机制与参数取舍；深入时询问失败边界、一致性保证与验证方法。只在回答或候选证据建立相关事实后追问具体技术。\nFOLLOW_UP 补当前问题中未解释的一点；DEEP_DIVE 深入已经说出的一个技术决策。缺失点补齐后切换主题。主题次数、预算与结束由 Controller 决定。\n评价遵循统一五维 1–5 量表，专业锚点解释当前问题如何评分，不引入长期能力分或风格加减分。",
    ),
    dict(
        id="general_cs", label="通用计算机基础", category="general_cs",
        roles=["软件开发工程师", "后端开发（通用）", "校招技术岗"],
        tags=["操作系统", "计算机网络", "数据结构", "数据库基础"],
        scope="通用计算机基础：操作系统、网络、数据结构算法与数据库原理，适用于任何研发岗的底座考察",
        categories=["general_cs", "计算机基础", "软件开发"],
        aliases=["软件开发", "通用后端", "计算机基础", "校招"],
        responsibilities=["软件开发", "基础平台", "通用研发", "系统开发", "底层开发"],
        capability_groups=[["操作系统", "进程", "线程", "内存管理"], ["TCP", "HTTP", "网络协议", "三次握手"], ["数据结构", "算法", "复杂度"], ["MySQL", "Redis", "数据库原理"]],
        exclusions=[],
        topics=["network_os", "algorithm", "mysql_consistency", "redis", "system_design", "data_security"],
        domains=["操作系统与资源管理", "网络协议与连接生命周期", "数据结构与算法取舍", "数据库与缓存原理"],
        key_caps=["解释进程线程与内存模型", "讲清一次网络请求的完整生命周期", "分析算法复杂度与边界条件", "说明索引、事务与缓存的机制"],
        project_strategy=["通用基础不依赖项目证据", "场景题优先考察原理到应用的转化", "不把背诵八股当作理解机制"],
        anchors=dict(technical_correctness="机制和术语是否准确，是否区分原理与实现细节", coverage="是否覆盖当前问题要求的链路与关键步骤", depth="是否说明设计理由、失败边界和验证依据", relevance="是否针对当前问题与岗位基础要求", communication="是否清晰表达流程、因果和取舍"),
        body="考察可解释的计算机基础，而不是八股背诵。优先选择操作系统、网络、数据结构算法或数据库原理中的一个真实机制问题，并尽量落到一个场景。\n入门检查概念边界与基本流程；中阶追问机制与取舍；深入时询问失败边界与验证方法。可以主动离开简历考察基础，但仍在本场冻结目标内。\nFOLLOW_UP 补当前问题中未解释的一点；DEEP_DIVE 深入已经说出的一个机制。缺失点补齐后切换主题。主题次数、预算与结束由 Controller 决定。\n评价遵循统一五维 1–5 量表，专业锚点解释当前问题如何评分，不引入长期能力分或风格加减分。",
    ),
]

MANIFEST = """{{
  "id": "{sid}", "label": "{label}", "version": "1.0.0",
  "professional_category": "{category}", "applicable_roles": {roles},
  "capability_tags": {tags}, "scope": "{scope}",
  "matching": {{
    "categories": {categories},
    "title_aliases": {aliases},
    "responsibility_terms": {responsibilities},
    "capability_groups": {groups},
    "exclusions": {exclusions}
  }}
}}
"""

SKILL = """{{
  "id": "{sid}",
  "version": "1.0.0",
  "label": "{label}",
  "priority_topics": {topics},
  "domains": {domains},
  "key_capabilities": {caps},
  "project_strategy": {strategy},
  "level_probes": {{"introductory": "说明输入输出和基本流程", "intermediate": "解释一个实际机制及选择理由", "advanced": "讨论失败边界、替代方案及验证方法"}},
  "evaluation_anchors": {anchors}
}}
"""


def dump(value):
    return json.dumps(value, ensure_ascii=False, indent=1)


def main():
    for skill in SKILLS:
        directory = ROOT / skill["id"] / "1.0.0"
        directory.mkdir(parents=True, exist_ok=True)
        manifest = MANIFEST.format(sid=skill["id"], label=skill["label"], category=skill["category"],
            roles=dump(skill["roles"]), tags=dump(skill["tags"]), scope=skill["scope"],
            categories=dump(skill["categories"]), aliases=dump(skill["aliases"]),
            responsibilities=dump(skill["responsibilities"]), groups=dump(skill["capability_groups"]),
            exclusions=dump(skill["exclusions"]))
        definition = SKILL.format(sid=skill["id"], label=skill["label"], topics=dump(skill["topics"]),
            domains=dump(skill["domains"]), caps=dump(skill["key_caps"]),
            strategy=dump(skill["project_strategy"]), anchors=dump(skill["anchors"]))
        body = f"# {skill['label']}面试 Skill 1.0.0\n\n{skill['body']}\n"
        (directory / "manifest.json").write_text(manifest, encoding="utf-8")
        (directory / "skill.json").write_text(definition, encoding="utf-8")
        (directory / "SKILL.md").write_text(body, encoding="utf-8")
        print("written", directory)


if __name__ == "__main__":
    main()
