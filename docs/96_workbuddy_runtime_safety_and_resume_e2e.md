# 第十七轮：验收环境数据库安全收口与真实用户主流程回归

- 开工交接 HEAD：`c54406e`（工作区仅 `.workbuddy/` 未跟踪）
- 收尾 HEAD：见最终报告
- 真实模型 HTTP：**0**（全部 Replay，验收进程同时阻断出站 HTTP）
- 本轮不新增产品功能、不启用 Coach Harness（`INTERVIEW_COACH_HARNESS=false` 未变）、不新增 Agent、不新增迁移

---

## 1. 第十六轮误连生产库的真实根因

不是"`--database` 没生效"，而是**它只对一个从未被使用的对象生效**。

`apps/api/scripts/serve_offline_acceptance.py` 的 `api` 分支做两件事：

1. `database(name)` 用 `--database` 建了一个 `engine` + `async_sessionmaker`，只给 `seed`/`history`/`state` 用；
2. `serve()` 调 `uvicorn.run("interview_api.main:app")`，由 uvicorn 导入应用。

而 `apps/api/src/interview_api/infrastructure/db/engine.py` 在**模块导入时**就用
`settings.database_url` 建了全局 engine；`settings` 来自 `.env`（`interview_agent`）。
于是：

- 守卫校验的是 `--database` 的字符串（E2E 名字，通过）；
- 服务实际读写的是 `.env` 指向的生产库；
- 两者没有任何代码路径相连，`--database` 对 API 进程完全无效。

`schema.py`/`main.py` 里的服务全部经由 `async_session_factory` → 那个全局 engine，
所以"只发生一次登录查询"是运气，不是保护。

**判定**：这是环境隔离缺陷，不是参数 bug。任何"只校验字符串、不校验最终生效对象"的守卫都不成立。

## 2. 修复后的配置优先级与进程启动保护

新增 `apps/api/src/interview_api/core/runtime_target.py`，把"决定目标 / 写入目标 / 核对目标"三件事放进同一处，
并复用既有的 `interview_api.core.db_targets.classify`（与 pytest 闸门、测试运行器、`delivery.py` 同一套库名规则，
没有第二套命名规则）。

优先级（严格单向）：

| 来源 | 作用 |
|---|---|
| 命令行 `--database` | **唯一**的目标数据库名来源；缺失即拒绝，不回退 |
| `settings.database_url`（`.env`） | 只提供 host/port/账号，**不提供库名** |
| 进程环境 `DATABASE_URL` | 若与 `--database` 不同，被覆盖并打印 `NOTICE: … 命令行 --database 优先` |

启动顺序（关键）：

1. `resolve()`：纯函数，校验库名为 `*_e2e_*`（`db_targets.E2E`），构造隔离组；不建 engine、不连库；
2. `apply()`：把目标写入 `settings` **和** `os.environ`（`DATABASE_URL`/`REDIS_URL`/`CELERY_*`/`MINIO_BUCKET`），
   发生在任何 engine、Redis client、Celery app 被导入之前；
3. 导入 app/worker → `verify_settings()` + `verify_engine(engine)` + `verify_optional_clients()`；
   `verify_engine` 只读 `engine.url`，**不建立连接**；
4. 通过后才 `uvicorn.run` / `worker_main`。

隔离组（API 与 Worker 由同一条命令解析，因此必然一致）：

| 资源 | 生产（`.env`） | 本轮验收 |
|---|---|---|
| PostgreSQL | `interview_agent` | `interview_agent_wb_r17*_e2e_20261113` |
| Redis 缓存/限流 | db 0 | db **9** |
| Celery broker | db 1 | db **10** |
| Celery result | db 2 | db **11** |
| MinIO bucket | `interview-agent` | `interview-agent-r17-e2e` |
| API / Web 端口 | 8000 / 5173 | 8000 / 5173（同端口，不同资源） |

拒绝时的输出（不泄露密码，`render_as_string(hide_password=True)`）：

```
验收目标拒绝：目标被判为生产库；验收服务不得连接生产库（包括只读查询）
  预期：E2E（名字含 _e2e_ 的浏览器验收库）
  实际：interview_agent（判定：PRODUCTION）
  说明：生产库、历史 Live 归档、系统库与回归测试库都不得被验收服务连接，只读查询也不例外
  未建立任何数据库连接，也不会回退到 .env。
```

