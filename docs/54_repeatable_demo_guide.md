# V2.1 / V2.2：5–8 分钟可重复演示

本指南展示已有业务记录，不重新造一套演示平台。**当前页面使用 Replay；真实 DeepSeek 仅展示历史验收记录，不实时调用。** 正式库仍为 0025，不能把隔离演示当成正式上线。

## 启动（项目根目录，两个终端）

先确认原 PostgreSQL、Redis 已健康，8007/5177 未被其他进程占用。若端口被占用，核对原命令行，不杀端口。不要使用通用 start 脚本代替下列命令：通用脚本包含 migration，Demo 模式还会 seed，不适用于本次“只复用已有数据”。

终端 A：

```powershell
Set-Location E:\work\code\python\interview-agent-platform
$env:REDIS_URL='redis://localhost:6379/15'
uv run --project apps/api --no-sync python apps/api/scripts/v21_acceptance_runtime.py --database interview_agent_v21_v22_browser_final_20261023_a --memory-replay
```

终端 B：

```powershell
Set-Location E:\work\code\python\interview-agent-platform
node scripts/v21_acceptance_web.mjs
```

- 登录：<http://127.0.0.1:5177/login>。
- **隔离 DEMO 账号**：`v21.demo@example.com` / `DEMO-v21-local-only`，不是个人真实账号。
- API health：<http://127.0.0.1:8007/api/v1/health>。
- 绝不加 `--prepare`，不运行测试建库脚本、Live 验收脚本或重新生成报告。
- runtime 仅使用 Replay Provider，并禁止 CompatibleModelProvider.generate；不修改 `.env`，不启动 Worker。命令不创建库、不迁移、不重新插 fixture。
- 缺少既有演示库或版本不是 0026 时停止，按文档定位，不自动初始化。Replay 仍走真实 Vue/API/PostgreSQL，不是静态截图伪装业务。
- 停止时分别在 A/B 按 Ctrl+C，仅结束本次进程；不停止基础设施、不执行 compose down。

## 演示顺序（约 7 分钟）

开场先说：“接下来是固定 Replay 历史和真实业务页面，最后另看已经执行过的 DeepSeek 决策记录。回放能证明状态、证据和事务，不能证明现在模型在实时推理。”

| 时间 | 打开位置 | 展示与讲解 |
| --- | --- | --- |
| 0:00–0:40 | `/interview?session_id=2` | 第一场仅答 Kafka ACK/重试，已有 1 Answer/1 Evaluation；形成 GAP_SUSPECTED，不用总分直接认证能力 |
| 0:40–1:30 | `/interview?session_id=3` | 第二场有 2 答，针对 ACK/事务边界 DEEP_DIVE，补齐唯一键/事务后改善；追问后答对算有辅助，PRACTICING，独立支持 0 |
| 1:30–2:10 | `/interview?session_id=4` | 第三场冻结记忆后提出支付回调宕机题，独立支持变为 1，尚不 VERIFIED |
| 2:10–2:50 | `/interview/reports?session_id=5` | 第四场独立验证成功，已有报告；统计来自保存的 Evaluation，不重新生成报告 |
| 2:50–3:40 | `/memory` → 查看表现变化与依据 | 当前 revision4/VERIFIED；四个版本对应缺口→练习→练习→已验证，原始观察可回跳面试 |
| 3:40–4:20 | `/interview?session_id=6`，展开历史记忆 | 近期 Kafka 已验证，新场转到 Redis 缓存工程；显示 DEMO / Replay，停在 Q1，不提交答案 |
| 4:20–5:30 | 下节的旧 Replay 库历史，或已有历史截图 | 展示相同条件反向表现、冲突、更正和撤销，不现场撤销最终库数据 |
| 5:30–7:00 | `docs/53_v22_live_memory_acceptance.md` | 历史 Live A 选 Redis、B 选 Kafka 崩溃诊断；展示合法组合、Controller ACCEPTED、6请求/35,585Token，不再调用模型 |

所有 `/...` 网页路径前缀为 `http://127.0.0.1:5177`。浏览器刷新后仍读取同一已保存记录；不要点击侧栏删除、发送回答、生成报告或保存更正，才能保持本指南原状态可重复。

Session6 是 2026-10-23 已启动的历史 Q1，时间预算可能已过；**可查看，不承诺还能续答**。本指南不会重置旧时间。要进行新的互动验收，应显式另建隔离 Session，而不是续跑过期场次。

## 冲突、更正：复用旧库，不改最终库

最快的讲法：查看已有历史截图（这些是上次真实浏览器 Replay 截图，不是当前操作）：

