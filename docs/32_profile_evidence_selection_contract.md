# Profile：Evidence selection → deterministic stable facts

日期：2026-10-05。仅修复 Profile 模型边界，不修改 Matching / Interview / Coach / Memory
业务逻辑、不改 DB Schema 或 migrations。旧失败事实保留在 docs/31，不回写历史版本。

## 下游只读审计与兼容方式

- Matching retrieval 将 CandidateFact.statement、label、Evidence refs 固定进 Run；policy 使用
  statement 的 literal topic match 或 label 相等。因此不能直接删除持久化 statement。
- M4 build_context 固定 CandidateFact.statement、field_path、Evidence refs，以及 normalized
  records 和真实 Evidence。继续保留原 snapshot 结构，不改面试创建规则。
- Profile UI 展示 label/statement 和可展开依据；correction 支持 label/statement 路径。
- Correction 是用户确认来源，不是模型生成来源。旧 statement 和确认文本仍可读，
  correction N+1 及 regeneration 的用户确认继承逻辑不变。
- 历史 CandidateProfileDelta、CandidateFactsExtraction 和 Prompt 1.0.0–1.0.3 保留，
  无 backfill、无历史版本 UPDATE。

## 新模型边界

resume_profile@1.0.4 / CandidateEvidenceSelection：模型按 skills / projects / education /
work_experience / internship_experience 分类，只选择 label 与调用级 Evidence aliases。
技能可提出 proposed_evidence_state。unknowns 仍是未确认信息主题。

模型 Schema 不含 statement / candidate_summary / strengths；extra=forbid，模型不能偷偷
写入自由 statement。返回的 label 必须在至少一条引用 Evidence 中规范化连续字面匹配。
未知 alias / unsupported label 拒绝，不自动从其他 Evidence 修复错误引用。

服务端 resolve_selection 精确恢复真实 key，去重并按 source/offset/key 排序引用：

- 所有合法引用保留为 Fact → ResumeEvidence 关系。
- 非技能事实：选最长的引用 excerpt；同长度按 resume/offset/key 确定性选取。
- 技能事实：在包含该技能 label 的引用 excerpt 中应用相同规则。
- compatibility statement 就是所选真实 excerpt，不裁剪、不改写、不拼接多个 excerpt。
- 再执行原有 state downward normalization 和 literal Evidence validator。
- 兼容 snapshot 仍为 CandidateProfileDelta，summary=NULL、strengths=[]，不写 derived Fact。

Representative statement 不是所有引用的文本汇总。冻结 Matching 中仅扫描 statement 的
topic 规则可能看不到其他引用中的次要技术词；保留全部 provenance，不通过拼句规避这个限制，
也不在本轮扩 Matching 规则。技能独立事实/label 仍可按既有规则参与匹配。

Ownership、Evidence offsets/key/hash、generation key、immutable Version、normalized records、
最后原子 current pointer 切换保持不变。只有新 Prompt/version/hash 形成新 attempt。

## 模型策略

仅 resume_profile thinking.type=disabled；当前正式 timeout=60 秒、max_tokens=6000。
其他 route 不改。真实验收只使用 deepseek-v4-pro-ga-260813，禁用 fallback，
本次最多一次实际 HTTP，不反复调用刷成功，不记录原始模型响应、私有简历或隐藏推理。

## 验收记录

### 自动化

完整隔离库 interview_agent_f4_regression_evidence_selection_20261005_1：
**267 passed / 99.97 秒，无 skip/fail**。原 246 项保留，新增 21 项。
新增覆盖 model Schema 无 statement/derived 字段、额外字段拒绝、五类 deterministic
representative、全部 provenance 保留、unsupported skill/project label、unknown alias、
state 仅向下收敛、unknowns 非负面事实、历史 Schema 读取、真实 DB normalized persistence、
statement correction 的完整 N+1、historical read、用户确认跨 regeneration 继承。
既有 Matching/M4 compatibility、generation 幂等、current 原子切换等测试全部保留。

Frontend 11/11；vue-tsc / production build、Python compile、git diff --check 均通过。
73 个检查文件（全部 migrations、Matching/Interview/Report/Memory Python 源码）hash
与本轮修改前完全一致。未修改 .env 或新增依赖。

命令：

```powershell
& apps/api/.venv/Scripts/python.exe scripts/run-regression.py --database interview_agent_f4_regression_evidence_selection_20261005_1
# apps/web
node --test tests/product-ux.test.mjs tests/job-matching.test.mjs tests/interview-sse.test.mjs tests/interview-report.test.mjs
pnpm build
# 仓库根目录
& apps/api/.venv/Scripts/python.exe -m compileall -q apps/api/src apps/api/tests apps/api/scripts
git diff --check
```

### 唯一一次正式 Resume 38 真实验收

正式库 revision=0024；真实 Resume 38 已上传并解析，本轮不重传、不修改 ResumeReport。
新 Prompt key 调用前无 generation/version，current=NULL。

| 指标 | 实际结果 |
|---|---|
| Provider / model | openai_compatible / deepseek-v4-pro-ga-260813 |
| thinking / Prompt / Schema | disabled / 1.0.4 / CandidateEvidenceSelection |
| timeout / max_tokens | 60 秒 / 6000 |
| 序列化 request chars / 实际 HTTP body chars | 9057 / 9037 |
| 原始 Evidence / alias 数 | 40 / 40，E01–E40 |
| HTTP / finish_reason | 200 / stop |
| Model Step latency / generation wall | 12568 ms / 12806 ms |
| input / output / total tokens | 2759 / 1239 / 3998 |
| reasoning tokens / repair / fallback | 0 / 0 / 无 |
| Structured Output / alias / literal validator | 全部通过 |
| skill normalization | 24 项，1 项向下收敛 |
| Generation / AgentRun / StepRun | 6 COMPLETED / 33 SUCCEEDED / 140 SUCCEEDED |
| ProfileVersion / current_version / version number | 1 / 1 / 1 |
| Generation key / Version key 唯一计数 | 1 / 1 |

稳定 Fact 共 32 条；项目 2 条、教育事实 6 条、技能 24 条、工作/实习 0 条。
这里教育数量是事实记录数，不代表六段独立学历。所有 32 条 compatibility statement
均精确等于至少一条所引用的 ResumeEvidence excerpt，71 条 Fact → ResumeEvidence
关系全部属于 Resume 38，snapshot refs 与 DB 关系一致。
summary=NULL，strengths=[]，unknowns=2；未生成 inferred ability/derived stable fact。

旧 generation、AgentRun 32 失败审计、ResumeReport、全部原 Evidence 前后完全一致。
只有一次实际 HTTP；没有 fallback、repair、再次模型调用、旧记录删除或半成品 current。
没有打印/保存模型原始响应、私有简历正文、隐藏推理或密钥。

## 文件和后续边界

本轮增量仅 schemas.py / context.py / prompts.py / agent.py、Profile fixture/tests、
M3 Replay demo fixture 与本文档。provider/router/config 的非思考策略是进入本轮前已有工作，
继续保留；其余先前 context/contract 修复及 docs/28–31 也未覆盖。

没有 migration/API shape 变更、没有 git commit。本轮没有启动 API/Worker/SearXNG；
已运行服务后续需受控重启加载新代码，不能用旧 Worker 自动重复模型验收。
Profile 已成功，停止继续 Profile 开发。真实 JD/面经导入只能使用维护者提供的真实 URL
及人工保存正文，不能把 DEMO/REPLAY 声称为真实采集；Live Search 继续冻结。