普通生产入口未改：`scripts/delivery.py` 与 `scripts/start-interview-platform.ps1` 仍是 `.env` 驱动。
本轮实测 `delivery.py config` 与 `delivery.py revision` 正常（`database=interview_agent`、`revision=0025`）。

## 3. 如何验证"拒绝发生在建立连接前"

不做"故意连生产库"的验证，改为三条互不依赖的证据：

1. **纯函数 + 替身**：`tests/test_runtime_target_safety.py` 把
   `sqlalchemy.ext.asyncio.create_async_engine` 与 `asyncpg.connect` 都换成抛异常的函数，
   再跑 `resolve → apply → verify_settings → verify_optional_clients`，全部通过——说明该路径本身不产生 I/O。
2. **子进程真实 CLI**：分别以 `verify`（无 `--database`）、`verify --database interview_agent`、
   `verify --database interview_agent_thinking_long_20261110`、`api --database postgres` 运行真实脚本，
   断言退出码 `3`、stderr 含"验收目标拒绝"、stdout **不含** `ACCEPTANCE_TARGET` 与 `uvicorn` 字样。
3. **运行期复核**：API 启动后日志首行即 `ACCEPTANCE_TARGET {…}`，随后 `pg_stat_activity` 中不存在
   `interview_agent` 的连接；收尾复查生产库 `revision=0025`、`sessions=19`、最新会话 `2026-10-07`、无 `user_job_targets` 表。

专项 20 条（见 §9）覆盖：显式指定、生产/归档/系统/回归/DEMO 库拒绝、CLI 覆盖环境变量并提示、
隔离组写入、引擎不一致检测、密码不外泄、子进程零启动。

## 4. HTTP 级跨账号权限回归恢复结果

第十六轮把该用例降级为"服务层 404 + 路由存在"。

**真实原因**（本轮用脚本复现）：`interview_api.infrastructure.db.engine` 是进程级单例，
asyncpg 连接绑定创建它的事件循环；每个测试各自 `asyncio.run`，上一个循环的池在新循环里写入即
`AttributeError: 'NoneType' object has no attribute 'send'`。实测：不释放 → 报该错；在新循环内
`await engine.dispose()` → 正常。

**修复方式**（测试侧，未改生产逻辑）：`tests/conftest.py` 新增 `app_engine` fixture，
提供 `reset()`（在**当前**循环内释放全局池），测试在进入和离开时各调用一次。
于是可以回到最高保真度：

```python
owner  = 用户 A（真实注册，密码 hash 入库）
intruder = 用户 B
POST /api/v1/auth/login  → 真实 JWT
GET  /api/v1/job-targets/{A的target}/skill-recommendations  (B 的 Bearer)
     → 404 JOB_TARGET_NOT_FOUND，响应不含 A 的公司/岗位/JD 原文
GET  同一 URL (A 的 Bearer) → 200 RECOMMENDED ai_agent_application
GET  无 token → 401
```

`get_db`、`get_current_user` 均未 override，认证、路由、依赖注入与 Service 都是真实的。

## 5. 简历上传、MinIO、Celery、画像生成实际链路

两条互补证据链，都不写伪造画像：

**A. 真实 Worker 链路（验收栈，浏览器触发）**

```
注册页 → /resumes 页面选择合成 .txt
  → POST /api/v1/resumes/upload（真实路由、真实 get_db、真实 MinIO put_object）
  → dispatch_process_resume 经 broker（Redis db 10）投递
  → 验收 Worker（真实 Celery，--pool=solo）消费
      · ReplayResumeExtract 只把上传文本按小标题回显成结构化 JSON（不编造内容）
      · 报告写入 resume_reports，状态 PROCESSING → COMPLETED
      · ReplayProfile 只从**本次调用**的证据目录里挑选别名与原文片段
      · CandidateProfileService.persist 正常校验并落库（producer=REPLAY_FIXTURE）
  → 画像页显示"第 1 版 · Replay 回放"，项目/技能与上传文本一致
```

