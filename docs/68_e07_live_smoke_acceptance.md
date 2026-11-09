# E07 唯一一次 DeepSeek Live 冒烟

2026-10-29。**正常路径通过；Repair 未自然触发，Live Repair 路径未覆盖。批次已经结束，不追加调用。**

## 范围与执行前检查

- 授权：1次Evaluation逻辑请求、最多2次HTTP（含唯一Repair）、30000 Token、180秒。
- 预检读取AGENTS、docs/66、67与实际structured/provider/Agent/Controller/quote repair源码；确认引用上限3、最多一次修复、已知/未知usage分别处理。
- 所有配置路由仍为 `deepseek-v4-pro-ga-260813`，无fallback。实际Evaluation使用冻结Prompt **2.3.2**、FOLLOWUP_SCORING_V1、thinking disabled、temperature 0、max_tokens 3000；未修改配置、密钥、.env或生产Prompt。
- 原PostgreSQL/Redis在本次开始时已停止。核对完整ID、原镜像及pgdata/redisdata挂载后仅启动原容器。PG正常WAL恢复期间预检曾58 passed/13连接失败；就绪后重跑 **71 passed / 0 failed / 0 skipped，10.16秒**，此时真实模型调用仍为0。
- 新建专用空库 `interview_agent_v232_e07_smoke_20261029_a`；仅复制已有测试库的0026 schema及alembic_version元数据，没有运行migration、修改定义或复制历史业务数据。

## 场景与真实调用边界

原历史失败位置：`interview_agent_v232_calibration_20261028_a` / Session6，保持不变。

本次专用库：user1 / **Session2**，父Question1、父Answer1，当前Question2、当前Answer2。父Main/Answer/Evaluation是明确Replay准备历史，通过正常service/Controller持久化，不冒充真实过去用户经历；**只有当前Evaluation是Live**。

复用原E07实际问题、Answer、Rubric并逐值核对，不人为改变模型输出：

- 当前题：Kafka 消费者更新订单后如何处理 ACK、重复投递及进程崩溃？
- 当前答：我按刚才提示，用订单ID唯一约束并在同一事务写订单及去重记录，提交成功再ACK。提交后ACK前崩溃则重复投递，由唯一约束阻止重复落库；提交前失败回滚重试。
- Rubric：业务提交成功后再提交消费位点；业务唯一键与原子事务防重复副作用；提交前后崩溃及重试恢复，不声称Kafka自动覆盖外部DB事务。
- 父题68字符、父答29字符，均来自合法同用户、同Session、已完成且正式评价有效的记录，无512/1024截断；assistance_status仍 **UNKNOWN**，不宣称可信提示识别或独立验证。

调用顺序：原始Answer持久化 → `MockInterviewService.call_agent(evaluator.adaptive())` → `evaluation_context` → 冻结投影/Hash/预算 → 真实配置Provider → Schema与原文/gap校验 → 原Controller正式提交 → 同键重试与Evaluation复用核对。没有调用Live Main/Coach或启动完整面试。

## 本次真实结果

| 逻辑 / HTTP | 状态 | 输入 | 输出 | 合计Token | HTTP延迟 | Repair |
|---|---|---:|---:|---:|---:|---|
| 1 / 1 | HTTP200、finish_reason=stop | 2746 | 524 | **3270** | **8.462秒** | 0 |

reasoning/cache Token均0。整批墙钟 **9.24秒**，没有超时、fallback或第二批。

首次原始JSON即通过完整Schema及业务校验，实际3条answer_quotes：

1. 用订单ID唯一约束并在同一事务写订单及去重记录，提交成功再ACK
2. 提交后ACK前崩溃则重复投递，由唯一约束阻止重复落库
3. 提交前失败回滚重试

均为当前原答连续原文。五维为 **4/4/4/5/4**；观察 **PARTIALLY_DEMONSTRATED**，原gap_306a6e6197096c0d286296de被解决，同时提出唯一约束冲突处理、事务/隔离细节及其他副作用窗口等新gap。

