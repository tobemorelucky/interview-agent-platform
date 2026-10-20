# 真实面试卡死恢复、性能与对话验收

日期：2026-10-06。仅修复已授权的恢复、性能和对话问题；没有新增 Agent、migration，没有修改 Profile/Research/importer 的业务实现，没有 commit/push。

## 1. 原始卡死的证据

真实用户 38 / Resume 38 / ProfileVersion 1；原问题属于 Session 57，不是 51/52。

- Answer 9 和用户消息已保存；原状态 RUNNING，无 Evaluation。
- Evaluation AgentRun 82 / StepRun 207 原为 RUNNING。实际模型 DeepSeek V4 Pro，历史 prompt 1.0.0。
- API 日志记录 SSE 请求之后约 12 秒发生请求取消、数据库连接取消清理异常。HTTP 200 只是流响应开始，不证明 Evaluation 成功。
- `asyncio.CancelledError` 不会被原 `except Exception` 捕获；连接中断时模型、审计、Answer 和预算预留未完整结算。前端也没有可靠地从数据库恢复处理中/失败状态。
- 这是 API 请求内的异步评价，不是 Celery Worker 作业。没有证据把原失败归因于 provider timeout、长 reasoning 或 state_version conflict；历史 usage/latency 缺失保持 NULL。

恢复没有重复创建 Answer：同一 Answer 9/业务键被安全处理，旧 Run/Step 标记 FAILED/interview_request_interrupted。恢复时原 7200 秒会话时间预算已实际过期，因此 Answer 9 现在明确 FAILED/INTERVIEW_BUDGET_EXHAUSTED，无 Evaluation、无自动 COMPLETE；保留原回答和快照，不能靠重试重置预算。Session 51/52 未因本轮验收被改写。

## 2. 最小修复

- 同业务键 RUNNING 请求有处理 lease、阶段和截止时间；有效 lease 阻止重复模型调用，过期恢复由短事务与 session version 校验隔离旧请求。
- 显式捕获取消，屏蔽取消保护审计/Answer 清理和预算结算；未知模型用量保守处理，不伪造 usage。
- SSE 模型任务有界、断连后清理；阶段 heartbeat 表明“正在评价/正在生成下一题”。结果事件仍然只在 Controller 提交成功后发送。
- 页面刷新从持久化 pending 状态恢复，轮询实际数据库状态；失败显示安全重试，继续复用原 Answer key。预算耗尽明确提示新建面试，不承诺重试可重置预算。
- 仅 `answer_evaluation` route 设置 thinking disabled。Main 仍使用 provider 默认策略，Profile 既有 non-thinking 设置不变。现有实际 Interview timeout 180 秒、max output 3000 未改动。
- Main prompt 1.0.5、Evaluation prompt 1.0.2：围绕具体回答和缺口只追一个核心焦点；已回答的追问转技术决策深挖/下一题，不重复项目背景清单。旧 prompt 版本保留。

主要文件：`interview/{agents,controller,core_service}.py`、`core/config.py`、`infrastructure/models/router.py`、`InterviewChatPage.vue`、`types/interview-processing.ts` 及相关测试。上述列表不代表工作树中此前已有的 Profile/Research/UI 修改来自本轮。

## 3. Matching 分阶段实测

已有推荐的 GET 没有模型调用。重启后的浏览器正常打开 MatchRun 1 返回 200；最新 MatchRun 8 刷新，API detail 19ms、history 13ms，推荐卡可见约 224ms（包括浏览器自动化开销，不是纯网络计时）。

| 阶段 | 本轮新真实 Run 6 |
| --- | ---: |
| 创建 Run | 174.50ms |
| DB candidate query | 19.82ms |
| eligibility | 0.044ms |
| Evidence matrix | 6.874ms |
| 模型 rerank 审计 latency | 59612ms |
| generate 总计 | 59752.71ms |

原 Run 4 / Run 5 模型分别 120674ms / 95997ms；最长阶段明确是模型。没有通过并行模型刷成功。

- LLM-facing DTO 去除仅服务器需要的 offset/hash/重复元数据，保留所有事实、Evidence key/excerpt、Requirements、eligibility 和确定性支持矩阵；持久化 snapshot/validator 不变。
- Matching prompt 1.0.1 限制重复说明；历史 1.0.0 可审计。输入 tokens 从约 20820 降至 18074，输出从约 8422–10336 降至 4246。Run 6 reasoning 3537、repair 0、无 fallback。
- detail/history 并行加载。
- 完整冻结输入完全一致时复用同用户成功结果，包括实际 ProfileVersion、JD/Requirement/岗位状态、已批准偏好、filters、policy/prompt/配置模型。不匹配则重新生成。当前画像选择器与显式同一版本选择器等价，但实际版本变化仍失效。
- Run 7 generate 实测 78.06ms；重启后通过真实页面正常创建 Run 8，generate API 104ms，COMPLETED、owner 38、Profile 1，Agent calls 为空。页面明确“复用已有匹配结果，未再次调用模型”，没有伪造新模型审计。
- 新 Run 独立完整持久化，旧 Run 不变；不可变 coverage 不 UPDATE；复用来源由已有 producer 字段和展示 DTO 表达。