**B. 任务体级专项（pytest，可重复）** —— `tests/test_resume_upload_e2e.py`：
真实上传路由 + 真实 MinIO + 真实持久化 + 真实任务体 `process_resume_async`（Celery 任务的函数体），
只把模型边界换成 Replay；broker 一跳被替换为记录调用，**这一点在文档里明确标注**，不用它冒充 Worker 验收。

专项断言：MinIO 对象键为 `resumes/<user_id>/<uuid>.txt` 且内容与上传字节一致；
`ResumeReport` 已保存；画像 `producer=REPLAY_FIXTURE`、`version_number=1`；
每条画像事实都关联 `resume_evidence`，且其 `excerpt` 逐字出现在上传文本中；
同一 task 重投与旧 task 重放不产生第二个版本；失败后同一 task 重试最终恰有 1 个版本；
用户 B 读取 A 的简历 404、列表 0 条、画像 `current=null`。

## 6. 新用户从简历到私有 JD 再到面试上下文的操作

真实 Chrome 157 headless（CDP 9222）走完，9 步：

1. `/register` 注册两个新用户（A、B），`/login` 登录 A；
2. `/resumes` 选择合成简历文件 → 页面显示"自动刷新中..."；
3. 轮询 `GET /resumes` 至 `COMPLETED`（`task_id` 为真实 Celery id，`file_size=274`）；
4. `/profile` 显示"我的画像 · 第 1 版 · Replay 回放"，含 `DEMO 订单系统`、`Python`；
5. `/jobs/targets` 粘贴 JD → 保存 → 打开 → "解析岗位要求" → 4 条要求（均带逐字 evidence）；
6. "确认用于面试" → 第 1 版 → 出现"推荐的专业方向：`Java 后端开发 v1.0.0` 第 1 位 · 命中权重 12 · 系统首推"，
   语义行写明"0 次模型调用…不是百分比或模型置信度"，并列出"JD 原文逐字证据"；
7. "用这个方向开始面试" → `/interview`：目标岗位已选、Skill 已选 `Java 后端开发`、
   来源"根据岗位规则自动推荐"；**会话数 0 → 0**（跳转不建会话）；
8. 点"+ 新建" → 会话 `READY`：`skill=java_backend v1.0.0`、`job_target.version=1`、
   `interviewer_selection.source=AUTO_RECOMMENDED`、快照含候选人画像版本；
9. 用户 B 登录：目标列表空、`skill-recommendations` 404、`/resumes/{A}` 404、简历列表 `total=0`、
   画像 `current=null`、会话 0 条；响应不含 A 的公司名或 JD 文本。

## 7. 推荐输入指纹与 JD 版本更新结果

- **同一条 JD 的两个确认版本**（`tests/test_private_jd_recommendation_versions.py`）：
  v1（Kafka/Redis、Java 后端）→ `java_backend`；改要求 + 改标题/职责 + 再确认 = v2（LangGraph/RAG/Agent）
  → `ai_agent_application`；`input_fingerprint` 变化、`target_version=2`、旧 key 读取标记 `stale`；
  v2 的逐字证据全部来自 v2 原文，与 v1 引用无交集。
- **冻结隔离**：v1 建立的会话其 `interview_context_snapshots.context_json` 仍是
  `java_backend v1.0.0` + `job_target.version=1`，重新确认 v2 不改变它；
  用 v1 的 `AUTO_RECOMMENDED` 交接到 v2 会被 `SKILL_RECOMMENDATION_CHANGED` 拒绝；`USER_SELECTED` 仍可用。
- **不制造变化**：同一状态连续两次读取，指纹与首选方向一致。
- **敏感性不足的具体反例（如实记录，不扩大改造）**：只改要求文本、不改标题与职责时，
  首选方向可能不变；此时产品用 `input_fingerprint` 标记"这份 JD 的推荐输入已更新"，
  而不是假装结果变了。规则权重仍是 title/职责/能力组，没有引入语义匹配。
- **前端**：第二个 JD（AI/Agent）在真实页面上给出 `AI / Agent 应用开发`，第一个仍是 `Java 后端开发`；
  手动选 `银行信息科技岗位` 后等待规则加载 5 秒，选择未被覆盖，来源显示"用户手动选择"。

