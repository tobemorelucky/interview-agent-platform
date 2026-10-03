# F4 工程交付

日期：2026-10-03。目标是可启动、可停止、可检查、可复现的本地交付，
不是新业务、Agent 或生产安全认证。F4 后停止，不进入 F5。

## 冻结与改动范围

M1–M6、F2/F3 的 Agent、Workflow、Domain schema、Prompt、Router 业务逻辑、
Controller 与前端业务页面冻结。没有新增/修改 migration（head 0024）。

F4 允许的增量仅是 `core/config.py` 的 JWT 启动保护、对应安全测试，
根模板/Compose 本地凭据参数、安全启停与检查脚本、Git ignore、交付文档。
不改模型、Prompt、Evidence validator、统计或业务 policy，不升级依赖。

Git 有大量此前已验收工作，全部分类保留：应用源码、0017–0024 migrations、
tests、docs、明确 fixture/sample。没有 reset/clean/squash、删除未知文件或 push。
最终 checkpoint 在所有验收完成、secret scan 与 diff/status 复核后创建；
提交说明 `feat: complete multi-agent career interview platform`，本地 tag `v0.9.0-finalization`。
实际 commit hash/tag/worktree 状态以交付最终输出为准，文档不预先宣称提交成功。

## Secret / sample 审计

- `.env`、`.runtime`、dump、log、PID、截图和私人上传目录被忽略。
- 模板只含 placeholder/公开本地初始化配置，没有真实 API key / JWT / 用户密码。
- `.env` 未改动或轮换：当前 development 的 JWT 默认值允许；生产不能用它启动。
- F0/F1 原 dump 保留，冻结清单对 `.env` 和 backup SHA-256 比对。
- `scripts/audit-delivery.py` 对候选提交文件、staged/unstaged diff 扫描当前配置真实凭据，
  只报告文件位置/结果，不输出 secret；还核对冻结文件和禁止提交的本地产物。
- PDF metadata 明确 synthetic，DOCX 为工具生成、测试邮箱和 demo 标识，
  无个人作者/批注来源；`sample_docs/README.md` 明确所有 sample 为构造案例。
  原二进制未重写，不把构造电话、人名、公司/成绩视为真实事实或联系方式。

## JWT protection

共享 API/Worker Settings 在 development/test 外 fail closed：
缺失值、旧 development default、明显 placeholder、短/低多样性值被拒绝。
合法强 secret 正常加载；不自动生成随机 secret，不记录 input value。
测试新增 11 项，包含要求的六类环境/密钥场景及 typo 环境拒绝。

这是启动配置保护，不是生产 JWT 密钥管理、HTTPS、轮换、撤销或全栈安全审计。

## 启停实现

| 文件 | 职责 |
|---|---|
| `scripts/platform-common.ps1` | 单一 PID ownership、锁、日志、HTTP/TCP 与 Compose 调用 |
| `scripts/start-interview-platform.ps1` | 依赖/配置检查 → 基础设施 → head 0024 upgrade → API → Worker ping → Web |
| `scripts/stop-interview-platform.ps1` | 仅停止本脚本拥有的进程；默认保留基础设施 |
| `scripts/status-interview-platform.ps1` | ownership + HTTP/TCP + Redis/Celery ping + DB revision + 非敏感配置 |
| `scripts/check-environment.ps1` | Docker/Python/uv/Node/pnpm/依赖/.env/端口，只检查，不安装 |
| `scripts/delivery.py` | 读取共享配置、scope DEMO、migration/revision、API/Worker 入口 |
| `scripts/serve-web.mjs` | 既有 Vue/Vite 的 normal / demo 本地代理启动 |
| `scripts/run-regression.py` | 新独立测试库，拒绝旧库复用，不碰正式库 |

PID 文件含进程 ID、创建时间、可执行文件与命令行；停止前重新核对，
PID reuse/ownership mismatch 拒绝操作。只处理验证父进程的后代，
不杀所有 python/node、不按端口 taskkill。已拥有且健康的服务复用；端口被外部程序占用则明确失败。
同一 checkout/mode 的 start/stop 用本地 mutex 防并发操作。
stdout/stderr 分文件，保存在 `.runtime/platform/logs`；PID 状态在 `.runtime/platform/pids`。
停止后的记录归档保留，没有删除未知文件。

旧三个 BAT 仅调用 PowerShell，危险逻辑已移除。
`-StopInfrastructure` 仅 compose stop，不 down、不删 container/volume；
若另一个 managed mode 仍运行，拒绝停共享基础设施。`-KeepInfrastructure` 明确保留。
失败迁移不启动新的业务进程，不降级、不 drop/create 正式库，不改旧 migration。