- `output/playwright/v22-capability-conflict.png`
- `output/playwright/v22-capability-corrected.png`
- `output/playwright/v22-capability-revoked.png`

要现场核对真实数据库版本，在终端 A Ctrl+C 后运行同一个 runtime，只更换为旧 Replay 库：

```powershell
$env:REDIS_URL='redis://localhost:6379/15'
uv run --project apps/api --no-sync python apps/api/scripts/v21_acceptance_runtime.py --database interview_agent_v21_v22_browser_20261023_a --memory-replay
```

终端 B 不变，刷新 `/memory`。若登录态失效，用相同 DEMO 凭证重新登录。该库当前已撤销，展开历史可见：

| 版本 | 真实历史 |
| --- | --- |
| 5 | VERIFIED |
| 6 | 相同语境反向表现，CONFLICTED，来自该库 Session6 |
| 7 | USER 更正、排除争议观察后恢复 VERIFIED |
| 8 | USER 撤销，UNKNOWN，revoked=true |

这不是“现在仍有开放冲突”，也不能把当前 UNKNOWN 说成退步。旧库的部分人工备注存在历史编码乱码，不把它当作新文案或模型输出；版本、actor 和状态仍可核对。无需改写历史来美化展示。

用户更正的现场操作可以只在最终库打开“更正/撤销”表单，展示排除项、原因、revision 后点击取消。不要为了每次演示再保存一次或撤销一次，撤销是持久墓碑，当前没有恢复按钮。

演示结束可 Ctrl+C 旧库 API，再用首条命令恢复最终库；不重新准备数据。若不想切服务，直接使用标明“历史”的截图和源码解释，同样清楚说明验证边界。

## 历史真实模型案例如何讲

Live 隔离库与 Replay 浏览器库不同，**相同 Session ID 不代表同一条数据**：

- `interview_agent_v21_v22_live_prepare_20261023_a`：A Session4、B Session9。
- A 冻结 Kafka 两次独立支持后降低优先级；Live Main 选 Redis，追问缓存一致性。
- B 冻结 Kafka 冲突；Live Main 选“业务成功但 offset 未提交就崩溃”，继续追问恢复机制。
- HTTP 原始业务结构和实际 messages：ignored `.runtime/acceptance/deepseek_v22_live_20261024_attempt1.json`。只展开 business_structured_output、usage、候选 ID；不要在展示中打开 `.env`、请求头或真实简历。
- 合法 candidate→目标→题源由程序还原，Controller 已提交；不是靠放宽 Evidence 通过。
- 只有首答及下一题，两个下一动作均 FOLLOW_UP；没有验证完整多场 Live/Coach/长期冲突消解，也不是记忆效果的因果对照实验。

## 重复展示前检查

1. 库名、账号、DEMO/Replay 标签正确；原正式库不迁移。
2. Session2/3/4/5 已完成，Session6 是仅用于查看的 Q1。
3. `/memory` 当前 VERIFIED/revision4；更正历史在另一个库，不混用编号。
4. 报告5可直接读取，不点 regenerate。
5. 截图、ignored 验收 JSON 和文档53存在；它们是历史证据，不冒充现在在线生成。

## 本轮核验（2026-10-24）

- 复用原 runtime / Web 脚本真实启动8007/5177，浏览器正常登录并读取 Session2/3/4/6、报告5和能力记忆。展开历史及更正表单后取消，没有提交修改；刷新仍恢复原状态。浏览器 console Errors0 / Warnings0。
- 两个库的真实只读版本核对：最终库能力 revision4/VERIFIED；旧库 revision6冲突、7用户更正、8撤销。旧库更正/撤销**本轮未重新执行浏览器写入**，只核对既有记录和保留截图。
- 定向后端28 passed、0 failed、2 deselected（两个数据库用例未选入，不能计为通过）；前端43 passed、0 failed、0 skipped。没有重跑完整510后端或声称新建一轮完整多场E2E。
- 文档源码链接、24题数量、4个发布PowerShell代码块语法、离线Alembic SQL和git diff whitespace均通过。正式库仍0025/64表，与本轮开始前的已保存指纹比对无变化。
- 新截图：`output/playwright/v22-delivery-memory.png`、`v22-delivery-topic-switch.png`、`v22-delivery-refresh.png`；Playwright快照保存在ignored `.playwright-cli/`，不冒充当前Live输出。
- 未提交新Answer/Evaluation、未重复fixture、未生成新报告、未调用真实模型。原服务保留运行供演示；没有重建数据库/容器、没有正式migration、没有commit/push。

既有完整 Replay 机制测试见51，Live边界见53。后续重启机器后须重新运行本指南的两个启动命令。