## 8. 本轮使用的数据库与隔离资源

| 用途 | 名称 |
|---|---|
| 专项/回归（业务） | `interview_agent_wb_r17_test_20261113` |
| 全量第一轮（输出丢失，未采用计数） | `interview_agent_wb_r17_full_test_20261113` |
| 全量最终轮（采用） | `interview_agent_wb_r17_full2_test_20261113` |
| 迁移空库（最终轮） | `interview_agent_v21_wb_r17b_mig_20261113` |
| 浏览器 E2E（首次，选择器失败后保留） | `interview_agent_wb_r17_e2e_20261113` |
| 浏览器 E2E（最终） | `interview_agent_wb_r17b_e2e_20261113` |
| MinIO（专项） | `interview-agent-r17-test` |
| MinIO（验收栈） | `interview-agent-r17-e2e` |
| Redis | db 9 / 10 / 11 |

生产库 `interview_agent` 未写入（`revision=0025`、`sessions=19`、最新 `2026-10-07`、无 `user_job_targets`）。
未 DROP 任何库，未删除数据卷；Redis/MinIO 容器在本轮前后均处于 Up 状态，未重启、未清空。

## 9. 测试与浏览器结果

| 项目 | 结果 |
|---|---|
| 启动安全专项 `test_runtime_target_safety.py` | **20 passed / 0 failed** |
| 简历链路专项 `test_resume_upload_e2e.py` | **4 passed / 0 failed** |
| JD 版本专项 `test_private_jd_recommendation_versions.py` | **2 passed / 0 failed** |
| 推荐/候选/HTTP 权限（16 轮两文件 + 本轮改造） | 38 passed / 0 failed |
| 全量业务段 @ `interview_agent_wb_r17_full2_test_20261113` | **866 passed / 0 failed / 1 skipped** |
| 迁移专项 @ `interview_agent_v21_wb_r17b_mig_20261113` | **10 passed / 0 failed / 0 skipped** |
| 前端 `vue-tsc && vite build` | 通过，0 类型错误 |

（16 轮为 840 passed；本轮 +26 = 20 + 4 + 2，计数自洽。）

浏览器：真实 Chrome 157 headless，7 张截图（`.runtime/ui/shots/`，gitignored）
`r17_01_profile_from_upload`、`r17_02_jd_recommendation`、`r17_03_interview_preselected`、
`r17_04_session_created`、`r17_05_other_account_empty`、`r17_06_second_jd_recommendation`、
`r17_07_manual_choice_kept`。

浏览器过程中发现并修复的产品缺陷（见 §10），修复后重跑全流程复验。

## 10. 仍未覆盖的风险与建议

1. **产品缺陷已修但需回归看护**：面试页入口处理器原先直接读
   `listTargets()` 的结果，而该请求是并行发出的；目标列表先返回空时会走"目标不存在"分支，
   一键选用因此丢失预选（第十六轮未暴露，因为当时列表返回更快）。
   已改为 `await privateTargets` + 失败时用 owner-scoped `getTarget()` 兜底。
   建议后续为该入口加一条前端单测或固定等待策略，避免再依赖时序。
2. **真实模型仍未参与**：简历结构化抽取、画像事实选择、JD 抽取、Coach 建议全部是 Replay。
   本轮证明的是链路、权限、持久化与准入，不是抽取质量。
3. **规则对"只改要求文本"的敏感性有限**（§7 已记录反例），是否引入更强匹配属于 Matching 改造，本轮不做。
4. **Celery 一跳在 pytest 中被替换**：真实 broker/worker 由验收栈覆盖，
   但没有做"worker 崩溃后消息重投"的故障注入；目前只覆盖了同 task 重投与旧 task 重放。
5. **Redis/限流未做隔离使用证明**：验收栈使用 db 9/10/11，但未对"限流键不落到生产 db 0"做断言；
   本轮只验证了连接目标（`verify_optional_clients`）。
6. **能力记忆仍无岗位维度**、**报告 `suggested_training` 真实样本仍缺**（沿用第十五轮结论），
   与本轮无关但仍是下一阶段的高优先级问题。
7. `ALEMBIC 0028`（请求记录↔业务审计 join）仍未实施，按约定不自行开展。