普通模式使用既有 `.env` 正式本地库/API 8000/Web 5173。
按实际简历 KB 配置需要时启动 etcd/Milvus；SearXNG 只在显式 `-WithSearch` 时启动。
Demo 不依赖向量/搜索服务；status 的 false 表示可选组件未启动，不是 Demo 失败。

## Replay Demo

复用 F3 QA runtime 和 M6 seed，不建设第二套 Demo 业务系统。
专用库 `interview_agent_m6_f3_ux_demo`、API 8013/Web 5174、Redis DB 13/14、
Worker queue `iap_f4_demo`；Demo 环境清空模型 API keys 并阻断 HTTP 模型调用。
固定 seed 存在时只读取，不重复追加或重置账号。
正式库、F2 attempts、原备份不参与 fixture 写入。

Demo 展示已有简历/画像；不宣称 Demo Worker 为用户上传执行真实模型链路。
普通真实链路的验收属于 M3/F2；当前现场展示是 Replay。
六 Agent 真实 DeepSeek成功、未 fallback 的事实见 25 文档，Live Search 仍冻结。

## 文档

根 README 更新为产品、六角色边界、Evidence、可靠性、Memory、组件图、
可执行 Quick Start、Real/Replay 边界与诚实限制。
`docs/README.md` 索引保留全部历史；旧部署文档顶部明确更正危险命令。
`docs/demo-guide.md` 提供 5–8 分钟展示，使用已有完成报告，不要求现场六轮。

## 验收记录

初次启动到 API/Worker 后，Web 因 Windows PATH 有两个 Node 可执行路径失败。
选择首个实际可执行 Node 后成功，已有 API/Worker 复用，失败记录保留。
首轮回归 214 passed / 1 failed：新静态测试错误地禁止提示文案中的 `volumes`，
而不是禁止实际删除命令；修正这项新增断言，第二个全新库复跑 215 passed（126.52 秒）。
没有更改原 201 项业务测试或 validator 来通过验收。

最终 fresh-start、浏览器、回归、安全与停止结果在下方追加。

### 最终结果

- 完整停止后的应用 PID、API/Web HTTP 与基础设施探测均为停止状态；启动后 API/Worker/Web、Redis ping、Celery ping、DEMO revision 0024 正常。
- 再次 start -Demo：三个原 PID 全部复用、fixture 不重复 seed；普通模式也已启动成功，包含实际需要的 Milvus/etcd，随后安全停止。普通 Worker 启动前确认 celery queue/unacked 均为 0，没有触发真实模型。
- 默认 stop：只停止所拥有的进程树，基础设施保留；应用 8000/8013/5173/5174 无监听。显式完整停止只执行 compose stop，容器和数据 volume 保留。
- 浏览器真实表单登录后逐页读取首页/简历/画像/推荐/面试/报告，七项共享导航保持；重启和刷新仍可读取已保存内容。
- 浏览器创建新的两题 DEMO Replay Session 2，完成两次固定回答/SSE/评价、COMPLETE、生成 Report 2。只读验证 Answer=2、Evaluation=2、错误关联=0、PENDING/ASKED=0、均分=4；Report COMPLETED/current、proposal PENDING、Memory 总数仍为 2、provider 只有 replay。
- 截图 `output/playwright/f4-report.png` 已视觉核对且被忽略。QA 的标题定位和一次只读查询列名错误均已修正；没有修改页面/Schema/validator 来通过验收。
- 最终全量回归：**215 passed，138.07 秒**（原 201 + 11 JWT + 3 安全脚本检查）；frontend **11/11**；production build 成功；Python compileall 与五份 PowerShell parse 检查通过。
- 回归命令关闭本地 pytest cache plugin，避免既有目录权限 warning；未修改系统权限或删除 cache。三个独立 F4 回归 attempt 库保留，不清空失败记录。
- 普通启动前/后正式库只读比对：0024、61 张表，旧行数/数据哈希无变化；无未验证约束或无效索引。
- 最终 secret / artifact audit：未发现当前实际配置凭据进入待提交文件或 diff；`.env`、原 backup 哈希未变；冻结的业务源码/migrations/UI 无漂移。没有真实模型调用或 Live Search。
- 未暂存 diff 的 `git diff --check` 通过，但在全部既有未跟踪文件暂存后，**`git diff --cached --check` 失败（本次 exit 1）**：16 个此前文件的 EOF 多余空行，包含冻结的 `0018` migration。这不是 LF/CRLF warning。此前未跟踪文件不在普通 diff 检查范围，因此直到 checkpoint 暂存时才暴露。
- 不改冻结的 migration/业务文件，不关闭 Git 空白规则以刷成功；**checkpoint commit/tag 暂停，尚未创建**，等待用户决定是否仅允许这 16 文件的 EOF 空行规范化。全部当前工作已安全暂存，未删除；没有 push。

