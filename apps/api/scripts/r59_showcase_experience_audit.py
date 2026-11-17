"""SHOWCASE-R1 / part 3: zero-model audit of the 6 flows / 59 question leads.

Retains the dataset's own identity flags verbatim, checks source and question
duplication, classifies technical topics, and states exactly what interview
context would (and would not) accept. Produces the required four lists without
publishing anything.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import asyncpg

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))
from interview_api.modules.public_career.fingerprints import normalize_text  # noqa: E402

BASE = REPO / "data/incoming_20261116c"
OUT = REPO / "data/r59_showcase_experiences"
DSN = "postgresql://interview_agent:interview_agent@localhost:5432/interview_agent_wb_jd_r31_20261116"

TOPICS = {
    "LLM/大模型": ("大模型", "llm", "gpt", "qwen", "transformer", "微调", "预训练", "token", "幻觉",
                   "模型生成", "模型评测", "多模态"),
    "Agent/RAG": ("agent", "rag", "langgraph", "langchain", "function calling", "工具调用",
                   "mcp", "多轮", "workflow", "prompt", "记忆", "规划", "智能体", "react",
                   "skill", "tool", "编排", "意图", "澄清", "口语", "错别字"),
    "检索/向量": ("向量", "embedding", "召回", "检索", "重排", "rerank", "知识库", "索引",
                   "分块", "rrf", "cross-encoder", "粗排", "文档", "markdown", "pdf", "切片"),
    "后端/工程": ("后端", "spring", "java", "go", "mysql", "redis", "kafka", "微服务", "接口",
                   "并发", "事务", "缓存", "消息队列", "分布式", "数据库", "sql", "消费",
                   "生产激增", "遥测", "顺序", "服务端", "限流", "jvm", "gc", "stw", "分库分表",
                   "跨库", "集群", "ai coding", "生成代码"),
    "算法/ML": ("算法", "机器学习", "深度学习", "模型训练", "评测", "指标", "特征", "排序模型",
                "二叉树", "前缀树", "数据结构", "数组", "链表", "复杂度"),
    "网络/协议": ("https", "tcp", "sse", "网络链路", "应用层", "传输层", "断线", "协议", "浏览器", "osi", "网络分层"),
    "系统设计": ("架构", "系统设计", "高可用", "扩容", "限流", "容灾", "性能", "监控", "平台"),
    "质量/工程实践": ("测试", "质量", "覆盖面", "覆盖率", "ai 工具", "研发效能", "工具支持"),
    "项目/行为": ("项目", "介绍", "为什么", "难点", "收获", "团队", "协作", "离职", "规划",
                "动机", "实习", "本人贡献", "业务流程"),
}


def fold(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").casefold()
    return re.sub(r"[\s，,。；;：:？?！!（）()\[\]【】“”\"'’‘·…—-]", "", value)


def topics_of(text: str) -> list[str]:
    value = fold(text)
    return [name for name, words in TOPICS.items()
            if any(fold(word) in value for word in words)]


async def db_state() -> dict:
    conn = await asyncpg.connect(DSN)
    try:
        companies = [dict(r) for r in await conn.fetch(
            "select id, canonical_name, status from companies order by id")]
        jobs = [dict(r) for r in await conn.fetch(
            "select id, company_id, canonical_title, status from jobs order by id")]
        experiences = [dict(r) for r in await conn.fetch(
            "select id, company, position, job_direction, review_status, company_id, job_id, source_url "
            "from interview_experiences order by id")]
        questions = await conn.fetchval("select count(*) from interview_questions")
    finally:
        await conn.close()
    return {"companies": companies, "jobs": jobs, "experiences": experiences,
            "question_rows": questions}


async def main() -> int:
    flows = [json.loads(line) for line in
             (BASE / "02_公开面经流程_6场.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    leads = [json.loads(line) for line in
             (BASE / "03_面经有出处改写题目.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    state = await db_state()

    by_flow = defaultdict(list)
    for lead in leads:
        by_flow[lead["flow_id"]].append(lead)

    url_to_flows = defaultdict(list)
    for flow in flows:
        url_to_flows[flow["report_url"]].append(flow["flow_id"])
    shared_urls = {url: ids for url, ids in url_to_flows.items() if len(ids) > 1}

    fingerprint = defaultdict(list)
    for lead in leads:
        fingerprint[fold(lead["question_paraphrase"])].append(lead["id"])
    duplicate_questions = {key: ids for key, ids in fingerprint.items() if len(ids) > 1}

    all_companies = sorted({f["company"] for f in flows})
    sandbox_companies = {c["canonical_name"] for c in state["companies"]}
    cross_company = sorted(set(all_companies) - sandbox_companies)

    classified: list[dict] = []
    for lead in leads:
        text = lead["question_paraphrase"]
        topics = topics_of(text)
        dup_key = fold(text)
        is_dup = len(fingerprint[dup_key]) > 1
        flow = next(f for f in flows if f["flow_id"] == lead["flow_id"])
        shared = flow["report_url"] in shared_urls
        url_ok = bool(lead.get("source_url")) and "nowcoder.com/discuss/" in lead["source_url"]
        url_matches_flow = lead.get("source_url") == flow["report_url"]
        # Identity is never upgraded: no verbatim source, no answer, no gold.
        reasons = []
        if is_dup:
            reasons.append("DUPLICATE_QUESTION")
        if not url_ok or not url_matches_flow:
            reasons.append("SOURCE_URL_MISMATCH")
        if shared:
            reasons.append("ONE_POST_USED_FOR_TWO_FLOWS")
        if not topics:
            reasons.append("NO_TECHNICAL_TOPIC")

        if reasons and ("DUPLICATE_QUESTION" in reasons or "SOURCE_URL_MISMATCH" in reasons):
            verdict = "REJECT"
        elif shared:
            verdict = "NEED_ORIGINAL"
        else:
            # Every remaining lead is still a paraphrase from an unverified post
            # with no human answer or gold; it cannot enter the approved pool.
            verdict = "REFERENCE_ONLY"
        classified.append({
            "id": lead["id"], "flow_id": lead["flow_id"], "company": flow["company"],
            "role": flow["role"], "question": text, "topics": topics,
            "source_url": lead["source_url"], "shared_across_flows": shared,
            "duplicate_group": fingerprint[dup_key] if is_dup else [],
            "flags": {"is_model_generated_interview_record": lead["is_model_generated_interview_record"],
                      "is_actual_human_answer": lead["is_actual_human_answer"],
                      "is_human_gold": lead["is_human_gold"]},
            "verdict": verdict, "reasons": reasons,
        })

    buckets = {name: [c["id"] for c in classified if c["verdict"] == name]
               for name in ("IMPORTABLE", "REFERENCE_ONLY", "NEED_ORIGINAL", "REJECT")}
    topic_counts = Counter(t for c in classified for t in c["topics"])
    company_counts = Counter(c["company"] for c in classified)
    flow_counts = {f["flow_id"]: len(by_flow[f["flow_id"]]) for f in flows}

    report = {
        "model_http_calls": 0,
        "dataset_identity": {
            "provenance": sorted({f["provenance"] for f in flows}),
            "independently_verified_event": sorted({f["independently_verified_event"] for f in flows}),
            "question_wording": sorted({f["question_wording"] for f in flows}),
            "complete_original_transcript": sorted({f["complete_original_transcript"] for f in flows}),
            "is_actual_human_answer": sorted({l["is_actual_human_answer"] for l in leads}),
            "is_human_gold": sorted({l["is_human_gold"] for l in leads}),
            "is_model_generated_interview_record": sorted(
                {l["is_model_generated_interview_record"] for l in leads}),
        },
        "counts": {"flows": len(flows), "question_leads": len(leads),
                   "selected_question_count_sum": sum(f["selected_question_count"] for f in flows),
                   "per_flow": flow_counts, "unique_source_urls": len(url_to_flows),
                   "shared_urls": shared_urls, "duplicate_question_groups": len(duplicate_questions),
                   "topics": dict(topic_counts), "companies": dict(company_counts)},
        "context_legality": {
            "scope_rule": "build_context(): (job_id == selected.job_id OR (job_id IS NULL AND company_id == selected.company_id)) AND (company_id IS NULL OR company_id == selected.company_id)",
            "adaptive_rule": "adaptive V2 additionally requires q.job_id == selected.job_id and source_anchor.kind != REPORTED_TOPIC",
            "review_rule": "ExperienceReviewService.approve requires ADMIN; questions need review_status=APPROVED and should_index=TRUE",
            "flow_companies": all_companies,
            "sandbox_companies": sorted(sandbox_companies),
            "companies_without_any_sandbox_job": cross_company,
            "can_bind_to_published_showcase_jobs": False,
            "reason": "字节/阿里/美团 在 JD 沙箱没有 company/job 行；强行关联麒麟、沐瞳、蔚来岗位会违反精确外键 scope 与自适应 V2 的 job_id 约束",
            "sandbox_experience_rows": len(state["experiences"]),
            "sandbox_question_rows": state["question_rows"],
        },
        "buckets": buckets,
        "leads": classified,
        "flows": flows,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "experience_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print("flows", len(flows), "leads", len(leads), "unique_urls", len(url_to_flows))
    print("identity", json.dumps(report["dataset_identity"], ensure_ascii=False))
    print("duplicate groups", len(duplicate_questions), list(duplicate_questions.values())[:4])
    print("shared urls", json.dumps(shared_urls, ensure_ascii=False))
    print("topics", dict(topic_counts))
    print("buckets", {k: len(v) for k, v in buckets.items()})
    print("can_bind_to_showcase_jobs", report["context_legality"]["can_bind_to_published_showcase_jobs"])
    print("written", OUT / "experience_audit.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
