# V1.4.2：面经原文锚定、独立 Fetch 与搜索来源约束

日期：2026-10-21。**离线实现和回归通过；独立 Fetch 真实通过；本次真实面经仍未完成入库发布。**

不要将本轮写成“全部 Live E2E 通过”。六个问题的片段锚点校验已通过，但真实模型给出的公司名称引用了不包含完整公司名的片段，整批被身份校验拒绝。没有绕过校验、改用别的文章或继续刷模型。

## 1. 真实失败根因与能够确认的边界

V1.4.1 的 `AgentRun 2` 只保存问题数量，没有保存失败的候选句子，因此**不能追溯断言当时具体哪句发生改写、错引或标点差异**。旧失败、docs47、原 Snapshot 均保留。

实际读取上轮隔离库的 Snapshot 5（664 字符）与 6（2112 字符）后确认：

- 原帖有四个短横线条目：机器学习模型及项目论文的复合条目、数据不平衡、再次询问项目论文、PyTorch/网络/优化器/梯度清零的复合条目；未必有标准问号。
- `又问项目论文` 是报道主题，不是确切面试问句。
- Search 正文包含“全部评论”和“相关推荐”；Snapshot 6 还有评论重复展开及导航。评论不能被默认为原作者本场经历。
- 旧链路使用 600 字符 Evidence 块，让模型生成展示问题后再要求该问题在原块中连续字面出现。**原文证明与展示规范化共用一个字段**，导致合理格式整理/改写与真实伪造无法通过这个字段清楚区分。
- `supports_literal()` 已有 NFKC、空白规范化及同 Snapshot 相邻块支持，并非完全不处理格式；但这不能证明自由改写的语义等价，也不应该通过取消字面规则解决。

## 2. 最小数据契约与文件

没有新增 Agent、数据库表、migration 或依赖；head 仍为 `0025`，未修改 `.env`、模型配置、正式业务数据或历史 migrations。

新增源码：

- `apps/api/src/interview_api/modules/public_career/research/fragments.py`
- `apps/api/scripts/accept_deepseek_web_v142.py`

增量修改：

- Research：`research/schemas.py`、`interview.py`、`evidence.py`、`agent.py`、`prompts.py`、`audit.py`。
- Web 边界：`tools/deepseek_web.py`、`tools/factory.py`、`web_collection.py`。
- 审核：`modules/experience/review_service.py`、`apps/web/src/api/admin.ts`、`apps/web/src/pages/admin/AdminExperienceReviewPage.vue`。
- 验收运行器：`apps/api/scripts/v14_acceptance_runtime.py`，LIVE 审阅模式额外禁止 Search/Fetch，与原有禁止模型调用共同保证浏览器审阅不消耗新费用。
- 既有测试/fixture：`test_v14_web_collection.py`、`test_v14_web_provider.py`、`v14_support.py`；负例从旧自由问句契约更新为未知片段拒绝，旧 Prompt 仍验证可读取。

新增专项：`test_v142_source_fragments.py`、`test_v142_search_scope.py`、`test_v142_interview_pipeline.py`、`test_v142_acceptance_budget.py`。

持久化复用：

| 内容 | 现有位置 | 语义 |
| --- | --- | --- |
| 原网页正文 | SourceSnapshot.content_text / content_hash | 原样保留，不被展示清洗覆盖 |
| 原文片段 | Claim.statement / ClaimEvidence.excerpt + source_location_json | 原文切片，带 Snapshot、起止位置 |
| 规范化题目 | InterviewQuestion.question | 明确为 derived display，等待人工核对 |
| 两者关系 | InterviewQuestion.routing_json.source_anchor | fragment_id、raw_excerpt、location、normalized_question、kind |
| 报道主题/排除信息 | InterviewExperience.extraction_output_json.source_anchors | 不进入精确题库问题 |
| 模型选择诊断 | ExperienceAgentStepRun.output_json | 有界公开选择条目；不保存完整响应或隐藏推理 |

管理员详情 DTO 新增可空 `source_anchor`；旧记录没有该字段仍可读。公开查询不新增原文全文、内部 Prompt 或 offset 暴露。

## 3. 先证据定位，再问题规范化

