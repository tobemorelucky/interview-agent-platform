# ModelScope 真实模型采集批次

批次 `p1_real_capture_20261109_a`。同一批次完成 **39 次真实 HTTP，320,693 Token，约 13 分 35 秒**。按 2 魔粒／次估算 **78 魔粒**，未读取账户实际扣减。120 次计划上限剩 **81 次**，1,500,000 Token 上限剩 **1,179,307**。各阶段已取得有区分度的样本，两场长程尝试遇到可归因的业务或输出阻塞，保留现场并封存批次。没有充值、切换模型或启动第二批。

| 类别 | HTTP | 结果 |
|---|---:|---|
| A 工具闭环 | 4 | 两个真实工具闭环均提交 |
| B OFF／ON | 17 | 6 组对照，12 个决策中 11 个提交 |
| C 长程 | 9 | RAG 场提交 2 个正式 Evaluation，Infra 场提交 0 个。均未完成 15～20 答 |
| D 其他 Agent | 7 | Profile、两岗位 Matching、Research、一个短场 Coach 成功 |
| E 评分边界 | 2 | RRF 机制错误、工具授权回答不完整，均完成真实 Evaluation |

**39 个请求 Fixture，28 个独立业务操作，23 个接受、5 个拒绝，0 个 Repair。** 32 次 HTTP 属于接受的业务操作。另有 2 个有效中间响应位于失败链路中，即 #8 的工具动作和 #27 已校验但未正式提交的 Evaluation。失败链路最后一条响应共 5 个，不能按成功提交计数。39 条项目配额记录均为 SETTLED，实际与扣减合计一致，无发送状态未知或配额拒绝。

| Agent 路由 | HTTP | 输入 | 输出 | 合计 |
|---|---:|---:|---:|---:|
| InterviewMain | 26 | 164,822 | 22,980 | 187,802 |
| AnswerEvaluation | 6 | 18,829 | 22,708 | 41,537 |
| SummaryCoach | 2 | 16,986 | 7,884 | 24,870 |
| Resume/Profile | 1 | 1,229 | 693 | 1,922 |
| JobMatching | 2 | 50,984 | 9,404 | 60,388 |
| Research Extract | 1 | 894 | 689 | 1,583 |
| Research Quality | 1 | 1,035 | 1,556 | 2,591 |
| **总计** | **39** | **254,779** | **65,914** | **320,693** |

## 旧响应兼容与采集边界

P1.1 的归档显示，`_raw` 位于供应商最终内容的 JSON 内，早于项目 Provider 解析、Action Envelope 和 Controller。现有数据无法进一步归因于供应商侧模型生成或托管后处理。只在 `main_harness.py` 对工具动作兼容一种回显形状。外层必须恰好是三个声明字段与 `_raw`，内层必须无重复键、能解析，且内容指纹与完整外层动作一致。去掉已证明冗余的回显后仍走原严格校验，并记录动作指纹。其余未知字段、不同参数与 FINAL 包装仍拒绝。

历史实发输入 Hash 核对通过，原失败 JSON 通过新契约，副本保留完整原字段。采集前定向回归 11 项通过。补充按原回显形状走真实 Service 的同键恢复后，聚焦回归 5 项通过，两组重叠。本批真实工具动作没有再次携带 `_raw`，兼容分支的直接证据是历史响应 Replay。

模型使用原 ModelScope `deepseek-ai/DeepSeek-V4.1-Flash-1017`。Main 和长程用 Evaluation 采用隔离实例的 6,000 输出 Token 上限，生产配置未改。两场 15 答目标的新隔离会话冻结 340,000 Token 测试预算，保留 Controller 的自然结束、时间、轮数和版本限制。整批每次发送前检查 120 HTTP、150 万 Token、90 分钟及分类上限，继续走 ModelGovernance。隔离库为 `interview_agent_v232_reliability_20261029_a`，Alembic 0026。正式库、历史会话及历史归档没有写入或覆盖。

## 工具收益与配对

真实执行 **7 次工具**，其中 3 次读取开放 gap、4 次读取冻结 JD。**5 个决策**完成工具反馈到合法 FINAL，另一个 JD 工具链在最终题目校验失败。A 的两条链都读回初始投影未提供的 gap 明细，然后选择仍开放的「相似 chunk 重复占位」范围。两臂的冻结合法目录和准备数据逐组比对，组间按来源 URL 区分。JD 来源为公开摘要，面经为报道主题，试验题、回答和历史准备是派生数据，均不是人类面试逐字记录。

