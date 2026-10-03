# F3：全中文产品体验收尾

日期：2026-10-03。定位：**面向秋招的多 Agent 求职与模拟面试系统**。

## 功能冻结 checkpoint

F2 Attempt 2 已验收：六个正式角色均使用真实 DeepSeek `deepseek-v4-pro-ga-260813` 成功，未 fallback。
冻结基线为 backend 201 passed、frontend 7/7。F2 的旧失败 attempt、成功 attempt 和文档事实原样保留。

F3 开始前对 397 个待保留文件建立 SHA-256 清单：`.runtime/finalization/f3-freeze.json`。
清单用于区分此前 M1–M6/F2 工作与本轮前端改动；不 reset/clean/checkout/squash，不删除未提交文件。
`.env`、`.runtime`、原数据库备份和浏览器产物均被 Git 忽略；实际 API key / 管理员密码未发现进入待提交文件或 diff。
已确认正式库、Attempt 1、Attempt 2 和 F3 DEMO/回归库分离；不打印连接密码。

**未执行 Git commit/tag。** 建议用户确认后审核并分阶段暂存全部已验收工作：

- 业务冻结 checkpoint 提交说明：`chore: checkpoint M1-M6 and F2 DeepSeek acceptance`；tag 建议 `checkpoint/f2-deepseek-accepted-20261003`。
- F3 独立提交说明：`chore(web): finalize Chinese product UX`；tag 建议 `checkpoint/f3-ux-accepted-20261003`。
- 当前工作区已经包含 F3 增量，不能直接 `git add -A` 后把混合结果标为“纯 F2 checkpoint”；先审核分阶段暂存。F3 开始时的文件哈希不是 Git commit，也不冒充已创建 tag。
- 忽略文件不能强制加入提交；备份/凭据/数据库/运行日志不得提交。

Agent、Workflow、Schema、ModelRouter、Prompt、validator、Controller、统计、审批与旧 migrations **无 F3 修改**。不新增 migration、依赖或后端 API。

## 页面信息架构

用户主流程：上传简历 → 候选人画像 → 选择 / 推荐岗位 → 模拟面试 → 回答评价 → 面试报告 → 长期成长记录。

| 页面 | 展示重点 |
|---|---|
| 首页 | 当前简历、画像版本、完成面试次数、最近报告均分、当前可读取报告的待处理建议；四个主要操作；下方六 Agent 产品说明 |
| 我的简历 | 保留上传/解析链路，中文状态与空态，明确画像入口语义 |
| 我的画像 | 摘要、技能、项目、教育、工作/实习、材料未覆盖信息、版本；“查看依据”展开原始 excerpt；保留修正机制 |
| 岗位推荐 | 中文资格与推荐等级、已有证据、材料缺口、准备重点；保留历史记录与开始面试按钮 |
| 模拟面试 | 固定公司/岗位/画像/JD版本、当前题型/难度、进度；“面试官 Agent / 我的回答”；折叠五维评价、已覆盖/待改进点与下一步 |
| 面试报告 | 本场数据与 Coach 分析分区；逐题复盘；历史报告；批准 / 修改后批准 / 忽略长期记忆建议 |
| 成长记录 | 本人批准的长期记忆与偏好；旧动态表现估计单独说明，不宣称稳定能力 |

所有页面沿用 App.vue 的共享 UserLayout。普通用户导航统一为：首页、我的简历、我的画像、岗位推荐、模拟面试、面试报告、成长记录。
详情路由保持父级 active 状态；报告详情不会同时激活模拟面试。
知识问答保留为首页辅助入口，不替代主流程。
管理员导航在独立折叠区域：知识库管理、面经采集、题库 / 面经审核、审计日志。
公共岗位与 Research / Tool audit 尚无独立现有页面，不为 F3 新增管理业务功能。

## 中文映射与安全展示

