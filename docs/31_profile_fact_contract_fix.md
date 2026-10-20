# Profile Stable Fact / Derived Analysis contract 修复

日期：2026-10-05。限定 Profile contract 和该 route 的模型参数，不扩展 Research、Matching、
Interview、Coach、Memory。无 migration；历史 migrations 和已发布版本不修改。

## 边界与兼容

- 保留 CandidateProfileDelta 作为历史 snapshot / correction 的兼容结构。
- 新增 CandidateFactsExtraction：candidate_summary 只能为 NULL，strengths 只能为空列表。
  第一阶段只抽取 skills、projects、education、work_experience、internship_experience。
- Prompt 升为 resume_profile@1.0.3；1.0.0 / 1.0.1 / 1.0.2 仍可查询。
- 五类稳定事实仍要求 label 和 statement 分别为所引用 atomic Evidence 的规范化连续原文。
  不接受改写、跨 Evidence 拼成新句子或仅靠有效 ID 证明新事实。
- alias 精确恢复真实 key，skill state 只能向下收敛；原 Evidence excerpt/key/hash/offset 不改。
- 新 CandidateFact 写入只遍历上述五类。summary/strengths 不生成 CandidateFact，unknowns 也不是事实。
- 未引入第二阶段分析 Agent；新抽取版本的 summary=NULL、strengths=[]。
- 旧版本和旧 Fact 行不 backfill、不 UPDATE。旧内容仍可历史读取；稳定事实 correction
  可以完整继承旧版未变的 display 内容，但不会在 N+1 中重建 summary/strengths 的 Fact。
  新 summary/strengths 不能通过稳定事实持久化或 correction 入口写入。
- correction 只允许五类稳定事实的 label/statement；用户确认支持的稳定事实继续跨 regeneration 继承。
- 保留历史 iter_facts 默认路径查找兼容，避免冻结 Matching 消费旧 Fact 路径时报错；
  新写入显式使用 stable_only=True。Matching/M4 新版本消费测试验证只读到五类 CandidateFacts。
- 原 generation key 和 current pointer 事务流程不变。新 Prompt version/hash 产生独立 generation key。

## 诊断与隐私

UNSUPPORTED_CANDIDATE_FACT 增加 field_path、category、value_field、cited_aliases、
cited_literal_match、uncited_literal_match、possible_wrong_evidence_reference 和规则名。
失败值只保存长度与 fingerprint，不保存整份响应或敏感原文。

其他 Evidence 中字面匹配只作为 possible_wrong_evidence_reference，不替模型修引用。
任何原文均无字面匹配仍不能区分真正 unsupported 与合理 paraphrase。
旧 validator 默认仍保留 summary/strengths 的历史严格摘录检查；新抽取/写入仅校验稳定事实，
这不是降低稳定事实的支持标准。

## Route-level reasoning

- RESUME_PROFILE_THINKING 默认 disabled；只有 resume_profile route 显式传 thinking.type。
- Provider 不指定该选项时不发送 thinking 或 reasoning_effort，其他 Agent route 保持原行为。
- 使用 thinking.type=disabled，不同时发送 reasoning_effort；已有可用性 fallback 若被配置，继承同 route 参数。
- 本次真实验收显式禁用 fallback，限制最多一次模型 HTTP。全局 availability fallback 规则未修改。
- 正式 timeout 仍 60 秒，max_tokens 仍 6000，.env 与 API key 未修改。
- 原因：默认 high reasoning 的诊断 120 秒内无正文；非思考诊断 12.178 秒完成，
  但当时 Evidence 校验失败，不能把速度改善宣称为业务验收成功。

## 自动化

首轮新隔离库 interview_agent_f4_regression_profile_contract_20261005_1：
243 passed / 1 failed，失败为既有 M5 API 测试所需 Redis 未启动，不修改 M5 测试或代码绕过。
启动原有 Redis 后，新隔离库 interview_agent_f4_regression_profile_contract_20261005_2：
**246 passed / 99.64 秒，无 skip/fail**。保留原 228 项，新增 18 项。

