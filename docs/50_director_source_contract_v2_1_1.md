# V2.1.1：Director 目标—题源契约修复

首次记录：2026-10-22；非收费及有界Live更新：2026-10-23。状态：**PostgreSQL/浏览器Replay及6请求真实DeepSeek Director验收通过；完整多轮Live面试与Coach闭环尚未验收。**

> 第1–9节保留2026-10-22的修复及故障历史，其中“阻塞/未执行”描述当时状态。数据库恢复及非收费结果见第10节；用户随后单独授权的6请求真实模型批次见第11节，不把Replay或部分Live成功扩大为完整面试验收。

本轮真实模型调用为 **0**。保留 V2.1 原失败批次、所有既有未提交修改；不改 .env、模型路由、密钥、历史迁移或正式业务数据，不 commit/push。

## 1. 可核实的根因与证据缺口

原始批次仍在 ignored `.runtime/acceptance/deepseek_v21_live_20261022_attempt1.json`：独立库 `interview_agent_v21_live_20261022_a`，DEMO user18、Session33；3逻辑/3HTTP、16,239Token，Main/Evaluation/Main 依次返回200，第三次业务校验 `UNKNOWN_DIRECTOR_SOURCE`，随后停止。

### 已确认的契约缺陷

- `adaptive.AdaptiveDecision` 原先要求模型分别填写 `target_knowledge_id` 与 `question_source_ref`；JSON Schema 不能表达二者之间的合法配对。
- `planning_payload()` 提供每目标来源，以及全局 `allowed_refs`。全局引用可作决策支持，**不能**充当任意目标的题源许可。
- `validate_decision()` 按所选目标的 `sources` 精确比较 ref；找不到就拒绝。这里没有 trim、别名转换、主题相似匹配或自动替换来源。
- 蓝图创建来自固定 Skill、JD、事实、已审核题源。`build_blueprint()` 对每个实际 Skill 目标都提供通用 Skill 来源，正常生成路径并非“目标没有任何来源”。
- 原流程在模型前使用冻结 Snapshot，在 Controller 短事务内再次使用同一 Snapshot 重算合法空间；没有查询“最新 JD”替换它的代码。是否有人修改过 Session33 的数据，必须待数据库恢复后核实，不能只凭源码排除。

因此，本次异常**确切表示所选目标与返回题源未能匹配**；设计层面的缺陷是把组合一致性留给模型自由填写。不能将其简单归结为“LLM 不稳定”，也没有证据说明是服务端 ID 规范化误伤。

### 无法还原的原始内容

当时仅成功 artifact 保存完整结构化结果；失败 Main 没保存原响应，也没保存拒绝的三个选择字段。批次 JSON 仅保留错误码、用量和开场题。故目前无法还原实际 strategy/target/source 三元组，不能声称模型具体填了哪个 ID。

冻结 Blueprint 全部真实数字题源 ID、实际 Evaluation 诊断、当时完整候选列表也需读取隔离库。不能用当前临时测试 ID 冒充 Session33 的真实值。

本轮检查了 `docs/49` 和当前真实源码；仓库未找到单独命名的 V2 源码审计文件，不把不存在的文件当作证据。

## 2. 修复后的契约

新建自适应 Session 固定 `decision_contract=TARGET_SOURCE_PAIR_V1`，Main Prompt 为2.1.1，Evaluation Prompt仍2.1.0。Blueprint、知识账本和内部决策仍兼容2.1.0结构，无数据库迁移。

```text
冻结 Blueprint + 当前合法策略/知识状态
  → 程序生成合法组合 C_<24位摘要>
  → Main 选择 candidate_id + strategy + supporting_refs + 问题措辞
  → 程序精确还原 target/source/provenance
  → 原题源、主题、Evidence、难度校验
  → Controller 短事务重新计算并校验相同组合
  → 原事务提交 Evaluation/知识观察/下一题
```

组合 ID 由 Blueprint 指纹、知识点 ID、该题源完整固定内容计算。不同目标共享真实来源时产生不同组合，不将所有来源并入所有目标。顺序调整不改变同一组合 ID；Snapshot/版本变化会改变 ID。

模型-facing `AdaptiveSelection` 不再包含 target/source 数据库 ID，问题 draft 也不接受 provenance 字段。程序填入的是模型**明确选中的合法组合**，不是发现模型错引后静默换一个来源。

- NEXT 的 PUBLIC_QUESTION / JOB_REQUIREMENT / CANDIDATE_PROFILE 精确还原原引用及真实 Evidence keys。
- FOLLOW_UP / REQUEST_EXAMPLE 沿用当前目标并使用 FOLLOW_UP 题型；DEEP_DIVE 使用 DYNAMIC_GENERATED，保留组合来源在决策审计中。
- NEXT 仍要求明确涉及所选技术主题；现有 `validate_question()`、候选事实/证据关联、难度、冷却和追问限制保留。程序并不宣称能完备证明自然语言语义正确性。
- 如果目标来源为空，仅当它是真正的固定 Skill 主题，才明确提供 `GENERIC_SKILL_NOT_COMPANY_EVIDENCE` 通用题；否则没有组合就不提供该目标。非法或伪造来源直接拒绝，不将其包装成通用题。
- `REPORTED_TOPIC` 仍只是方向，不是逐字原题或公司必考偏好。
- 新契约校验 Blueprint 指纹及 Skill 版本；拒绝不存在/过期 candidate_id、跨组合替换、伪造来源、非法策略及错误题型。