Runtime / 215+11 tests / build / browser / secret 验收通过，但最终 staged whitespace 验收尚未通过，不能宣布完整 F4 成功或创建 finalization successful tag。所有 `.runtime`、backup、数据库、浏览器文件继续留在本地，不进入 Git。

## 剩余工程限制

- Windows native 进程停止是终止拥有的进程树，不保证正在执行模型/任务的优雅排空；建议空闲时停止，任务重试仍遵循既有幂等策略。
- 本地 Compose 无认证 Redis、HTTP 与已公开 development 初始化默认值不是生产配置；外网部署需自行配置 TLS/网络隔离/凭据/备份策略。
- 修改 PostgreSQL 初始化变量不会修改已有 volume 的账号密码；不得以删除 volume 解决不匹配。
- status 是各检查时刻的快照；不要在 start/stop 进行中把中间状态误判为最终验收。Milvus HTTP health 检查针对本地 Compose 的 9091 端口。
- scripts 不安装软件、自动创建管理员、修 Live Search、升级 PyMilvus，或承担云部署。
- 数据库 migration integration tests 需要新空测试库，不要复用已含公共 provenance 审计的旧库执行 downgrade。

## 历史 EOF 的精确豁免（用户授权后续验收）

此前 staged whitespace 失败记录保留，不改写为原检查成功。
用户随后明确要求保留冻结字节、采用精确路径豁免，而非修复这些历史文件。
分类结果：16 项全部为 M1/M2 冻结历史文件，F4 新增/允许修改文件为 0 项，
本轮实际删除 EOF 空行的文件为 **0**；没有运行批量格式化工具。

`scripts/frozen-eof-whitelist.json` 固定以下路径、既有诊断行号、工作文件 SHA-256、
暂存 index blob ID。`scripts/check-whitespace.py` 分两层运行标准 Git 检查：
非白名单文件直接 `git diff --cached --check`（精确 literal 路径排除）；
全量再次检查，只接受白名单完全一致的 `new blank line at EOF.` 诊断。
同一路径的 trailing whitespace、不同诊断/行号、新路径 EOF 均不豁免；
working diff 也继续执行标准检查。任一冻结工作字节或 index blob 改变即失败。
不修改系统/仓库 `core.whitespace`，不全局关闭 `blank-at-eof`。

| 精确豁免路径 | 分类 / 原因 |
| --- | --- |
| `apps/api/alembic/versions/0018_add_public_career_data_domain.py` | 冻结 migration；绝对不改，包括 EOF |
| `apps/api/scripts/backfill_public_career_data.py` | M1 冻结 backfill helper |
| `apps/api/src/interview_api/modules/public_career/__init__.py` | M1 冻结领域文件 |
| `apps/api/src/interview_api/modules/public_career/backfill.py` | M1 冻结领域文件 |
| `apps/api/src/interview_api/modules/public_career/fingerprints.py` | M1 冻结 fingerprint utility |
| `apps/api/src/interview_api/modules/public_career/tool_models.py` | M2A 冻结领域文件 |
| `apps/api/src/interview_api/modules/public_career/tools/__init__.py` | M2A 冻结工具文件 |
| `apps/api/src/interview_api/modules/public_career/tools/contracts.py` | M2A 冻结工具 contract |
| `apps/api/src/interview_api/modules/public_career/tools/errors.py` | M2A 冻结工具错误定义 |
| `apps/api/src/interview_api/modules/public_career/tools/fixtures/search.json` | M2A 冻结 Replay fixture |
| `apps/api/src/interview_api/modules/public_career/tools/gateway.py` | M2A 冻结 Gateway |
| `apps/api/src/interview_api/modules/public_career/tools/providers.py` | M2A 冻结 providers |
| `apps/api/src/interview_api/modules/public_career/tools/replay.py` | M2A 冻结 Replay provider |
| `apps/api/tests/conftest.py` | M1 历史测试基线，不改旧测试 |
| `apps/api/tests/test_00_migration.py` | M1 历史 migration 测试 |
| `apps/api/tests/test_experience_fingerprint_writes.py` | M1 历史 fingerprint 测试 |