1. 按原始非空行/段落及列表边界构建有界候选，复用 `EvidenceSpan`，不建立平行 SourceSpan 表。
2. 稳定 ID 沿用 `ev_` 命名，由 Snapshot ID、原始 offset、完整 raw excerpt 的 SHA-256 得到。相同输入重复生成一致。
3. 显示清洗仅处理列表/标题/Markdown 标记、转义标点、NFKC 和空白；保留每个显示字符到原字符位置的映射，`raw_range()` 可还原原文范围。ClaimEvidence 始终保存真实 raw 切片。
4. 仅有一般叙述、标题或来源指令的内容不能充当精确问题。评论/推荐边界后不作为原帖候选。无法可靠判断时不强行提取；长行和预算截断条目隔离，不伪装完整正文。
5. Web 抽取使用新 `FragmentInterviewExtraction` 与 `interview_extract@1.2.0`：模型只返回片段 ID、分类、可选规范化问句、置信信号及有证据的身份，不生成 quote、位置、URL 或答案。
6. `REPORTED_TOPIC` 必须 `normalized_question=null`，仅保留原主题；复合真实片段允许拆出多个问题，共用同一个原文锚点。
7. 程序先解析存在的锚点，再进入既有 Validation → Routing → Reliability → Quality Gate。未知 ID、非问题叙述冒充问题、报道主题升级成精确问题继续拒绝。
8. 规范化文本不是原话，也不直接变成原文 Claim；管理员必须对照只读片段核对，不自动发布。审核 API 禁止改写锚点原文。

**能力边界：** exact anchor 能证明引用来自哪里，不能仅凭确定性程序证明任意自然语言改写都语义正确。引用真实片段却扩写新考察内容，仍需人工拒绝/修正；没有使用 embedding、语义相似度或第二个 LLM 作为证据裁判。

旧人工导入及非 Web Research 保持原契约；旧 Web Prompt 1.1.0、旧审计和历史记录没有改写。五维评价、SessionController、Skill、Profile、Memory 业务逻辑未调整。

## 4. 同一牛客文章的真实结果

仍使用原 URL，不换文章：

<https://www.nowcoder.com/discuss/427769028658184192?toCommentId=15083088>

本轮将旧隔离验收库克隆为 `interview_agent_v14_acceptance_live_20261021_v142_attempt1`。保留旧成功 JD 和失败 Run；不是向正式 `interview_agent` 写数据，也不是把 Replay 内容改称 Live。

真实模型针对**原 Snapshot 5**产生 7 项选择：

| 项目 | 类型 | 锚定结果 |
| --- | --- | --- |
| 机器学习模型与一个模型原理 | QUESTION | 指向第一复合片段 |
| 简历项目/论文介绍 | QUESTION | 同一片段；规范化语义仍需人工核对 |
| 数据不平衡的方案 | QUESTION | 指向第二条原文 |
| 又问项目论文 | REPORTED_TOPIC | 不生成精确问句 |
| PyTorch 网络构建 | QUESTION | 指向 PyTorch 复合片段 |
| 优化器选择 | QUESTION | 同一复合片段 |
| 梯度清零方法 | QUESTION | 同一复合片段 |

6 个问题均通过片段 ID 解析，1 个主题未混入题目。Extraction、Routing、Reliability 三次 Structured Output 均成功；无 repair、无 fallback。

**最终失败是另一条真实约束：** `Company/job identity absent from exact cited evidence: name='中国移动研究院'`。模型引用的两个 ID 分别对应标题与岗位简介；完整公司名在另一个 hashtag 片段中。当前引用不能支持该完整名称，属于 **wrong evidence reference**，不是 Markdown、Unicode 或截断错误。

程序不自动换引用、不缩写/猜测公司、不借真实存在但未引用的其他片段放行。Run 3 / task 4 保持 FAILED，最终面经/题目均为 0，未审核发布；旧 Run 2 FAILED 及 JD1 PUBLISHED 不变。

追加的有界修复仅把**同一身份规则前移**，现在公司/岗位引用失败会在 Routing/Reliability 前停止，并增加 `literal_elsewhere` 诊断。专项测试证明只用 1 次抽取调用；**没有再次调用模型证明修复后 Live 成功**。身份选择也纳入后续有界审计；不能用新增诊断反推已丢失的历史输出。

## 5. 独立 Fetch：真实成功，但不声称全文完整率