集中映射：`apps/web/src/i18n/zh-CN.ts`；前端显示变更不改 API/DB enum。
包括六 Agent 名称、Session/Question/Report 状态、Eligibility、Fit band、Evidence/Skill 状态、MemoryProposal、模型模式与错误提示。
推荐等级为高度匹配 / 较为匹配 / 可尝试 / 信息不足；资格为已满足 / 明确不满足 / 暂无法判断，不显示匹配百分比。
确定性 Evidence 交集文案中的英文 enum 仅在显示层翻译，原数据与政策保持不变。

UUID、内部 ID、prompt/model version、fingerprint、原始审计收进“技术详情”；普通错误显示中文，详情保留真实 error code/request ID，不展示 provider 原始响应或 Python 栈。
`UNSUPPORTED_SKILL_STATE` 映射为画像结果与简历证据不一致的中文提示；不修改 validator。
没有数据时显示中文说明；加载中、失败和空数据分别处理，不用 `[] / null / No data` 作为空态。

沿用 Vue 和原样式，无大型 UI framework；新增轻量全局 spacing、字体、focus、卡片与详情展示规范。
新增 SVG favicon 与中文 404；登录/注册加入浏览器 autocomplete 提示。

## DEMO / Replay / Real Model 边界

- 明确演示账号的共享顶部显示 DEMO 横幅。判断依据为现有 demo 命名账号，不建立新权限/环境 Schema。
- Replay producer/provider 的记录显示 `DEMO · Replay 回放模式`，明确“不是 Live 模型验收”。
- 实际模型字段为 DeepSeek 时显示 `DeepSeek 实时模型`；未知 provider 不冒称 DeepSeek。
- 原始简历、Evidence excerpt、问题正文和招聘材料不自动翻译/重写；原内容可能有英文技术词或 fixture 原文，这是源材料，不是 UI 英文占位符。
- 合成公司的随机技术后缀不铺在普通界面；仍明确显示 DEMO，公司/岗位引用与 Snapshot 内容不改动。
- F3 不调用 Live Search 或真实模型。F2 真模型结果仅引用此前真实验收事实，不能将 F3 Replay 浏览器验收称为“全部 Live E2E”。

## Playwright 用户流程验收

使用 Playwright 技能的 CLI-first 流程：快照 → 操作已观察控件 → 新快照 → 截图/刷新验证。
Windows 直接运行已缓存的 Playwright CLI，无新安装测试框架。

环境：`interview_agent_m6_f3_ux_demo`（独立 PostgreSQL，既有 0024）；API 8013；Vue 5174。
`apps/web/qa/replay_runtime.py` 是显式浏览器测试入口，复用现有服务和 fixtures；ModelProvider 为 replay，HTTP 出网 sentinel 阻断调用。
正式 `interview_agent`、F2 Attempt 1/2 和原备份不用于写入 DEMO。

已实际跑通：

1. 表单登录公开固定 DEMO 账号 → 我的简历 → 我的画像 → 历史岗位推荐。
2. 从 AI 应用岗位推荐卡点击“开始模拟面试”，由现有 M4 创建新 Snapshot，首题成功。
3. 固定 DEMO 回答经过浏览器 / SSE / 真实业务 API，共 6 轮；第一次回答后刷新，恢复同一场面试及评价/下一题。
4. Session COMPLETED → 本场统计 → Replay Coach → 完整报告 → 一条 PENDING MemoryProposal。
5. 浏览器点击批准并确认对话框 → 刷新仍 APPROVED → 成长记录可读取内容；报告生成时没有自动批准。
6. 报告详情/历史路径、首页、共享七项导航与 active state 均检查；截图已视觉核对。
7. 五个主页面空态使用**浏览器-only DEMO response fixture**独立检验，不宣称这些响应是数据库真实空历史。
8. 错误 fixture 注入 `UNSUPPORTED_SKILL_STATE`，中文 alert 成功、真实 error code 留在技术详情；中文 404 验证成功。

真实 DEMO 库只读复核：Session=COMPLETED、6 Answer/6 Evaluation/6 Question、无 PENDING/ASKED 泄漏、回答评价关联错误=0；统计综合及各维度均为 4/5。
Report=1，MemoryProposal=1 且 APPROVED；MemoryItem=2（一条 seed 的明确偏好、一条浏览器批准记录）。所有 Agent step provider 均为 replay。