运行：`uv run --frozen --no-sync python ../../scripts/check-whitespace.py`（在 apps/api）。
`--self-test` 使用独立临时 Git 仓库，已验证新 EOF、白名单新 trailing whitespace、
Git fatal error 均会拒绝；不触碰主仓库 index 或 Git 配置。
这属于本地冻结 checkpoint 的字节审计，不是跨系统重新 checkout 后的换行格式承诺。

### 0017–0024 冻结字节核对

八份 migration 与 F4 修改前 `.runtime/finalization/f4-freeze.json` 完全一致。

| Revision | SHA-256 |
| --- | --- |
| 0017 | `eb0736a346d763a1bd4d8832459a33edf7a57cdf8390ff5ee937e0de1aa6cc4f` |
| 0018 | `3f4b951021427fbc3b629609897d3fa7cd732f400e00d8192a9223a90abdcd47` |
| 0019 | `37407daf240b6207dd4e43454c02a63e01840c70a4c030428eec395cd673772f` |
| 0020 | `2a68ce2cde02da0da6ee00b1f2d31e71e804e187da85c57c650e2ad4c129e7aa` |
| 0021 | `ffcd22aebf8ca6d33b5fa93d6daf20c2c3cec5680d77561c13f09cb559c393fe` |
| 0022 | `df1f7dc463693acd211b2d9070ca52ce4bd4d251af32abadb645db54024ddca0` |
| 0023 | `4425a307bb42d8ae3fb7e1dfa83e555940b66b33efb59233592663dd1f948514` |
| 0024 | `9ce33fc489834cdc211659c22c4be7e2043d903e9d4c25fb18817bb27a310d48` |

冻结业务源码 / Workflow / UI 哈希无漂移，F4 已批准的 JWT 配置保护除外。
`.env` 和原 PostgreSQL backup 哈希不变；secret/artifact scan 未发现泄露。
正式业务库 current revision 仍为 `0024`。

后续最终重验收结果在下面追加；只有全部通过才建立本地 checkpoint/tag，不 push。

### 用户授权后的最终重验收

- 新独立数据库 `interview_agent_f4_regression_20261003_eof_authorized`：**215 passed，131.45 秒**；原测试与历史 attempt 全部保留。
- frontend **11/11**；`pnpm build`（vue-tsc + Vite production）成功；Python compileall、五份 PowerShell parse 全部通过。
- 原始 `git diff --cached --check` 仍有上述 16 条历史 EOF 诊断，不能声称原始全量命令通过。精确例外检查通过，非白名单标准检查、所有其它 staged 诊断和 working diff 均为零；16 项工作文件 hash/index blob 无漂移。
- `check-whitespace.py --self-test` 通过；未改任何 whitespace 配置。0017–0024 hash 全部匹配 F4 前基线，业务核心未批量格式化。
- 从全部停止状态重新 `start -Demo`，重用已有 fixture 不重复 seed；API/Worker/Web ownership、FastAPI/Vue HTTP、Redis/Celery ping 正常，DEMO revision 0024。Milvus/SearXNG 未启动，不是当前 Replay DEMO 前置条件。
- 独立只读验证正式数据库 revision 为 0024，repository `alembic heads` 唯一输出 `0024 (head)`。
- Playwright 再次表单登录、读取首页、刷新 Report 2，并从共享导航读取简历/画像/推荐/面试/成长记录/报告。七入口和 DEMO 标识保持，控制台 Errors=0/Warnings=0。一次 QA 将所有页面标题限定 h1 的错误定位超时；实际简历页为 h2，修正定位后六页通过，没有修改冻结 UI。
- 已有两题 Replay E2E 再次数据库只读验证：COMPLETED、Answer/Evaluation=2、unfinished/mismatch=0、Report 2 current、Proposal PENDING、Memory=2、provider=replay。本轮不重复生成数据或执行真实模型。
- 默认 stop 仅停止所属 DEMO 应用；显式基础设施停止仅 compose stop。全部 volume/database/原 backup 与旧验收记录保留。

提交前再次运行 secret/artifact/freeze audit 和精确空白检查，全部通过后本地建立：
`feat: complete multi-agent career interview platform` / `v0.9.0-finalization`。
最终 commit hash、tag 指向和 clean worktree 以本轮交付回复的 Git 实测结果为准，避免提交后再改文档造成 dirty。不 push，不进入 F5。
