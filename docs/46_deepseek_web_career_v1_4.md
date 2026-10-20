# V1.4：DeepSeek Web 职业资料采集与审核

日期：2026-10-20。**实现与离线业务验收已完成；真实官方 Web API 验收受凭证阻塞。不能宣称在线 JD/面经采集已成功。**

## 1. 范围与真实环境

复用 V1.3 共享 PostgreSQL 资料库、M2 Gateway / Research / Evidence、原有面经审核、JD 人工发布。没有新增 Agent、向量库、独立网关、Celery 引擎或数据库表，没有修改 `.env`、模型路由或历史 migration。当前模型仍为原配置的 `deepseek-v4-pro-ga-260813`，不自动恢复豆包。

进入任务前仓库已有大量 M1–M6、Profile 修复、Skill V1–V1.2 和 V1.3 未提交工作，全部保留。本轮不 commit/push、不清理旧失败记录、不向正式业务库写入验收数据。

重启后只启动本项目**已有** PostgreSQL / Redis 容器，不重建容器或 volume；浏览器与回归使用独立数据库。正式库仍沿用既有 0025，不新增 migration；0017–0024 无修改，0018 SHA-256 仍为 `3f4b951021427fbc3b629609897d3fa7cd732f400e00d8192a9223a90abdcd47`。

## 2. 官方接口核实与凭证

