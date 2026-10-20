# 真实人工 E2E：导入与网页手测交付

## 验收边界

本轮使用正式 `interview_agent` 数据库，revision 为 `0024`。真实公开网页的人工保存正文通过
`MANUAL_SOURCE_IMPORT + LIVE MODEL` 入库：Source → SourceSnapshot → PublicDataRefreshTask →
PublicDataResearchAgent / 既有面经 extraction → Claim/Evidence → 待审实体 → 人工审核发布。
没有执行 Live Search，没有伪造 Fetch 成功审计，也没有直接 INSERT 最终 JD/面经作为替代。
高校转载仍为 community，未升级为 official。失败尝试和旧来源替换历史保留。

本轮正式模型使用 `openai_compatible / deepseek-v4-pro-ga-260813`，成功调用没有 fallback。
真实用户已有画像保持不变，没有向其面试填入测试回答，也没有替用户批准长期 Memory。
真实用户完整回答、Evaluation、Coach 与 Memory 审批仍由本人接下来手测。
浏览器完整流程使用独立 DEMO 数据库和明确标记的 Replay 模型，不称为全部 Live E2E。

## 正式资料状态

| ID | 来源身份 | 正式记录 | 审核结果 |
| --- | --- | --- | --- |
| JD01 | 华为 / AI应用工程师；official | Task 9、Snapshot 9、JD 4、Job 4 | PUBLISHED / ACTIVE |
| JD02 | 中国移动通信集团浙江有限公司嘉兴分公司 / AI工程师；community | Task 1、Snapshot 1、JD 1、Job 1 | PUBLISHED / ACTIVE |
| JD03 | 中国工商银行广东省分行 / 科技菁英；community | Task 2、Snapshot 2、JD 2、Job 2 | PUBLISHED / ACTIVE |
| JD04 | 中国移动通信集团湖北有限公司 / AI研发工程师；community | Task 3、Snapshot 3、JD 3、Job 3 | PUBLISHED / ACTIVE；博士要求保留 |
| EXP01 | 华为 / 后端开发岗；community，整理帖 | Task 4、Snapshot 4、Experience 20 | APPROVED；20 道可索引审核题 |
| EXP02 | 字节跳动 / 算法岗；community，整理帖 | Task 5、Snapshot 5、Experience 21 | APPROVED；20 道可索引审核题 |
| EXP03 | 上海银行 / 金融科技（应用开发）；community | Task 6、Snapshot 6、Experience 18 | APPROVED；14 道可索引审核题 |
| EXP04 | 中国移动研究院 / AI算法工程师；community | Task 7、Snapshot 7、Experience 19 | APPROVED；4 道可索引审核题 |
| EXP05 | 中国工商银行 / 科技菁英；community | Task 8、Snapshot 8、Experience 17 | APPROVED；7 道可索引审核题 |

共 65 道审核通过、可索引问题。EXP05 中一条原有候选问题的引用正文不完整，按审核流程拒绝，
没有补写证据。EXP04 泛化问题未作为可索引技术题。审核通过不声明社区面经绝对真实。

精确 URL、正文 SHA-256、旧 mismatch URL 和替换说明见
`data/manual_sources/2026-10-05/manifest.json`。9 个正文 TXT 均 ignored、未被 Git tracking；
逐文件字符数和 SHA-256 与 manifest 一致。JD01 在首次正式导入前补录同一官方页面真实页脚，
用于公司身份 grounding；旧正文 hash 保留，未伪装为同一个已持久化 Snapshot。

复核时对 9 份材料再次执行 `ManualImportService.prepare`，全部返回原任务、原 Snapshot、
`reused=true / COMPLETED`；对 COMPLETED 执行入口即返回，不访问 router，不再次调用模型。
所有 9 个 Snapshot acquisition mode 为 MANUAL_SOURCE_IMPORT，
`created_by_tool_invocation_id=NULL`，没有声称来自 Live Fetch。