主要文件：`job_matching/{context,prompts,agent,service}.py`、`JobMatchingPage.vue`、相关类型/测试。没有放宽 eligibility 或 Evidence validator。

## 4. 三轮真实模型浏览器验收

Session 58 验证真实刷新中断、取消审计和同一 Answer 10 安全重试：一次失败调用保留 FAILED/6049ms，usage 未返回保持 NULL；重试成功后只有一个 Answer 和一个有效 Evaluation。

最终 Session 59 明确标记 ACCEPTANCE/测试回答，绑定真实用户 38、ProfileVersion 1、华为 AI应用工程师。

1. 项目介绍 → 简短泛回答 RAG/黑马点评/Docker/Python/Java/Redis/MySQL/LangChain → Evaluation 10 → 针对 RAG 检索链路 FOLLOW_UP。
2. 回答清洗、权限过滤、embedding、召回、重排序和来源标记 → Evaluation 11 → 针对候选数/保留数、文档冲突的 DEEP_DIVE。
3. 给出明确标注的本地技术方案，不宣称生产效果 → Evaluation 12 → Controller 因同一主题追问/深挖额度已消费，执行 NEXT；第四题正常 ASKED。

结果：3 个 COMPLETED Answer（12/13/14）、3 个有效 Evaluation（10/11/12），对应正确、无重复；Session IN_PROGRESS，当前 Q47，state_version 7，预留预算 0、无 pending。刷新/最终重启后仍显示 3/6 和第四题，没有第一题后错误 COMPLETE，也没有提前展示第四题参考答案。

全部实际模型 `openai_compatible / deepseek-v4-pro-ga-260813`，无 fallback、repair 0。

| 调用 | latency ms | input | output | reasoning | thinking |
| --- | ---: | ---: | ---: | ---: | --- |
| Main 首题 | 14166 | 13682 | 792 | 655 | provider default |
| Evaluation 1 | 6680 | 13396 | 369 | 0 | disabled |
| Main FOLLOW_UP | 30559 | 14364 | 1946 | 1753 | provider default |
| Evaluation 2 | 8125 | 13542 | 408 | 0 | disabled |
| Main DEEP_DIVE | 19945 | 14554 | 1118 | 874 | provider default |
| Evaluation 3 | 8440 | 13686 | 479 | 0 | disabled |
| Main NEXT | 16004 | 14767 | 852 | 627 | provider default |

截图位于 ignored `output/playwright/latency-interview-three-rounds.png`、`latency-matching-reuse.png`。测试回答不写为长期 Candidate Fact/Memory。

## 5. 回归与冻结保护

- Backend：319 passed / 111.95s；相较入口 311 增加恢复、幂等、预算、route、上下文投影/复用覆盖，没有减少原测试。
- Frontend：27/27；相较入口 24 增加处理阶段恢复/失败与并行加载覆盖。
- Production build、Python compile、whitespace check、git diff --check 通过。历史 EOF 豁免维持明确路径白名单，不全局关闭检查。
- 浏览器 Playwright：真实登录、MatchRun 1/8、真实三轮模型、处理中提示、断连安全重试、刷新/重启状态恢复均通过；不是新增编造的 Playwright suite 数量。
- 正式 revision 0024；0017–0024 八个 migration hash 与冻结基线一致；Profile current=1/version count=1；旧 Memory snapshot/审批记录不漂移；secret scan 无泄漏，`.env/.runtime/output` 仍 ignored。
- API / Worker / Web 正常 LIVE_MODEL_CONFIG 启动。SearXNG 不启动，Live Search 继续冻结；没有 commit/push。

执行的主要命令：

```powershell
$env:PYTHONPATH='apps/api/src'
apps/api/.venv/Scripts/python.exe -X utf8 scripts/run-regression.py --database interview_agent_f4_regression_20261006_latency_e
apps/api/.venv/Scripts/python.exe -m compileall -q apps/api/src apps/api/tests
apps/api/.venv/Scripts/python.exe scripts/check-whitespace.py
# apps/web 下：
node --experimental-strip-types --test tests/*.test.mjs
pnpm build
git diff --check
```

回归 migration 测试使用独立数据库，不 downgrade 正式数据库；保留所有验收数据和此前工作树修改。

## 6. 手测与限制

- 推荐：`http://127.0.0.1:5173/jobs/matches/8`（历史 1 也仍可打开）。从华为卡点击“开始模拟面试”，正常创建本人的新会话。
- 新华为面试入口：`http://127.0.0.1:5173/interview?company_id=4&job_id=4`。
- 验收会话：`http://127.0.0.1:5173/interview?session_id=59`，含明确标注的测试回答，不作为纯人工面试记录。

Main 默认思考仍耗时约 14–31 秒，不宣称所有调用即时返回。原 Session 57 已真实超出时间预算，不改历史预算强行恢复。学历/专业/毕业年份缺少可验证结构化证明时仍 UNKNOWN，不能以模型猜测或自由文本替代。岗位库目前仅四项；Live Search 未恢复。本轮没有再次生成 Report/Coach 或批准 Memory，只保持其回归/既有快照不受影响。