依据官方 [Search/Fetch 指南](https://api-docs.deepseek.com/zh-cn/guides/search/)、[Search API](https://api-docs.deepseek.com/zh-cn/api/search/)、[Fetch API](https://api-docs.deepseek.com/zh-cn/api/fetch/)：Search 使用 `query/count/need_content`；来源和日期筛选编码为查询语法，而不是虚构额外请求字段。Fetch 提交公开 URL，接收 Markdown 正文。

服务端只请求固定 HTTPS 官方 API：

- `https://api.deepseek.com/v1/tools/search`
- `https://api.deepseek.com/v1/tools/fetch`

新增独立 `DEEPSEEK_WEB_API_KEY`，默认空。**不继承 `LLM_API_KEY`，也不继承方舟 endpoint。** Key 不进入 ToolInvocation / AuditLog / 文档，provider 错误正文不落日志。

已检查本地 `.env` 与进程环境的 Key 是否存在（仅输出布尔值）：独立 Web Key 未配置，也未发现通用官方 DeepSeek Key。当前模型凭证属于另一服务商，不能拿它试探官方鉴权。因此没有执行一次“碰碰运气”的真实收费请求。

配置模板只增加空 Key 与有界参数，不更改真实 `.env`。默认每次网络 timeout=25 秒、最多一次重试，Gateway 单次总上限90秒。默认同数据库 UTC 日 Search10 / Fetch20 次，**所有尝试均计数**，不限于付费成功；跨进程 PG 短事务与 advisory lock admission，保守全局1秒间隔，因此单目标域名也不会超过该请求频率。不会通过新增管理员/任务绕过日预算。

401/402/403/404/413 不重试；408、429 和暂态网络/部分5xx只有限重试。过大的 Retry-After 停止，不无限等候/重试。200 非法 JSON 不重复付费请求。403 不切换直抓/SearXNG 绕过站点限制。目标 URL 先规范化并拒绝 localhost、私网 IP、非HTTP(S)、凭据和敏感参数；DNS/重定向实际抓取发生在官方服务端，返回403即停止，**不声称本地验证过服务端未披露的重定向链**。

## 3. Provider 与持久化语义

DeepSeekSearchProvider / DeepSeekFetchProvider 实现已有 SearchProvider / FetchProvider。Search 归一化 title/url/snippet/date 与 summary/authority/rerank_score 等 metadata；**实际 content 为独立正文字段**，与 snippet/summary 分开。

- Search metadata → 创建/复用 Source，保留关键词、原 URL、Provider、时间和参考信号。
- Search `content` → 通过 Gateway 的正文持久化路径创建/复用 Snapshot，标记 `body_origin=SEARCH_CONTENT`。
- 只有 snippet/summary → 不创建正文 Snapshot；选中后才 Fetch。
- 正文足够或已存在可核对的 Web 正文 → 复用，不额外 Fetch。
- Fetch → 原始正文 hash 与 normalized content，存入同一 SourceSnapshot 表。
- ToolInvocation 的外层调用和每次实际 HTTP admission 分别留审计，不伪造 Fetch 调用。HTTP attempt 的失败/取消也计数。

URL hash 唯一、`(source_id, content_hash)` 唯一沿用既有约束。不同 URL 可保存同一内容；独立来源按 normalized fingerprint 分组，不能把转载计成多份独立支持。正文变化形成新 Snapshot / JDVersion；相同 URL、正文和文档类型复用已有 extraction task。Source 来源身份/人工 metadata 不因再次搜索被覆盖。

默认 LIVE Gateway 现改为任务绑定的官方 Web Gateway，包含持久 HTTP 预算；SearXNG 保留显式工厂，但不是失败时自动 fallback。旧 importer 保留独立 MANUAL_SOURCE_IMPORT 身份，未改为假 Fetch。

## 4. 管理流程和状态

```text
管理员公开关键词
  → WEB_DISCOVERY: PENDING → SEARCHING → COMPLETED / FAILED
  → 选择真实返回 URL（不能任意附加另一个 URL）
  → 正文充分：复用；否则 WEB_ACQUIRE: FETCHING → COMPLETED / FAILED
  → 内容寻址 JOB_JD / INTERVIEW_DATA task: READY_FOR_EXTRACTION
  → 现有 Research: EXTRACTING → VALIDATING → PUBLISHING → COMPLETED / FAILED
  → JD: WAITING_REVIEW / APPROVED（仍不是普通用户可见）
  → 管理员审核发布 PUBLISHED
  → 面经: WAITING_REVIEW → 原有人工 APPROVED
  → V1.3 共享 /career 只读库
```

两次状态“完成”的含义不同：discovery 完成只表示搜索完；Research 完成只表示抽取完，**不等于发布**。正文获取与抽取之间没有自动付费串联，管理员明确点击抽取。

短事务认领后释放锁再网络调用；同 URL Fetch 共用认领，不能两个请求同时抓取。完成的搜索 request_key 重试复用，失败可以显式重试。超5分钟的短采集任务在刷新时标记中断失败，而非永久 pending；started_at lease 栅栏阻止恢复后的旧请求覆盖新执行状态。Research 延用既有认领/有限 model timeout，进程崩溃导致的 Research 孤儿任务仍需维护者诊断，不自动盲目再次付费。

## 5. 抽取、证据与可信度

没有绕过 Research 裸 INSERT 最终实体。Web task 保存不可伪装为人工导入的 manifest，解析器验证成功的 Gateway body audit、URL、Snapshot ID 与 raw hash，再构建现有 exact EvidenceCatalog。

Web Prompt 1.1.0 保留旧版本；正文视为不可信数据，不能执行网页指令。岗位身份仍须真实引用；Web requirement/question 和可选岗位职责/地点/发布时间/岗位编号须有原文支持。可选 attributes 只是已有 JD structured JSON 的扩展，另建 Claim/Evidence，不新增 DB 字段。未知不补全；无法支持岗位身份时失败留审计，不制造最终 Job。

authority/rerank 仅是检索信号，不能令资料发布。沿用域名/证据/quality gate，声明 official 也不等于核验官方；community JD 不能因权威值高变成官方。Web 面经强制人工审核。质量分析留在 JD metadata / 既有 AgentRun、StepRun，表示来源支持信号，不认证绝对真实。

管理员审核页可查看主体/岗位、正文预览、要求、证据、质量风险，并输入发布/拒绝/要求修正理由。JD 正文版本不原地改写；“要求修正”留下审核记录和 WAITING_REVIEW，不自动让模型改事实。面经通过已有详情编辑、题目修改和批准/拒绝/重跑处理。

普通用户只有已发布记录的投影；JD 默认最多500字符节选及岗位要求/来源链接，避免无条件全文转载。管理员正文用于核查，不是公开全文分发 API。共享 Source 类型可由已审核版本的来源声明投影，不改历史 Snapshot；私人简历/回答/Memory 从未送入搜索请求。系统无法阻止管理员故意在关键词中粘贴私密信息，UI 明确提醒且接口不接受 resume_id 等私有引用字段。

## 6. API 与页面

以下新增入口全部校验 ADMIN，付费写操作另外复用现有 Redis 入口限流：

| API（前缀 `/api/v1/admin/public-career`） | 用途 |
| --- | --- |
| GET `/status` | Key是否配置、Web日预算；不返回Key |
| POST `/search` | company/job_title/keywords/count/sites/日期/need_content/request_key |
| GET `/tasks`、`/tasks/{id}` | 最近任务、实际状态、结果、调用审计与正文预览 |
| POST `/tasks/{id}/select` | 选URL、文档类型、来源身份；取得/复用正文 |
| POST `/tasks/{id}/extract` | 现有 Research；FAILED 须明确 retry_failed |
| GET `/jd/{id}` | 管理员要求、主体、质量与精确证据 |
| POST `/jd/{id}/review` | publish/reject/request_correction + 理由 |

前端 `/admin/public-career` 纳入原管理员导航，不另造管理站。复用中文页面、loading/error、折叠正文与证据、恢复任务记录。面经跳到 `/admin/experience-review`，从审核队列进入详情。普通 `/career` 继续可进入 Matching、Skill 推荐和当前 M4 创建规则，不改变这些业务。

## 7. 实际测试与失败记录

- 针对性 Provider/真实 PostgreSQL/ASGI API 测试：包含200归一化、永久4xx不重试、429有限退避、超时、无Key、Replay阻断外网、非法JSON、筛选语法、正文复用、内容版本、并发Fetch合并、未知选择拒绝、模型失败/unsupported requirement、持久HTTP预算、转载去重、失联采集恢复，以及管理员审核后两个普通用户共享同一记录。
- 已完成的一次全量：独立 `interview_agent_f4_regression_v14_20261020_a`，**423 passed / 155.69秒**，包含此前404项与本轮19项。后续增加转载/失联两项及小幅页面/来源身份修正，最终重跑结果另记下文，不把前一次数字冒充最终版本。
- 前端原有40/40、TypeScript + production build 已通过；最终重跑结果见下文。
- 初期专项失败两次：测试共用隔离库的 Replay 日预算先被其他 fixture 消耗，导致预算专项在准备阶段拒绝。只调整**离线测试**的 cap 与相对预算断言，生产默认10/20不变。旧失败库/记录未删除。随后19项通过。
- 浏览器脚本首次在面经审核抽屉打开时点击背景退出，被正常遮罩拦截；关闭抽屉后完成登录流程。不是绕过遮罩强点，不改业务权限。

真实浏览器（不是 route.fulfill / 静态 HTML）：Vue + FastAPI + PostgreSQL + Redis + JWT。Web transport为明确 Replay MockTransport，模型为明确 Replay，二者不访问真实网络。

专用 `interview_agent_v14_acceptance_20261020_browser`：

1. DEMO ADMIN 正常登录 → 搜索任务1 → 返回两条 fixture URL，标注 Replay。
2. JD task2 → 真实 Snapshot/Claim/Evidence/Requirement → JD1 → 人工发布；刷新后状态仍在。
3. 面经 task3 → 原有 Extraction/Validation/Routing/Reliability/Gate → Experience1、两问题，WAITING_REVIEW → 原有管理员批准。
4. 普通 A、B 分别正常登录 `/career`，各可见同一岗位与面经两张卡；管理员入口数均为0。A刷新可恢复。
5. API 集成负例确认无JWT401、普通用户采集403、未审核404、不接受私人引用422；V1.3与既有全量用例继续覆盖私人Session/Resume/Report/Memory归属。

本地截图（ignored）：`output/playwright/v14-admin-published.png`、`v14-community-review.png`、`v14-public-user-a.png`、`v14-public-user-b.png`。已实际查看管理页截图。仅有 fixture 数据，不是真实官方职位或真实社区分享。

## 8. 真实接口、来源与费用结果

| 类别 | 实际请求 | Token / 费用 | 结果 |
| --- | ---: | --- | --- |
| DeepSeek官方Search | 0 | 0次Web请求；没有发生本轮Web调用费用 | 缺独立官方Key |
| DeepSeek官方Fetch | 0 | 同上 | 缺独立官方Key |
| 本轮LIVE模型抽取 | 0 | Token=0 | 不为弥补Web阻塞而消耗模型 |
| Mock/Replay Web、模型 | 有限本地测试 | 非服务商调用 | 单独标注，不计为在线成功 |

真实搜索关键词：**无**。真实新抓取URL：**无**。没有将用户给过的链接或搜索摘要假装成本轮实际网页正文。

浏览器实际 fixture URL 为 `https://fixture.example/browser-demo/jd` 和 `https://community.fixture.example/browser-demo/interview`。这两个是本地 MockTransport 匹配用地址，不在公网获取。JD/面经真实示例验收均**未执行**；已有正式库人工来源不会被重新包装成本轮 Search API 成功。

用户允许本轮最多Search10/Fetch20、估计约0.15元，当前未消耗。之后补Key才能进行单一有界真实批次；模型抽取另行记录请求/Token上限与账本，不能把Web调用额度当成无限模型授权，也不能复用上一轮已经结束的14次模型批次。

## 9. 文件范围

新增：

- `apps/api/src/interview_api/modules/public_career/tools/deepseek_web.py`
- 同模块 `web_audit.py`、`web_collection.py`、`web_router.py`
- `apps/api/tests/test_v14_web_provider.py`、`test_v14_web_collection.py`、`v14_support.py`
- `apps/api/scripts/v14_acceptance_runtime.py`
- `apps/web/src/pages/admin/AdminPublicCareerPage.vue`
- 本文。

增量修改：`.env.example`；API `core/config.py`、`main.py`；public_career `tools/contracts.py / providers.py / factory.py / gateway.py`、`research/agent.py / prompts.py / schemas.py / evidence.py`、`review.py`、`library.py`；Web `router/index.ts`、`i18n/zh-CN.ts`、`layouts/UserLayout.vue`、`pages/PublicCareerPage.vue`；README与文档索引。这里的 review.py / library.py 在进入本轮前就是未跟踪的既有工作，只做增量，不将整文件归为本轮重写。

## 10. 启动与最终人工体验

正常项目仍使用 `scripts/start-interview-platform.ps1` / status / stop。管理员入口 `http://127.0.0.1:5173/admin/public-career`；普通共享库 `http://127.0.0.1:5173/career`。未配置官方Key时，页面显示明确阻塞且禁用真实搜索按钮，不是空白假成功。

复现本轮离线浏览器环境（不需要真实Key；只使用既有专用库，不加prepare覆盖）：

```powershell
# 根目录，API终端
apps/api/.venv/Scripts/python.exe apps/api/scripts/v14_acceptance_runtime.py --database interview_agent_v14_acceptance_20261020_browser
# 另一终端：真实Vue，代理到上述隔离API
node scripts/v12_acceptance_web.mjs
```

Web `http://127.0.0.1:5175`；ADMIN `v14.admin@example.com`，A/B `v14.a@example.com` / `v14.b@example.com`；专用DEMO密码均 `DEMO-v14-local-only`，不适用于正式账号。

10分钟体验：管理员登录 → 职业资料采集 → 恢复任务1/2/3看来源、正文、证据与发布 → 重新搜索会明确Replay → 选择并抽取（不重复最终记录） → 原有面经审核 → 退出 → 普通A/B各自看同一公共库。真实体验需维护者在本地安全配置独立官方Key，重启API，不在聊天中粘贴Key。

## 11. 限制与停止点

真实鉴权、真实Search结果格式/域名可达性、真实Fetch、至少一个真实官方JD和一个真实社区面经、LIVE抽取质量与实际计费均未验收，阻塞原因明确为官方WebKey缺失。不能把开发完成写成“全部Live E2E”。

当前是有限短请求+显式阶段，不是无界爬虫或新增持久任务引擎。Web日预算是单业务DB的应用保护，不等价于服务商跨应用账单硬上限；进程崩溃的未知HTTP保守占用预算。域名信任依赖维护者配置/人工核实，不依赖搜索排名。版权合规仍需管理员按实际来源用途审核，500字符上限不是法律授权。

不扩展Live Search之外的新业务，不增加Agent，不进入V1.5。

## 最终回归与清理记录

- 独立最终回归库 `interview_agent_f4_regression_v14_20261020_final`：**425 passed / 232.33秒**（404既有 + 21项V1.4，覆盖未减少）。这一完整命令实际略超原先约三分钟的估计，没有挂起或另开真实模型批次。
- 全量后修正两处仅Web边界：quality/routing/reliability Prompt不再混入提取面试题指令；原文横跨相邻Evidence分块时允许连续、同Snapshot的literal支持，不允许跨缺口/跨网页拼接，不允许语义改写。后者不涉及Profile contract。
- 最后定向 **21 passed / 11.13秒**，包括最新Prompt、literal分块、JD拒绝/要求修正/发布及已发布不可覆盖；相关Provider/M2B evidence专项另 **30 passed / 6.56秒**。这些是全量后的定向复核，不伪称又跑了一遍整个425项。
- 前端最终 **40/40**，TypeScript检查与production build通过；前端现有测试部分为静态契约，不冒充全部组件测试，新增页面由上述真实浏览器实际验证。
- 浏览器最终再次用普通B刷新、读取岗位详情，访问管理路由被导回首页（`adminRouteDenied=true`）。追加截图 `output/playwright/v14-public-user-b-final.png`。
- Python compileall、tracked `git diff --check`、本轮10个新增文件的trailing/EOF检查通过。实际非占位API Key对diff及未跟踪文本扫描命中0；`.env`、`.runtime`、浏览器截图仍ignored且未被跟踪。全部模型route解析仍为原DeepSeek，官方Web Key仍未配置。
- 正式库只读核对revision=0025，DeepSeek Web LIVE HTTP审计行数=0，没有向正式库写入本轮fixture。
- 本轮Playwright会话已关闭；按PID与完整命令行核验后，仅停止隔离API/Web，保留专用数据库、截图和旧失败记录。PostgreSQL/Redis保持在线；没有停止其他项目或删除数据。
- 未commit、未push，工作区保留进入任务前和本轮所有修改。真实官方Search/Fetch及真实JD/面经示例仍是唯一凭证阻塞的验收缺项，不能宣称V1.4全部真实验收完成。

## 后续V1.4.1真实验收记录

以上为离线交付时事实，不覆盖。用户随后提供普通官方API Key，已完成真实Search2次、LIVE模型5次（15,236 Token）：JD通过审核发布与双用户共享；面经在严格原文字面校验失败，未创建或发布最终面经。Search正文充分，Fetch未额外调用。完整失败、用量和边界见 [V1.4.1 Attempt 1](47_deepseek_web_real_acceptance_v1_4_1.md)，不能标记为全部验收成功。