旧 Session 没有新 contract 标记时，继续使用原冻结 Prompt/Schema，未自动升级 Session33 或改历史 Snapshot。旧 V1/V1.2 不启用新分支。

## 3. 调用次数、失败审计与恢复

仍是每普通轮一次 Evaluation + 一次 Main，确定性终局不再调用 Main；**没有额外规划请求或业务校验修复调用**。原 JSON/Schema constrained repair 最多一次、沿用既有预算。新业务错误走 SAFE_RETRY，不无限循环。

Main 输出在验证后转换为内部 ResolvedAdaptiveDecision，审计保存规范化后的候选 ID、真实目标/来源和原策略。复用成功审计时按正确内部 Schema 读取，再验证当前合法空间；不把内部 artifact 当成模型-facing Selection 去解析。

失败审计增加有限的原选择字段、允许组合 ID、目标来源映射、错误和 `SAFE_RETRY_NO_EXTRA_CALL` 处置；不保存失败问题正文、简历、原始响应或隐藏推理。现有特定错误诊断与新增信息合并，不覆盖原选择。

Controller 原 state_version、active_question、Answer 幂等键、lease、预算和短事务逻辑不改。本轮只让它继续通过共享 `validate_decision()` 重算并验证组合。

源码中 Evaluation 成功、Main 失败时：Answer 留存为 FAILED/题目ANSWERED，清 processing，不提交正式 Evaluation/下一题/知识状态；成功 Evaluation 的审计可供同一真实输入重试复用。**这是源码与此前测试的行为，本次数据库尚未复核，不能当作 Session33 的实测状态。** 原键重试还需满足本场实际时间/Token预算；不擅自重置旧场预算。

## 4. Replay 重现记录

`test_v211_director_contract.py::test_v21_failure_class_replay_uses_known_input_not_an_invented_live_output` 使用批次脚本中已知第一答：

> 消费者收到 Kafka 消息后处理订单，然后 ACK，我没有考虑数据库提交失败和重试。

错误输出是**明确人工构造**的 Redis 目标 + 已存在但只属于 Kafka 的 requirement:1，不是丢失的 DeepSeek 原响应。旧 validator 复现 `UNKNOWN_DIRECTOR_SOURCE`；新契约的合法组合通过。同文件测试假 candidate、跨目标替换、共享来源、generic fallback、精确 public/fact provenance 和四策略。

外部 Replay Provider 适配新模型 Schema，只从提供的组合选择，不替换业务校验器。泛化回答产生 REQUEST_EXAMPLE，ACK 缺口产生 DEEP_DIVE；已充分展示的目标被排除。它证明契约行为，不证明真实模型策略质量。

## 5. Docker 与 Session33 核对状态

实际检查：当前 Docker context为desktop-linux；daemon named pipe缺失。启动已有 Docker Desktop 后弹出用户截图中的 Inference Manager 错误，涉及本机 `Docker/run/dockerInference` 监听初始化。

没有运行 prune、down、删除/重建容器卷、恢复出厂设置、unregister WSL 或删除 socket；没有更改 Docker 配置。报错是 Desktop 自身启动失败，**不是 PostgreSQL 数据损坏的证据**。用户要求谨慎后停止进一步启动尝试。

因此以下仍阻塞：Session33 的 Answer/Evaluation/Question 行数、active_question_id/state_version、lease释放、AgentRun/StepRun状态、实际usage与用户配额账本、原幂等键服务重试。HTTP批次的 reserved=0不代表数据库lease或配额已核实。

本轮不另建替代数据库来冒充读取了原隔离库，也不把正式库重建为测试库。

## 6. 本轮实际文件

- `apps/api/src/interview_api/modules/interview/adaptive.py`：组合、Selection/Draft Schema、resolver、完整性检查。
- 同目录 `adaptive_prompts.py`：独立2.1.1 Main Prompt，保留旧正文。
- 同目录 `context.py`：仅新 Session 固定新契约/Prompt。
- 同目录 `agents.py`：按契约选择 Schema、模型输入投影、解析、复用、有限失败诊断。
- 同目录 `core_service.py`：首题/回答/skip传入已有 payload 选择本场 Schema。
- `apps/api/tests/v21_support.py`、`test_v21_adaptive.py`：显式模型 Replay 边界适配；原测试断言保留。
- `apps/api/tests/test_v211_director_contract.py`：新增契约测试。
- 本文及 `docs/README.md` 索引。

未修改 Controller 的原事务/预算规则、Skill Engine、公共资料库、Profile、Memory 或模型配置；没有新增表、API路径和 migration。

## 7. 实测结果与未执行项

以下是本轮重新运行，不引用474/41作为本轮完整回归结论：