新增覆盖：原文支持/unsupported project/paraphrase/跨 Evidence 拼句拒绝、summary/strengths
抽取 Schema 拒绝、持久化入口拒绝新 analysis 且 current 不变、wrong reference 字段诊断、
无敏感失败值及不修引用、route 隔离、实际 HTTP thinking 参数、旧 Prompt 查询、
稳定 Fact/normalized persistence、generation 幂等、用户确认继承、Matching/M4 消费边界和失败审计。
原有 unknown alias、state downward normalization、原子切换、历史不可变、M1–M6 测试保留。

命令：

```powershell
& apps/api/.venv/Scripts/python.exe scripts/run-regression.py --database interview_agent_f4_regression_profile_contract_20261005_2
# apps/web 工作目录
node --test tests/product-ux.test.mjs tests/job-matching.test.mjs tests/interview-sse.test.mjs tests/interview-report.test.mjs
pnpm build
# 仓库根目录
& apps/api/.venv/Scripts/python.exe -m compileall -q apps/api/src apps/api/tests apps/api/scripts
git diff --check
```

Frontend 11/11；production build/vue-tsc、Python compile、diff check 通过。
检查的 Matching/Interview/Report/Memory 源码及全部 migration hash 与本轮前一致。
回归使用新库，不清理旧 attempt 或正式业务数据。测试库名称前缀由已有安全脚本要求，
不表示重新进入 F4 阶段。

## 唯一一次 Resume 38 正式真实模型验收

不是 Replay，也不是 Live Search；使用已上传真实 Resume 38 的现有 Report / 40 条 Evidence。
generation → Structured Output → alias resolver → state normalization → literal validator。

| 指标 | 结果 |
|---|---|
| Provider / model | openai_compatible / deepseek-v4-pro-ga-260813 |
| thinking / Prompt | disabled / resume_profile@1.0.3 |
| 请求字符数 / max_tokens / timeout | 10134 / 6000 / 60 秒 |
| HTTP / finish_reason | 200 / stop |
| 模型 Step latency / 整体 wall | 19248 ms / 19437 ms |
| input / output / total tokens | 3045 / 2061 / 5106 |
| reasoning tokens / repair / fallback | 0 / 0 / 无 |
| Structured Output / alias / normalization | 通过 / 通过 / 通过 |
| Skill normalization | 16 项，0 项需要下调 |
| 最终 Evidence validator | FAILED：UNSUPPORTED_CANDIDATE_FACT |

精确失败位置：skills.12.statement（第 13 项技能的 statement）。引用 E29/E30，长度 99。
cited_literal_match=false；uncited_literal_match=false；possible_wrong_evidence_reference=false。
本次不保存或打印失败 statement；不能据字面失败断言候选人没有该能力，
也不能只凭此判断是编造、改写还是拼接。没有自动替换、裁剪或修引用。

正式 generation 5=FAILED，AgentRun 32=FAILED，StepRun 139=SUCCEEDED。
Step 成功只说明 Structured Output 成功，不代表最终业务校验通过。
新 generation key 的 Generation 数=1、ProfileVersion 数=0，current 仍为 NULL。
原 generation/Report/Evidence 前后完全一致；没有半成品 current 或重复成功版本。
未执行第二次真实调用、fallback、提高 timeout 或修改 validator 后再试。

## 文件与停止

本轮增量：candidate_profile 的 schemas/prompts/evidence/service/agent/context；
共享 CompatibleModelProvider 的可选参数、resume_profile router/config、.env.example；
test_profile_fact_contract.py、既有 Profile fixtures / Prompt 历史断言、M3 Replay demo fixture、本文档。
不新增依赖，不修改 Domain DB Schema 或 migration，不修改 Matching/Interview/Coach/Memory 业务代码。
进入本轮前已有 context 修复和 docs/28、docs/29、docs/30 等未提交工作，均保留。

为回归启动了现有 Redis；现有 PostgreSQL 保持运行，没有启动 API/Worker 或 SearXNG。
正常服务后续需受控重启加载代码，本轮没有触发 Worker 自动 retry。
未 commit。真实画像尚未通过，按要求停止，不开始 JD importer。