官方接口规范：[Fetch](https://api-docs.deepseek.com/zh-cn/api/fetch/)、[Search](https://api-docs.deepseek.com/zh-cn/api/search/)。本轮实际发出一次 `POST https://api.deepseek.com/v1/tools/fetch`，不是 Search content 冒充 Fetch。

| 项目 | 实际值 |
| --- | --- |
| 请求 URL | 上述同一牛客 URL |
| HTTP / Provider / mode | 200 / deepseek_web / LIVE |
| 耗时 | 14.413 秒 |
| title | 移动研究院算法工程师一面面经热乎的 |
| 正文 | 2305 字符 |
| Snapshot | 7，新版本；Source 复用 |
| hash | `61af133346ebc52b9c56c0f9ffd6f3c165bd965df0d476a09b1b1f48dcc53a82` |
| 与原 Search Snapshot5 | raw 不同；原 664 字符及 hash 未变 |
| 原文依据 | 每个保留片段都满足 `content[start:end] == excerpt` |

Fetch 带来更多评论/页面 UI，不等于更多面试事实。还发现它会转义列表和括号，例如 `\-`、`全部评论\(31\)`；最初解析无法识别转义的评论边界。已用该**保存的实际正文**补 RED/GREEN 回归和映射修复，未额外请求网络。

修复后 Snapshot5/7 均保留 3 个问题片段、1 个报道主题；显示规范化后的四个主体片段完全一致。评论边界分别位于原正文 325 / 418 字符处。没有声称拿到了网站 DOM 全文，也不计算虚假的完整率。

空正文、短验证/拒绝页直接失败，不生成成功 Snapshot；不足正文不能进入提取。任何 Fetch 都不会原地覆盖已有有效 Snapshot。本次没有站点 403，也没有换其他页面来证明牛客可抓取。

## 6. 搜索域名后置约束

- 显式 `sites` 仍生成服务商 `site:` 语法，但实际结果必须再次经 URL 安全规范化及 hostname 校验。
- 仅允许 `host == domain` 或 `host.endswith('.' + domain)`；支持合法子域名、大小写与尾点规范化。
- 相似域名、URL 用户名伪装、私网/无效 URL、显式跳转包装及不合范围的已披露 final_url 被拒绝。不用字符串包含判断。
- 原因记录在 `ToolInvocation.response_json.data.source_filter`，包含结果序号/原因，不记录可能带凭据的危险原 URL。
- 不合格结果不会生成 Source/Snapshot，也不能被管理员 selection 端绕过。提取时还会复核原 discovery 的范围，避免旧未审核漏筛任务继续入库；已发布旧 JD 的只读查询不受影响。
- 无显式域名限制时仍允许正常的不同公开域名，不扩大调用数量补足被筛掉的结果。
- 服务商未披露的服务端跳转链无法在本地证明安全；明确跳转包装宁可拒绝，不宣称已检查所有远端重定向。

本轮真实 Search=0；域名修复通过 Mock Provider 和真实 PostgreSQL/Gateway 集成验收，不冒充已重新验证服务商的 site 行为。

## 7. 自动化、浏览器与历史失败

最终隔离回归库：`interview_agent_v14_acceptance_20261021_v142_regression_final`。

- 全量后端按文件分两批：A **134 passed /72.05秒**，B **321 passed /116.91秒**，合计 **455 passed，无 skip**。包含既有 431 项与本轮 24 项；未减少旧覆盖。
- 本轮新增覆盖：稳定片段 ID/offset、Unicode/Markdown/转义映射、口语和复合条目、无题目/普通叙述/恶意指令、报道主题不升格、未知 ID 拒绝、真实保留快照离线复核、原始 Claim 与规范化问题分离、质量拒绝不覆盖、引用失败早停、域名漏筛后无 Source/Snapshot、预算守卫与未知 usage 保守计入。
- M1–M6、V1.1 Skill、V1.2 工具面试、V1.3 私有数据隔离与配额、人工导入/审核共享均在该全量范围；真实模型不参与 pytest。
- 前端 **40/40**（既有单元/静态契约），TypeScript + production build 通过。不会把这 40 项全部称为浏览器测试。
- Playwright 使用真实 Chrome、Vue、FastAPI、PostgreSQL、Redis 和正常 JWT 登录。Web transport 与模型明确为 Replay，未 `route.fulfill` 或使用静态 DEMO 页面代替服务。

真实浏览器流程：隔离管理员 → 页面搜索 Replay fixture → Source/Snapshot → 面经抽取 → 既有审核详情 → 核对只读原帖片段与规范化题目 → 点击通过并处理原生确认框 → 普通 A/B 各自登录 `/career` 读取同一面经1、两道题 → B 刷新恢复。A 请求管理 API 为403；无管理员导航。该403是主动权限负例，不是 JS 崩溃。

该浏览器成功属于 **Replay 后端业务链路**，不是本次失败的 LIVE 牛客面经被批准。截图在 ignored 的 `output/playwright/v142-*.png`。

保留的测试环境失败也不抹去：

1. 最初复用已有审计数据的测试库做迁移降级，4项被旧防审计丢失约束拒绝；换全新库，不改 migration/删数据。
2. 分批测试最初仅设置 `M1_TEST_DATABASE_URL`，一条报告删除 API 仍使用默认连接，返回404而非期望409；明确给两个数据库变量配置同一隔离库后重跑通过。未改业务断言来掩盖问题。
3. 新字段测试先 RED：模块/解析器缺失、域名漏筛、正常叙述误分类、身份校验过晚、Fetch 转义评论边界，随后最小实现使其 GREEN。

## 8. 真实请求与费用

授权单批上限：Search2、Fetch2、模型4逻辑/8HTTP、40,000 Token、600秒。实际一批，40.091秒结束，失败后停止，不利用剩余次数反复抽取。

| 调用 | HTTP | 秒 | input Token | output Token | total |
| --- | --- | ---: | ---: | ---: | ---: |
| Fragment extraction | 200 | 12.159 | 1644 | 956 | 2600 |
| Routing | 200 | 9.062 | 2012 | 819 | 2831 |
| Reliability | 200 | 3.675 | 2907 | 184 | 3091 |
| 模型合计 | 3 HTTP /3逻辑 | 24.896 | 6563 | 1959 | **8522** |

均 `openai_compatible / deepseek-v4-pro-ga-260813`、原配置 thinking disabled、finish_reason=stop、reasoning_tokens=0、cached_tokens=0；repair0、fallback0。没有改任何 route。

- Search **0**；Fetch **1** 次成功。按用户给定5元/1000成功请求估算约 **0.005元**，不是服务商账单核销。
- 模型价格未配置，费用保持未核实，不自行估价或宣称免费；usage 使用实际响应值，不把未知当0。
- 不充值、不购买、不改计费保护。attempt文件 `.runtime/acceptance/deepseek_web_v142_20261021_attempt1.json` 保留 `STOPPED`，不改写成成功。

## 9. 网页复现与停止点

只查看/复现本轮 Replay 审核业务结果：

```powershell
apps/api/.venv/Scripts/python.exe -X utf8 apps/api/scripts/v14_acceptance_runtime.py --database interview_agent_v14_acceptance_20261021_v142_browser
# 另一终端
node scripts/v12_acceptance_web.mjs
```

Web `http://127.0.0.1:5175`；DEMO 管理员 `v14.admin@example.com`，普通 `v14.a@example.com` / `v14.b@example.com`，隔离密码均 `DEMO-v14-local-only`。

1. 管理员访问 `/admin/public-career`，恢复任务1/2查看 Replay 标签、来源、正文和完成状态。
2. `/admin/experience-review` 打开面经1，看规范化题目和只读原帖片段；现有条目已审核，重复相同输入会复用，不制造第二份正式记录。
3. 新待审条目应核对规范化内容未扩写、身份/轮次有原文依据，再用既有“通过/拒绝/修正”机制；未审核普通用户不可见。
4. A/B 正常登录 `/career` 读取同一面经，刷新验证持久化。

只读查看本轮真实失败与 Fetch 审计，改用：

```powershell
apps/api/.venv/Scripts/python.exe -X utf8 apps/api/scripts/v14_acceptance_runtime.py --database interview_agent_v14_acceptance_live_20261021_v142_attempt1 --review-live
```

同端口不要同时启动两套 API。LIVE 审阅模式禁止新增 Web/模型调用。task4=FAILED 不能假装待发布，旧JD仍PUBLISHED；牛客真实面经尚无最终实体可供审核发布。

**剩余阻塞：** 当前模型的公司身份 Evidence 选择仍需后续有界验收；本轮未重试。规范化题目的语义适当性仍需人工核对。候选切分是保守、有界的纯文本策略，不等于网站 DOM 解析或全网帖子识别。没有进入下一阶段、commit 或 push。

## 10. 最终检查与清理

Python compileall、tracked `git diff --check`、新增文件 trailing/EOF 检查通过。对本轮变更扫描实际本地密钥值，命中0；`.env`、`.runtime`、截图仍ignored。migrations无diff，0018 SHA-256仍为 `3f4b951021427fbc3b629609897d3fa7cd732f400e00d8192a9223a90abdcd47`。

已关闭本轮专用 Playwright 会话，并核对 PID/完整命令行后停止隔离 API/Web；保留 PostgreSQL/Redis、全部隔离库和旧失败文件。Windows `Stop-Process` 出现系统异常，改用同 PID 的原生 Terminate 后复核进程已退出，没有扩大停止范围。

原文审核页与 A/B 共享页面截图已保存；最后一次大卡片局部截图因元素稳定等待超时，未把该局部截图宣称成功。其 DOM 对照检查、审核、发布及双用户读取均已完成；现有可用截图和失败信息保留，不因此修改业务 UI 或伪造截图。