- 首轮定向 RED：缺少 Selection/候选组合；实现后 GREEN。指纹及新 Prompt 测试先失败，再通过。
- 新契约专项：18 passed，全部离线/Replay，无收费调用。
- 前端：`node --test apps/web/tests/*.test.mjs`，41/41通过；含单元和静态契约，不称作41条浏览器测试。
- 生产构建：`pnpm --dir apps/web build`，vue-tsc及Vite通过。
- 全量后端最后完整运行：**294 passed、199 skipped，6.62秒**。共493项（原474项+新增19项）；其中新增18项离线通过、1项数据库重试/审计测试跳过。199个跳过项主要依赖PostgreSQL/记录快照，不计为通过，没有删去旧覆盖或将跳过项改为通过。
- 数据库集成、真实浏览器+API Replay、Session33恢复验证：**阻塞，未完成**。没有以静态DEMO页面代替E2E。
- Python AST语法（5个领域文件）、`git diff --check`通过；历史migration无diff，0018 SHA256保持 `3f4b951021427fbc3b629609897d3fa7cd732f400e00d8192a9223a90abdcd47`。.env、.runtime验收记录、Playwright截图仍被ignore。

兼容性结论：不触达旧工具/Skill/共享JD领域逻辑，离线覆盖通过；数据库兼容性仍需复跑，不能声称完整不回归。

## 8. 后续最小真实验收提案（尚未授权/未执行）

先安全恢复Docker，再读取原隔离库、跑全部数据库回归、独立DB浏览器Replay后才申请执行：

1. 原Kafka场景相同输入，创建新的2.1.1固定配置隔离Session：开场Main、第一答Evaluation、后续Main，共3逻辑。
2. 另一类“泛化回答/具体例子”场景：同样3逻辑，比较实际策略和组合合法性。

合计 **6逻辑，含既有repair最多12HTTP，建议上限60,000Token/8分钟**；若完成两场各两轮，再增加两个终局Evaluation，总8逻辑。实际保守请求预估超预算时停止，不将未知用量计零。失败不刷成功、不切模型。

不覆盖原attempt文件，不篡改Session33的旧Prompt或预算，不复用旧费用授权。真实模型效果、增减Token和实际延迟仍需新批次测量；本轮只能确认没有增加模型调用次数。

## 9. 最终验收恢复：只读排障记录（2026-10-22）

本次恢复轮没有再次启动 Docker，没有停止 WSL/Docker 服务、改系统配置、删除/移动文件、重建容器或执行 migration。只更新此记录。真实模型调用0。

### 实际检查

| 检查 | 结果 | 能说明什么 |
| --- | --- | --- |
| Docker CLI / context | CLI存在；desktop-linux，端点dockerDesktopLinuxEngine | 不是缺少CLI；未修改context |
| Engine | named pipe缺失；docker version无有效Engine响应，已取消本次挂起的只读CLI | Engine尚不可用，不能检查容器清单 |
| Desktop | 安装版本4.73.1.226574；Desktop/backend进程仍在 | 有进程不等于Engine健康 |
| 服务 | WslService Running；com.docker.service Stopped/Manual | 不能单凭辅助服务Stopped断言根因，未擅自启动服务 |
| WSL | wsl --status可返回；唯一列出的docker-desktop为Stopped、WSL2 | WSL可响应；没有证据证明发行版损坏 |
| Desktop日志 | backend cancelling with error: initializing Inference manager ... remove dockerInference: The file cannot be accessed by the system | 启动失败位于Desktop Inference初始化，而非已启动PG的SQL错误 |
| 文件类型 | dockerInference与userAnalyticsOtlpHttp.sock均为reparse tag 0x80000023；fsutil查询前者报1920 | Unix socket运行端点异常，与公开同类故障吻合；不能据此诊断整个NTFS或数据库损坏 |
| 项目连接 | 只打印非敏感host/port/db：localhost:5432/interview_agent；2秒TCP探测超时 | 当前应用无法连接PG；未输出密码 |

日志位置：`C:\Users\he\AppData\Local\Docker\log\host\com.docker.backend.exe.log`。实际错误时间为本地15:51:43/15:52:15；此前截图同一错误。没有上传diagnostics。

读取Docker设置中允许的路径字段，发现 `CustomWslDistroDir=E:\work\application\Docker\DockerDesktopWSL`。实际文件：

- `E:\work\application\Docker\DockerDesktopWSL\disk\docker_data.vhdx`，20,757,610,496 bytes，最后修改2026-10-22 14:48:15。
- `E:\work\application\Docker\DockerDesktopWSL\main\ext4.vhdx`，109,051,904 bytes。
- C盘默认位置另有较旧的1,692,401,664 bytes VHDX，不能拿它代替实际E盘数据盘。
- 检查时C盘可用57,859,096,576 bytes、E盘可用153,061,814,272 bytes，无磁盘耗尽证据。存在/大小不等于逻辑数据完整性证明，未挂载、改写或hash扫描VHDX。

### 执行命令类别

`git status --short`、`Get-Command docker,wsl`、`docker context ls`、`docker version`（挂起后仅取消本次CLI）、有界 `wsl --status` / `wsl --list --verbose`、`Get-Process`、`Get-Service`、`Get-PSDrive`、日志关键词读取、目录/VHDX元数据读取、`fsutil reparsepoint query`、只读Win32 FindFirstFile标签查询、非敏感数据库配置投影与2秒socket连接。读取了项目Compose及现有启动脚本，**未运行启动脚本**：它会执行compose up和migration，当前排障不适合盲目执行。

没有执行prune、down、Factory Reset、WSL shutdown/unregister或全局服务停止。没有修改.env、Docker settings或任何正式数据。

### 来源与判断边界