| B 场景 | OFF／ON HTTP | OFF／ON Token | 观察 |
|---|---:|---:|---|
| 信息充分 | 1／1 | 6,978／7,399 | ON 无工具，RAG 深挖。OFF 转 Python |
| 岗位要求 | 1／2 | 6,089／13,010 | ON 查冻结 JD，题目措辞未落在选定目标的技术主题 |
| 历史 gap | 1／3 | 8,526／22,559 | ON 读 JD 与 gap，继续复测仍开放的缺口。OFF 转 Agent |
| 工程故障 | 1／2 | 6,416／12,960 | ON 读 JD，两臂最终仍选 Python |
| 深挖 | 1／1 | 6,906／6,944 | 两臂均 RAG 深挖，ON 无工具 |
| 工具安全 | 1／2 | 6,244／13,293 | ON 读 JD，两臂最终仍选 Python |

gap 场景有直接的证据链。JD 工具并未稳定改善最终选择，两个配对付出了额外请求但没有改变最终知识目标。当前样本支持定向使用 ON，尚不足以默认开启。

## 长程、Coach 与失败样本

- RAG 长场 Session **1235** 正式提交两答。第三答的 Evaluation 输出通过模型和业务评价检查，但未与下一题一起提交。Infra Session **1239** 已生成开场题，首答 Evaluation 截断，正式评价为 0。两场均保持 IN_PROGRESS，未更改状态或重发失败请求。
- 工具权限短场 **1243** 的开场为 Replay，Evaluation 与 Coach 为真实请求。Report **74** 正式完成，Agent 能力为 PRACTICING，下一场 **1244** 的冻结 MemoryGuidedPlan 引用真实已提交 `evaluation:1012`。完成了短场的评价、报告与记忆传递，下一场 Main 未发 Live。
- Profile 合成简历通过。明确排除无关公司的两岗位 Matching 成功（Run **31**）。Research 对 J01 公开摘要完成抽取与质检，结果为 WAITING_REVIEW。没有使用实时网页抓取。宽范围匹配失败后，定向匹配使用新的合法筛选，不重发同一请求。

| HTTP | 准确失败原因 |
|---:|---|
| 9 | `ADAPTIVE_TOPIC_MISMATCH`。选择 system_design，实际发问是 Agent 外部下单工具的幂等与恢复，措辞没有命中所选主题。前一条 JD 工具结果真实有效 |
| 28 | `INTERVIEW_TOPIC_COOLDOWN`。选择 Agent 并问工具恢复，题首“检索这块先放一放”使确定性分类判为正在冷却的 rag_retrieval |
| 30 | `model_output_truncated`。宽范围 Matching 输入 42,694 Token，输出达到 6,000，上游 finish_reason=length |
| 35 | `model_output_truncated`。Infra 首答 Evaluation 达到 6,000 输出，其中推理 4,766，finish_reason=length |
| 36 | `COACH_COMPUTED_NUMBER`。Coach 定性规则把“BM25 分值”误识别为数字评分，拒绝发布 Report 73 |

定向匹配的第一次本地预检还发现岗位类别过滤保留了类别未知的候选。该步骤没有发送 HTTP，改用显式排除其他公司后取得两岗位样本。以上问题均留档，未修改相应产品校验。

## 本地数据与明日 Replay

目录 `.runtime/acceptance/p1_real_capture_20261109_a/` 已封存。每个响应的完整消息、规范化 SHA256、原始最终文本、服务商请求 ID、Token、耗时及状态逐请求原子写入。随后补充实际 StepRun、工具结果和 Controller 状态。未保存密钥、鉴权头、Cookie 或隐藏推理文本。

- `requests/0001.json` 至 `0039.json` 是独立请求档案，`captures.jsonl` 为合并数据，`manifest.json` 和 `index.json` 提供汇总及索引。
- `pairs/` 保留配对输入，`outcomes/` 区分已提交结果和中间输出，`replay_check.json` 保留校验。
- 首条新记录在采集中独立离线验证。全部 **39 个响应**按完整指纹重复命中，修改输入均拒绝。**7 个工具结果**按动作和 Snapshot 指纹重放。两条截断响应复现 Provider 错误，#9 与 #28 复现确定性主题拒绝条件，#36 用原规则确认技术名词误匹配。
- 明日可以直接使用 #1–#4 的工具闭环、#5–#21 的配对、#22–#28 与 #34–#35 的真实长程失败、#29–#33 与 #36–#39 的跨角色输出。用本地 `run_batch.py verify` 读取和验证，不发模型请求、不计入额度。