截图在 Git 忽略的 `output/playwright/`：`f3-home.png`、`f3-report.png`、`f3-growth.png`、空态与错误截图；不提交任何登录 token/浏览器 session。

复现入口（不使用正式库）：先按现有 Alembic 将专用 DEMO 库升级到 0024，显式 DATABASE_URL 指向该库；在 `apps/api` 用 `uv run --frozen --no-sync python ../web/qa/replay_runtime.py seed` / `api`。
专用 Vue 开发服务代理 8013；正式应用的 Vite 配置和后端配置不改。已有 seed 不重复执行；浏览器账号密码为公开 fixture，不是正式管理员密码。

## 回归与冻结复核

前端原有 7 项测试保留，2 项文案断言更新为新中文显示，业务语义不变；增加 4 项导航/中文/模式/刷新与报告分区检查，共 11 项。
后端全套使用独立 `interview_agent_f3_regression`，migration downgrade 不触碰业务库。
最终测试、build、diff 和数据冻结复核结果在下方追加。

### 最终执行结果（2026-10-03）

- 后端第一次全套独立库运行：201 passed；再次复用该库运行：197 passed / 4 failed。失败在 migration downgrade 的既有 provenance 保护：上轮测试留下了公共 Agent 审计，不能按空库前提降级。该次失败和数据库均保留，没有修改 migration/保护规则或清库。
- 新建全空隔离库 `interview_agent_f3_regression_final` 后完整复跑：**201 passed，124.18 秒**；1 个既有 pytest cache 权限 warning。正式业务库不参与 downgrade 测试。
- 前端 `node --test tests/*.test.mjs`：**11/11 passed**，原 7 项全部保留并新增 4 项 UI 检查。
- `pnpm build`：vue-tsc 与 Vite production build 成功；没有新增依赖。
- Playwright 实际浏览器流程通过：登录 → 简历/画像读取 → 推荐 → 新面试 → 六轮回答/SSE/评价 → 完成 → 报告 → 批准记忆建议 → 成长记录；中途及完成后刷新状态保留。桌面和 390px 移动端报告/面试布局检查完成。
- favicon 请求为 200；五个中文空态、错误提示与技术详情、中文 404、共享导航 active 状态检查通过。
- 正式库只读行数/数据投影对比：旧数据无变化，revision 仍为 0024。DEMO 业务审计确认回答/评价关联与完成统计一致。
- 冻结清单复核：API/Worker、Agent/Workflow/Schema/migrations 没有 F3 增量；`.env` 和 F0/F1 backup 哈希未变。待提交文件与 diff 未发现配置中的实际 API key / 管理员密码。
- `git diff --check` 通过；Git 的本地 ignore 文件权限与 LF/CRLF 提示不属于代码 whitespace 错误。

验收后停止本轮临时 API/Vue 和浏览器会话，保留 DEMO/回归数据库及截图供复现，不删除正式库、F2 attempts 或原备份。

## 已知 UI / 环境限制

- 原材料可能是英文，Evidence 不允许为了中文界面改写；高级技术详情有意保留英文技术标识。
- 首页待处理建议数量仅涵盖既有报告列表 API 当前可读取范围，已经显示范围说明；不新增聚合后端 API。
- 现有类别筛选仍遵循岗位库的结构化值；缺少成熟 taxonomy 的类别不能由 UI 猜测扩展。
- 管理员的公共岗位 / Research 专用页面尚不存在；本轮不新增。管理员复杂审核编辑与真实模型长面试不是本轮浏览器范围。
- Replay 浏览器验收读取已有 fixture 简历/画像，不重复上传或调用真实 Worker/模型。上传/Parser/真实 Profile 链路保留 M3/F2 验收事实。
- 现有开发 JWT 默认值仅作本地限制提示，未轮换；Live Search、PyMilvus dependency warning 均保持冻结。
- pytest cache 权限 warning 是本地环境问题，不降低测试覆盖、不升级依赖。

完成 F3 后停止，不进入 F4。