Docker官方仓库的[同类错误记录](https://github.com/docker/desktop-feedback/issues/554)描述了无法清理遗留Unix socket而导致Inference初始化失败的情况；这是相符的公开故障类别，不是保证某种修复在本机有效。不能直接断言MFT损坏，也不能宣称升级或删单个socket必然解决。

[Docker备份文档](https://docs.docker.com/desktop/settings-and-maintenance/backup-and-restore/)要求在Desktop完全停止后备份实际配置位置的虚拟磁盘；本机应考虑E盘实际文件，而非C盘旧默认文件。未擅自执行备份或重装。

### 停止点与待授权恢复方案

下一步可能涉及退出Docker Desktop及隔离本机运行目录，可能影响其他Docker工作负载，所以按照用户要求停下。建议申请一个单独有界操作：

1. 正常退出故障Desktop（不是恢复出厂设置），确认不再有backend/活动Engine；不全局关闭WSL。
2. 完全停止后，将E盘两块现有VHDX及Docker设置备份到新命名的独立目录；先确认空间与备份成功，不覆盖原文件。
3. 仅将 `C:\Users\he\AppData\Local\Docker\run` 改名隔离并保留原目录，不删除其端点、不移动整个Docker数据目录、不碰VHDX或WSL注册。
4. 单次正常启动Desktop，检查Engine；如出现另一个初始化错误则停止，不自动扩大隔离范围、重装或清理。
5. Engine恢复后先检查原容器及卷挂载，再决定是否只启动已存在的项目PG/Redis，之后只读核对Session33并执行隔离验收。

**上述步骤只是建议，尚未执行。** 需用户明确批准这种项目外运行目录操作后继续。

Session33、原容器和逻辑数据完整性仍未核实；本次没有重跑数据库/浏览器或拿前次294/199、41结果冒充恢复通过。目前不具备最终Live验收条件，6逻辑请求提案仍未授权执行。

## 10. 最终非收费集成验收（2026-10-23）

### 10.1 安全边界及备份

Docker Desktop 已在单独授权操作中从4.73.1升级至4.96.1。本轮检查时原PostgreSQL和Redis均处于停止状态；分别确认原容器ID、镜像与卷后，仅启动这两个既有依赖，未运行Compose、重建卷或清理Docker。PostgreSQL 16.14正常完成自身崩溃恢复，使用已有数据库目录，不运行初始化或人为WAL修复。

在测试写入之前，先查询实际数据库清单，选择正式`interview_agent`及16个需要保留的acceptance/browser/live隔离库，包括原V2.1真实失败库和V1.4.2真实来源库。使用原容器、原账户执行`pg_dump -Fc`，不输出凭证。

- 新备份目录：`E:\work\backups\interview-agent-platform\v211-20261023-144352-e617d0`。
- 共17份archive，总计11,636,672 bytes；每份通过容器内`pg_restore --list`，清单`manifest.json`保存数据库名、文件、SHA256、大小和校验结果。
- 首份验证出现本地stdin管道提前关闭；保留已生成文件，改用`docker cp`到容器唯一`/tmp/v211-verify-*.dump`后执行`pg_restore --list`，成功后才继续测试。没有执行restore，也没有在正式库恢复任何内容。
- 这些是选定数据库的逻辑备份，不是整个集群、角色/全局对象或VHDX备份；本轮没有执行实际恢复演练。`pg_restore --list`证明archive目录可解析，不等价于完整恢复验证。
- 正式库64表测试前后逐表比较`count + 排序行内容MD5摘要`，全部相同；Session33、其Snapshot及Answer摘要也完全相同，正式Evaluation仍为0。摘要仅用于本地前后变更检测，不作为密码学防篡改或全盘完整性证明。

### 10.2 Session33保护核对

原隔离库`interview_agent_v21_live_20261022_a`中的Session33未参与测试写入：

| 检查 | 实际值 |
| --- | --- |
| owner / ProfileVersion | DEMO user18 / version18，不是正式用户38的version1 |
| 状态 / state_version / active_question | IN_PROGRESS / 2 / 29 |
| Answer | 1条，id24，FAILED / UNKNOWN_DIRECTOR_SOURCE |
| 正式Evaluation / Question | 0 / 1；原题ANSWERED，没有下一题 |
| lease / 预留 / 运行中AgentRun | answer_processing=null，模型预留清空，无运行中AgentRun |
| 冻结Prompt | Main及Evaluation均2.1.0，无新pair contract标记 |
| 用户18模型账本 | 3条SETTLED，总计16,239 Token；与原HTTP记录一致，无RESERVED |

Run93开场成功，Run94评价结构化成功，Run95 Main业务失败。Step95的SUCCEEDED只说明模型/结构化调用成功，不能替代Run95的业务校验结果；没有正式Evaluation提交与该事务边界一致。库内另有3条系统测试所有者UNKNOWN、保守计费422 Token，不属于用户18，没有擅自退款或更正。

时间已超出原7200秒预算，不能直接续跑来验收新契约。没有重置时间、升级Prompt/Snapshot、改Answer、删除失败审计或触发原键重试。失败Main的原始目标/题源三元组仍未保存，恢复数据库不等于补回丢失的模型响应。

### 10.3 本次数据库回归（全部重新运行）

隔离库：

- 定向：`interview_agent_v21_v211_targeted_20261023_a`。
- 全量：`interview_agent_v21_v211_full_20261023_a`。
- 浏览器：`interview_agent_v21_v211_browser_20261023_a`。
- 真实来源回放只读：`interview_agent_v14_acceptance_live_20261021_v142_attempt1`。

使用`uv run --project apps/api --no-sync python scripts/run-v21-tests.py`。脚本仅接受`interview_agent_v21_`新库前缀、拒绝覆盖既有库；测试数据库中的migration兼容测试允许upgrade/downgrade，但正式库完全不迁移。Redis依赖恢复后，完整回归的测试进程显式使用逻辑库15；不修改`.env`。浏览器API最终也以该逻辑库运行。

| 检查 | passed | failed | skipped | 说明 |
| --- | ---: | ---: | ---: | --- |
| 初始Director/自适应/预算定向 | 38 | 0 | 0 | 含先前因数据库不可用跳过的恢复测试 |
| 全量第一批 | 134 | 0 | 0 | 84.77s，含migration往返与旧模块 |
| 第二批首次 | 356 | 3 | 0 | Redis未启动，原记录保留，见下文 |
| Redis恢复后第二批 | 359 | 0 | 0 | 108.70s |
| 增补旧/新Prompt数据库兼容后Director专项 | 20 | 0 | 0 | 3.85s |
| 最终第二批（含新增用例） | 360 | 0 | 0 | 101.51s |
| **最终全量，分两批覆盖全部文件** | **494** | **0** | **0** | 134+360，未将重复定向执行相加 |

原493项增加1项历史契约真实数据库兼容测试，所以是494而不是降低旧覆盖。没有剩余skip；保留真实Snapshot的只读回归也实际运行。单个测试子进程上限175秒，没有收费模型或公网Search/Fetch调用。

首次3个环境失败分别是：

- `test_m5_memory.py::test_proposal_api_ownership_and_decisions`：旧显式Memory入口需要Redis锁。
- `test_v13_shared_governance.py::test_real_auth_shared_read_private_boundaries_and_429`：限流依赖不可用返回503。
- `test_v14_web_collection.py::test_real_api_admin_review_two_users_shared_and_private_denied`：RATE_LIMIT_UNAVAILABLE。

修复是启动核实后的既有Redis依赖并重新完整执行第二批，没有关闭限流、改评分、减断言或把异常当成功。

关键已执行覆盖：

- `test_pair_rejection_preserves_answer_and_reuses_evaluation_on_original_key`：合法Evaluation审计成功后Main返回假candidate，保留FAILED Answer/ANSWERED题、无正式Evaluation/下一题；原键重试只调用一次AdaptiveEvaluation，之后重复请求返回同一事件，无重复用户消息或问题。这里验证调用次数/复用机制，不声称测量了真实服务商费用。
- `test_same_answer_concurrent_retry_optimistic_conflict`：并发回答和state_version围栏。
- `test_frozen_legacy_and_pair_sessions_execute_without_upgrading_history`（本轮新增）：仅在新测试Session的创建fixture边界还原旧冻结契约，旧2.1.0和新2.1.1分别实际完成两轮，Main StepRun版本正确，Snapshot不变；不改Session33或业务validator。
- 既有V1/V1.2工具执行、并发工具去重、Skill动态注册、V1.3用户配额/所有权、V1.4审核共享/私人数据隔离、报告与Memory审批测试全部包含在494项内。

### 10.4 真实浏览器 + API + Replay

实际使用缓存的Playwright CLI启动headed浏览器，正常登录隔离DEMO账号；真实Vue页面访问真实FastAPI和PostgreSQL，没有`route.fulfill`、静态DEMO页面或伪造HTTP响应。模型边界使用现有`AdaptiveReplayProvider`固定响应模板、固定策略分支和确定性动态ID绑定；这是明确构造的Replay，不是丢失的DeepSeek原响应，更不是模型语义质量评测。runtime显式禁止`CompatibleModelProvider.generate`。

创建时通过页面选择岗位、Skill、轮次、风格、自适应选项，使用当前Prompt2.1.1和pair contract。两条场景JD均明确DEMO，在独立库内生成，不冒充真实雇主资料。首次fixture同时包含Kafka与数据库词汇，开场因优先级选中了数据库事务；保留Session2，不改其Snapshot，另建聚焦的DEMO JD version2，再新建Session3/4验收，没有改生产选题代码来迎合测试。

| Session（浏览器隔离库） | 实际过程 | 最终数据 |
| --- | --- | --- |
| 3：Java/Kafka | 消息可靠性NEXT开场 → 缺ACK提交边界的回答 → DEEP_DIVE → 补充幂等、唯一约束、事务提交后ACK、重试 → 原gap解决 → NEXT缓存主题 → 第三答 → COMPLETE/Report | 3 Answer /3 Evaluation /3 Question，state_version7 |
| 4：AI Agent | Agent目标NEXT开场 → 泛化回答 → REQUEST_EXAMPLE（Controller呈现FOLLOW_UP） → 补充白名单、Schema、权限、幂等和恢复 → 原gap解决 → NEXT Python → 第三答 → COMPLETE/Report | 3/3/3，state_version7 |
| 5：异常恢复 | Replay单次model_timeout → 刷新恢复原回答/原键重试 → 第二题 → 第三答在固定3秒模型延迟中刷新取消 → 原回答/原键重试 → COMPLETE/Report | 3/3/3，state_version9；保留1条超时、1条取消失败审计 |

Session3/4每场各消除1个真实持久化gap，第二条observation引用正确Answer/Evaluation及真实回答跨度，解决目标不再被NEXT选择。Session5为专用异常测试输入，未解决知识缺口，报告仍正确显示本场证据不足，不能当成学习进步案例。

三场均完整生成current successful Replay报告并核对；Session3/4报告及面试中刷新恢复通过。最终均无active_question、无processing、无未完成题。只读脚本复算9个candidate ID和目标/题源关系，核对每条知识观察的Answer/Evaluation归属和`content[start:end] == excerpt`；报告均值从实际Evaluation重算一致。这里候选选择的来源是明确的通用Skill来源，不冒充公司真实面经；公开题、JD要求、CandidateFact精确题源的拒绝/还原由专项回归覆盖。

每场3次Main +3次成功Evaluation；第三答预算终局不再请求Main。Session5额外保留2次已失败的Evaluation尝试，不产生重复有效评价。没有第七个Agent、独立Planner请求或额外规划环节。

浏览器脚本也保留了时序定位事实：一次刷新后过早读取尚未加载的input；一次等待了错误loading文案；一次刷新早于Answer提交，只得到未提交状态。这些不被宣称为“取消恢复通过”。最终在固定3秒Replay延迟内等待700ms后刷新，数据库实际留下`interview_request_cancelled`且原键重试成功，才判定该项通过。为重置一次性延迟fixture，仅重启了本轮启动且已核对命令行归属的隔离Replay API，未重启正式服务。

### 10.5 结果文件、前端及改动范围

- 数据核验：`.runtime/v211_acceptance_20261023/browser-db-results.json`。
- 日志：同目录`api.stderr.log`、`api2.stderr.log`、`web.stdout.log`等；旧失败日志保留。
- 截图：`output/playwright/v211-A-gap.png`、`v211-A-gap-resolved-next.png`、`v211-A-report.png`、`v211-B-request-example.png`、`v211-B-report.png`、`v211-timeout-safe-retry.png`、`v211-disconnect-recovered.png`、`v211-recovery-report.png`。
- Playwright真实网络/动作trace：`.playwright-cli/traces/trace-1792738528902.*`。仅隔离DEMO账号，未导出真实用户凭证。
- 最终页面浏览器console：Errors0、Warnings0；不把测试脚本自身失败的断言隐去。
- 前端`node --test apps/web/tests/*.test.mjs`：41 passed /0 failed /0 skipped；这些是单元及静态契约测试，不冒充41条浏览器E2E。
- `pnpm --dir apps/web build`：vue-tsc和Vite通过。Python compile及`git diff --check`通过；LF/CRLF提示不是whitespace失败。
- 历史migration没有diff；0018 SHA256仍为`3f4b951021427fbc3b629609897d3fa7cd732f400e00d8192a9223a90abdcd47`。新隔离库head0025，正式库head仍0025且内容摘要不变。

**本轮没有修改任何生产业务代码或新增migration/API。** 仓库实际新增修改仅为`apps/api/tests/test_v211_director_contract.py`补充数据库兼容用例，以及本文/文档索引；既有大量tracked/untracked修改全部保留。备份在仓库外；`.env`、`.runtime`、截图和Playwright输出均被ignore；无commit/push。隔离准备及只读核验脚本也保存在ignored runtime目录，不作为新业务功能。

本轮隔离Replay地址为`http://127.0.0.1:5177`，API为`http://127.0.0.1:8007`。DEMO账号仍为`v21.demo@example.com` / `DEMO-v21-local-only`，不是正式账号。重新启动可使用现有脚本（不要再次执行`--prepare`）：

```powershell
$env:REDIS_URL='redis://localhost:6379/15' # 仅当前验收进程
uv run --project apps/api --no-sync python apps/api/scripts/v21_acceptance_runtime.py --database interview_agent_v21_v211_browser_20261023_a
node scripts/v21_acceptance_web.mjs
```

验收结束已核对命令行归属并关闭本轮隔离API（PID383120）、Web（PID362836）及Playwright专用浏览器。原PostgreSQL和Redis保持healthy；未停止其他服务、清理数据库或删除验收记录。上面的地址需按命令重新启动隔离服务后访问。

### 10.6 剩余边界与下一次Live提案

非收费数据库/API/浏览器链路已达到下一次真实验收前置条件。真实DeepSeek是否会选择恰当策略、是否出现新的语义问题和实际延迟，仍未验证；程序可证明引用/组合/事务合法，不能完备证明自然语言技术质量。Replay的0Token和低延迟不能作为真实模型性能指标。

旧Session33仍保留失败状态且时间预算已过，不复用、不修成成功。V1.4.2未通过的原真实面经发布验收也没有被本轮兼容性回归改写为通过。

下一次最小Live建议两种场景各3逻辑：新场Main开场 → 固定第一答Evaluation → 后续Main，共**6逻辑，既有repair最多12HTTP，建议总上限60,000Token/8分钟**。这只覆盖原失败节点及不同策略，不等于完整终场/报告真实模型验收；若两场均需第二答终局，至少8逻辑，若还需各生成一次Coach则10逻辑，应另行明确授权。本轮没有启动任何新Live请求、Search或Fetch，没有自动继承历史剩余额度。

## 11. 最终有界DeepSeek Live Director验收（2026-10-23）

### 11.1 授权、隔离与执行方式

第10节完成后，用户在对话中另行明确授权：最多6逻辑、12HTTP、60,000Token、480秒。本节只记录这一唯一真实批次，不覆盖2026-10-22的失败attempt，也不使用旧批次剩余额度。

- 新库`interview_agent_v21_v211_live_20261023_a`：通过PostgreSQL `CREATE DATABASE ... TEMPLATE interview_agent_v21_v211_browser_20261023_a`复制已验证、无活动连接的纯DEMO隔离fixture库。没有复制正式用户数据、执行migration、创建容器或卷。复制前检查目标不存在，复制后head0025、模型账本0条。
- 使用DEMO用户1和既有测试岗位2/3，各创建一个新Session，id6/7；不是正式用户38，也不是历史失败Session33。已有DEMO简历/画像和岗位输入沿用fixture，只有本轮Main/Evaluation的输出来自Live模型。
- 复核七个正式route均为`openai_compatible / deepseek-v4-pro-ga-260813 / thinking disabled`，拒绝不符配置及fallback；未修改`.env`或路由。Main/Evaluation当前timeout为180秒，保持现有值；外层批次仍有480秒硬上限，没有把timeout调大。
- 旧`accept_deepseek_v21.py`的12逻辑/三轮流程不符合本轮授权，因此只在ignored `.runtime/v211_live_20261023/run.py`增加独立的验收执行器，复用原MeteredProvider、HTTP计量、治理账本及业务service。没有修改生产业务代码、Prompt、validator或Schema。
- 执行前5项离线边界检查实际通过：逻辑6、HTTP12、Token60,000、截止480秒及repair限制。整批额外收紧为最多1次repair；本次实际为0。
- 与原批次共用排他文件锁，不允许并行batch；新attempt已存在则拒绝再执行。A提交后留一个最多90秒的本地语义核对检查点，核对通过才继续B，该等待也计入480秒。不增加额外模型探针或Planner调用。

### 11.2 实际用量与耗时

原始记录：`.runtime/acceptance/deepseek_v211_live_20261023_attempt1.json`，执行日志：`.runtime/v211_live_20261023/batch.stdout.log`。

**6逻辑 /6HTTP /33,410Token /77.086秒 /0repair /0fallback。** 六次均HTTP200、finish_reason=stop、reasoning_tokens=0。输入30,830、输出2,580；缓存命中4,352为输入的子集，不重复相加。77.086秒包含A/B间语义核对及数据库操作，不等于六次模型耗时之和。

| 请求 | 场景/角色 | 输入Token | 输出Token | 总Token | HTTP耗时 |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 | A 开场Main | 4,230 | 190 | 4,420 | 4,748ms |
| 2 | A 首答Evaluation | 4,943 | 808 | 5,751 | 12,635ms |
| 3 | A 后续Main | 5,944 | 237 | 6,181 | 4,695ms |
| 4 | B 开场Main | 4,370 | 229 | 4,599 | 4,480ms |
| 5 | B 首答Evaluation | 5,026 | 887 | 5,913 | 13,446ms |
| 6 | B 后续Main | 6,317 | 229 | 6,546 | 4,327ms |

没有超时、JSON/Schema修复、业务校验失败、余额/权限或明确计费异常。未查询服务商账单，不据此承诺实际费用为零；仅确认实际请求与Token在授权范围内。没有继续消耗未用预算。

### 11.3 A：Java/Kafka，Session6

用户固定回答原样使用：

> Kafka 消费失败可以重试，成功以后手动 ACK，保证消息不丢失。

真实开场：

> 在 Kafka 消费场景中，如果要保证消息被可靠消费，你会从哪些关键配置和消费流程入手来避免消息丢失或重复处理？

开场Main选择`C_22f8657ba1228e0e906c6427` → `java_backend:kafka_reliability` + `requirement:4`，Controller以JOB_REQUIREMENT提交原题11。

Evaluation将该回答记为本场`INSUFFICIENT_EVIDENCE`：确实覆盖了重试和手动ACK，仍需澄清offset提交时机、先处理业务再确认、幂等和rebalance等机制，留下4个open gap。五维为2/1/1/3/2，只是本次模型评分，不作为永久能力认证或确定性正确性证明。

后续Main自主选择：

- strategy=`FOLLOW_UP`；不是执行器写死的动作。
- candidate_id=`C_96fe47b097f5fafbeb684ea7`。
- target=`java_backend:kafka_reliability`。
- source=`skill:java_backend@1.0.0`，明确为通用Skill，不冒充公司面经；理由引用真实answer10、evaluation_run27、question11。
- Controller通过当前候选、版本、引用、预算等校验，提交问题12、kind/source=FOLLOW_UP。

真实追问：

> 你提到成功以后手动 ACK，那具体是在什么时机提交 offset？如果业务处理成功但提交 offset 失败，或者提交成功前进程崩溃，会发生什么？

语义核对：直接引用候选人刚才的手动ACK说法，聚焦确认边界及失败窗口，关联刚保存的缺口；没有切到Redis等无关主题，没有假设候选人已实现未提供的事务/幂等方案。A通过后才放行B，未追加模型“评判”该追问。

### 11.4 B：AI Agent/Tool Calling，Session7

用户固定回答：

> Agent 根据模型决定调用哪个工具，执行完以后把结果给模型，就可以继续完成任务。

真实开场：

> 在 Agent 应用里，当模型决定调用一个工具时，从模型输出到工具真正执行，中间通常需要经过哪些关键步骤？

开场Main选择`C_9c1b6d8a5bdd6c08c9872f56` → `ai_agent_application:agent` + `requirement:5`，以JOB_REQUIREMENT提交问题13。

Evaluation观察为`INSUFFICIENT_EVIDENCE`，承认回答中的高层执行/回传流程，诊断工具意图解析、名称及参数Schema校验、程序执行边界、结果消息组织和异常处理尚未说明，留下5个open gap。五维2/1/1/2/2。此次并未穷尽权限/状态管理的所有设计细节。

后续Main实际也选择`FOLLOW_UP`，没有为展示差异而强迫REQUEST_EXAMPLE或DEEP_DIVE：

- candidate_id=`C_263f755555063ff5a2449bb4`。
- target=`ai_agent_application:agent`。
- source=`skill:ai_agent_application@1.0.0`。
- 理由引用answer11、evaluation_run30、question13和该Skill。
- Controller提交问题14，kind/source=FOLLOW_UP。

真实追问：

> 你刚才提到模型决定调用工具后执行并返回结果。那具体来说，程序拿到模型输出后，如何识别这是一个工具调用，并校验工具名称和参数是否合法？

语义核对：紧接回答中“模型决定→执行”的笼统跳跃，要求解释程序解析和参数验证，而不是重复介绍项目或随机切题。题目与Agent目标、该Skill及Evaluation缺口一致，没有虚构实际执行的工具结果。

### 11.5 提交、引用和费用账本核对

两场均固定Main Prompt2.1.1、`TARGET_SOURCE_PAIR_V1`；Evaluation沿用设计中独立冻结的2.1.0，内部知识/决策版本2.1.0不等于Main仍在使用旧Prompt。六条StepRun实际记录的model均为目标DeepSeek、structured_retry均0，六个业务AgentRun均SUCCEEDED。

| Session | 状态 | state_version | Active Question | Answer/Evaluation/Question |
| --- | --- | ---: | ---: | --- |
| 6 | IN_PROGRESS | 3 | 12 | 1 /1 /2 |
| 7 | IN_PROGRESS | 3 | 14 | 1 /1 /2 |

每场原题COMPLETED、后续题ASKED，这是按授权停在下一题、不是状态泄漏或错误COMPLETE。answer_processing为空，模型预留为0；无重复Answer业务键、无重复有效Evaluation。每次首答再次用原幂等键调用service返回完全相同事件，逻辑请求计数没有增加。

程序在真实调用流程中由`resolve_selection`精确找到本次合法candidate，再经Agent validator及Controller短事务重新验证目标/来源/策略和版本，没有扩大全局题源许可、替换来源或执行错误回退。原始HTTP结果保留允许的结构化业务字段，数据库保存解析后的真实目标及来源。原题的JOB_REQUIREMENT和追问题的通用Skill语义明确分开。

两条知识观察均精确关联各自Answer及正式Evaluation。独立只读SQL重新核对`message.content[start:end] == excerpt`均为true，4/5个缺口真实持久化。本轮未补答，故不能声称已掌握或缺口解决。

同一隔离DEMO用户累计6条配额记录，全部SETTLED，`actual_tokens == charged_tokens`，总计33,410，与HTTP usage完全一致，无RESERVED/UNKNOWN。两场审计输出中的quota为同一用户的累计账本，不能将A/B两个累计视图再次相加。业务校验成功不免除Token结算；本次无失败/未知usage，因此保守结算异常分支只由之前非收费回归覆盖。

正式库64表与Session33/其Snapshot/失败Answer的批次前后内容摘要完全一致。没有修改旧会话预算、正式数据、原模型路由或配置；没有执行migration、Search/Fetch、Report/Coach、浏览器Replay或新功能开发。临时验收执行器及检查点保存在ignored runtime目录，未commit/push。

### 11.6 与旧失败的对比及停止边界

V2.1.0曾在第3次HTTP200后出现`UNKNOWN_DIRECTOR_SOURCE`，正式Evaluation/下一题未提交。本次A相同故障位置（首答后Main）以及B另一专业目标均真实通过候选ID解析、来源约束和Controller提交，**没有再次出现该错误**。这验证了新组合契约可以在真实DeepSeek输出下工作，不只是Replay的自洽结果。

不夸大的限制：本次固定输入与旧批次并非逐字相同，旧失败原始三元组仍缺失，不能宣称精确重放了丢失的模型响应；两场均选择FOLLOW_UP，没有Live验证所有策略、多轮难度/主题切换、补齐缺口后的知识收敛或终局Coach。Kafka开场及Evaluation仍覆盖较宽的机制列表，评分解释也偏严格，不能用两例证明评分标定或面试真实性的普遍改进。

**结论：已具备进一步申请完整多轮Live验收的条件，但尚未完成两场完整Live面试或报告/Memory闭环。** 唯一批次已停止，禁止用剩余额度继续补答；后续请求数、Token和时间必须由用户另行授权，不自行推进V2.2。