无Repair，故不存在本轮“修复前后字段保持不变”的Live对照，不能声称真实模型子集修复通过。该受约束路径仅有既有Replay验证；验收包装另做了明确标注的MockTransport自检，确认新修复不会被误计为第二次逻辑请求，**这不计作真实HTTP**。没有通过故意增加quote制造触发条件。

## 提交、Usage与数据一致性

- Live AgentRun5 / StepRun5：SUCCEEDED，Prompt2.3.2，structured_retry=0，usage_complete=true，实际usage3270完整保存。
- 正式当前Evaluation2仅1条，Answer2 COMPLETED；Session state_version **4→5**，最终COMPLETED、active_question_id=null。
- 两道题预算原本就为2（Replay父题＋当前子题）。Controller独立确认正常结束条件，未因评价建议FOLLOW_UP而突破预算；这不是长面试完成。
- 新Session共2 Question / 2 Answer / 2正式Evaluation，其中 **1条父Replay＋1条当前Live**，不是两次真实评价。
- ModelAdmission仅1条，SETTLED：reserved13562 → actual=charged **3270**。批次发送前另以实际HTTP字节＋输出上限＋余量保守预留14734，未接近30000上限。
- 会话实际累计 **3270/200000**，reserved_model_tokens=0、model_call_reservations={}、unreported_model_tokens=0、无处理中lease。逐HTTP、StepRun、Evaluation与Session计数一致。
- 同键重试返回相同已提交events；Evaluation复用返回相同AgentRun，不产生新HTTP、新Answer或新Evaluation。Snapshot完整内容和指纹不变。
- 本次没有出现“HTTP成功但随后校验失败”，所以不能将本轮称为该异常Live路径验收；已知Usage异常传递仍以前述71项离线/PG回归为依据。

保护库只读核对前后完全一致（业务表内容摘要，不包括WAL/系统表/序列）：

| 数据库 | 版本 / 表数 / 行数 | 前后SHA256 |
|---|---|---|
| 正式interview_agent | 0025 / 64 / 1699 | cf4faece3c47468fb8cd9aa05427477f619450042a7f4516788f5e44fc40fcaa |
| 原V232验收库 | 0026 / 66 / 231 | c5a3432fa87fd46ac560145460b8501ad8027ae67959dcad66c8cd63c1ae651a |

原E07 Session6仍IN_PROGRESS/v4/active8，Answer8仍FAILED，无当前正式Evaluation，历史保守计数 **19806** 未调整。

## 工程结论与保留限制

没有新增阻断故障。当前具备**另行授权开展完整有界长面试验收**的工程前置条件；不等于证明能稳定完成15–20轮、Coach和跨场次Memory，也不保证200000 Token必然支持20轮。下次仍须独立预算和正常结束条件，本次不继续执行。

保留docs/67中的E03状态语义差异、共同参考答案技术混淆、UNKNOWN提示限制；本次不是评分准确性校准或人工Gold审核，也未修改这些输出/规则。Repair Live未覆盖不是本次失败，也不通过反复重跑刷出Repair样例。

## 产物与命令

本轮仅新增：

- `apps/api/scripts/accept_v232_e07_smoke.py`：复用现有Replay准备、LiveBatch计量、真实service/Controller/Provider；独立识别quote修复，使用原批次文件锁，拒绝覆盖或续跑已付费尝试。
- 本文。

本地ignored证据：`.runtime/acceptance/v232_e07_preparation_20261029_a.json`、`v232_e07_live_20261029_a.json`，含实际messages、原始业务JSON、usage/延迟、冻结输入指纹与保护库摘要，不含Key/Authorization/隐藏reasoning。

实际命令：Docker只读核实与原容器start；新空隔离库及schema-only复制；上述71项pytest；无网络MockTransport验收包装自检；脚本prepare一次、live **仅一次**；最终只读SQL/hash、AST及git check-ignore。没有运行浏览器、全仓回归、migration、模型探测请求或第二收费批次，没有commit/push。