## 真实匹配与面试准备

真实用户 Resume 38 的当前 ProfileVersion 仍为 1。真实匹配 Run 1 已 COMPLETED：

| 最终顺序 | 岗位 | 推荐等级 | 硬资格 |
| --- | --- | --- | --- |
| 1 | 华为 AI应用工程师 | GOOD_MATCH（较为匹配） | UNKNOWN |
| 2 | 工商银行广东省分行 科技菁英 | UNCERTAIN（信息不足） | UNKNOWN |
| 3 | 中国移动湖北 AI研发工程师 | UNCERTAIN（信息不足） | UNKNOWN；博士要求未消失 |
| 4 | 中国移动嘉兴分公司 AI工程师 | UNCERTAIN（信息不足） | UNKNOWN |

pre_rank、agent_rank、final_rank 和 Evidence Matrix 均落库。华为矩阵有真实 SUPPORTED 项，
也保留 NO_EVIDENCE 与 NOT_APPLICABLE，未显示匹配百分比。
匹配 AgentRun 52 使用 DeepSeek，98.602 秒，input 20806 / output 8910 / reasoning 3845 Token，repair 0。

既有 Profile 没有可可靠用于硬资格判断的结构化学历、专业、毕业年份，M6 既定策略保持 UNKNOWN，
不把 UNKNOWN 猜成满足，也不因项目技术匹配推断满足博士条件。
当前岗位仅 4 个，coverage=LIMITED，不联网补库。

真实面试 Session 50 已启动，3 个主问题预算，IN_PROGRESS；首题可作答。
InterviewMainAgent 的 AgentRun 53 使用 DeepSeek，11.964 秒，input 19389 / output 738 /
reasoning 594 Token，repair 0。首题来自已审核公共问题 52。
ContextSnapshot 固定 ProfileVersion 1、Job 4、JD 4，没有回头读取新版本替换。
复核时本场 Answer=0、Evaluation=0，未写入合成用户回答。

只有 EXP01 已精确绑定华为 Company；它不是 AI 岗专属面经，job_id 仍 NULL。
其余来源主体与四个 Job 不同，保留未关联 FK，不将研究院/总行/另一银行错误归并到省公司或省分行。
因此五篇虽已入正式审核库，并不会全部进入同一场华为 InterviewContext。

## 本轮最小修复与冻结范围

- Importer CLI 和 manual-source acquisition adapter 保持冻结；复用已完成任务的幂等逻辑。
- Research 支持配置化 non-thinking（仅 public research routes），避免纯抽取耗尽深度思考预算；
  Profile 的既有 non-thinking / 60 秒预算不变，Matching / Interview / Coach 的 route 策略不变。
- 人工来源使用单独版本化 extraction Prompt 1.0.1；旧 1.0.0 可用于历史审计。
  输入身份仅用于限定范围，最终身份仍必须有真实 Snapshot Evidence。
- 身份格式校验仅规范化空白及同一 Snapshot 中连续相邻 span，不用语义模糊匹配或跨段拼事实。
- 整理帖有界抽取最多 20 条代表性原文问题；未采纳的正文仍完整保留，不编造答案。
- 补齐已存在待审 JD 到 PUBLISHED 的维护者审核 service/CLI：ADMIN 校验、精确 provenance、
  原文支持的条件、AuditLog、调用者拥有的单事务。已发布版本不可原地改写。
  不是新的普通用户 UI，不新增 Agent 或 Schema。

未修改 Job Matching / Interview / Coach / Memory 业务逻辑，也没有继续重构 Candidate Profile。
入场前未提交的 Profile、Importer、文档和测试修改全部保留。本轮不 commit、不 push。

## 回归与浏览器结果

- backend：独立库 `interview_agent_f4_regression_real_manual_20261006_b`，299 passed，113.09 秒。
  原有 287 项保留，新增 12 项覆盖人工 JD 审核、回滚/幂等、来源校验、route 隔离及人工 Prompt。
  首轮旧测试受新增 `.env` research thinking 配置影响；仅补测试默认配置隔离后全量重跑通过。
- frontend：11/11；production build / vue-tsc 成功。
- Python compile 通过；`git diff --check` 与精确冻结 EOF 检查通过。
- `0017–0024` 与 HEAD 原始 Git blob 字节一致；16 个冻结 EOF 文件 hash/blob 无漂移。
- tracked / 可提交 untracked 文件与 diff 的实际配置 secret 扫描无泄漏；
  `.env`、backup、runtime、原文 TXT、浏览器截图均 ignored。
- Playwright：独立 DEMO 数据库 `interview_agent_m6_f3_ux_demo`，正常表单登录 → 简历/画像/岗位 →
  推荐创建面试 → 6 轮回答/SSE评价 → 中途刷新 → COMPLETE → Report/Coach → 批准 MemoryProposal →
  成长记录。Session 3 COMPLETED，6 Answer / 6 Evaluation，0 PENDING/ASKED；
  Report 3 current+COMPLETED，Proposal 3 APPROVED、MemoryItem 3。
  页面 DEMO / Replay banner 明确，未将这些记录混入正式库。
- 正常 LIVE 登录页再次实际通过 Playwright 打开，中文表单正常。未持有真实用户密码，
  未通过 JWT 注入或重置账号绕过认证；真实账号登录后完整长流程由本人验收。

## 启动与当前状态

恢复任务时服务和基础设施未运行，已用原脚本启动，不删除已有 volume/container。
PostgreSQL、Redis、MinIO、Milvus、API、Worker、Web 均健康；Worker ping 成功，revision 0024。
SearXNG 未启动，Live Search 继续冻结。临时 DEMO 应用已停止，数据保留供复现。

在仓库根目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-interview-platform.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/status-interview-platform.ps1
# 手测结束后安全停止项目 API / Worker / Web，基础设施和数据默认保留
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/stop-interview-platform.ps1
```

Web：`http://127.0.0.1:5173`；health：`http://127.0.0.1:8000/api/v1/health`。
本人登录后先看 `/profile`，再打开 `/jobs/matches/1`；推荐先测试华为 AI应用工程师，
从模拟面试历史继续 Session 50，或从推荐卡按当前 M4 规则创建新会话。
API / Worker / Web 日志在 `.runtime/platform/logs/local-*`。

正式导入前备份：`.runtime/backups/manual-real-20261005-1600/interview_agent.dump`，
521064 bytes，原生 custom pg_dump 成功且 pg_restore 解码验证成功；备份和 F0/F1 旧备份保留。

## 已知限制

- 真实用户仍须用原账号登录并亲自回答；本轮没有替本人完成真实 Evaluation/Coach/Memory 审批。
- 博士、专业、毕业年份的候选侧可靠硬资格不足，UNKNOWN 不是已满足。
- 当前结构化地区/专业列表覆盖不足，部分列表只录入了原文中的子集，不能视为完整可选集合；
  本轮真实 matching 没有地区筛选。首轮手测请先不设置地区过滤，完整原文仍在 Snapshot/JD 中。
  不原地改写已发布历史 JD，也不修改 M6 policy 掩盖这一数据覆盖限制。
- 整理帖最多保存 20 个代表性抽取问题，不代表原文全部问题已结构化。
- 五篇面经的公司/岗位并非四个 JD 一一对应，不猜测关联；华为面经是后端整理帖，不是 AI 岗官方题库。
- matching 的真实模型约 99 秒，页面应等待完成；真实模型延迟/服务可用性不作稳定时延承诺。
- 本地仍用 development JWT 配置，不用于生产部署；Live Search 未恢复，未宣称稳定成功。

到此停止开发，不扩功能、不新增 migration/Agent、不 commit/push。
