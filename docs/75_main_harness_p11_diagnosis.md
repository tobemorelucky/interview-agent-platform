# P1.1 Main Harness 定向诊断与验收

## 原批次证据

- B／ON 的历史模型消息已使用原冻结 Prompt 和原 Action Schema 重建，输入 Hash 与实发记录一致。它收到 Requirement ID `2234`、`2235`、`2236`，可用 `inspect_job_requirement`，严格参数只接受一个正整数 ID。缺少的是便于判断是否值得取数的「要求 → 合法目标 → 重要度」索引。Agent 和大模型目标的摘要优先级都为 4，仅靠数字无法看出轨迹评测、动态工具执行或失败恢复的具体侧重。ON 从已知通用 Skill、答题状态和候选目录出发，选择合法的 Agent 状态管理题。该路径有足够依据，无法从结构化产物反推模型未调用工具的内在理由。读取冻结正文会提供初始消息没有的岗位细节。
- 原 C／OFF 的实发消息 Hash 同样已核实。该次使用 `max_tokens=3000`、`response_format={"type":"json_object"}`，魔搭路由不传 `thinking` 或 `reasoning_effort`。HTTP 200、`finish_reason=length`，completion 和 reasoning 都为 3,000，记录无可解析的正文。历史记录未保存正文长度，无法区分空串、null 和非 JSON 片段。Provider 在结构解析前产生 `model_output_truncated`，保留了 9,671 Token 的已知用量。证据指向输出预算被推理用尽，未见 Schema 或 JSON 模式拒绝。没有保存隐藏推理文本。
- 模型页描述了模型层能力，但魔搭托管 API 示例没有承诺支持低推理强度或关闭思考的参数。没有将自部署接口参数当作托管端契约。C 复测只在隔离 Main Provider 实例把两臂输出上限设为 6,000。B 两臂均为 3,000。两组均保持原结构化输出模式和推理参数行为。[魔搭托管模型页](https://modelscope.cn/models/deepseek-ai/DeepSeek-V4.1-Flash-1017)

## 最小改动与离线验证

- `adaptive_prompts.py`、`context.py`、`context_projection.py` 为新 ON Session 冻结 Prompt `2.4.0-harness.2`。新增 `context_availability`，只传与合法候选关联的要求 ID、目标、原有重要度，以及活跃 gap 数和可用详情入口。JD 正文、gap 明细、详细 Skill 仍需按需读取；合法候选、P0、Controller 和工具权限均沿用。Prompt 保留信息充分时直接 FINAL 的路径。
- `core_service.py` 让普通 Main 预留跟随实际 Provider 输出上限，仍计入两个可能输出。Harness 路径和 ModelGovernance 已读取实例上限。生产默认配置、密钥和路由未改。隔离批次另在每次发送前按实际消息体保守预留，并检查 10 HTTP、90,000 Token 和 600 秒限额。
- `test_main_harness.py` 覆盖新增指针、工具反馈改变合法选择，以及 HTTP 200 但 3,000 Token 全用于推理时拒绝、单次发送和完整结算。隔离 PostgreSQL 0026 定向测试 **60 通过、0 失败、0 跳过**，含 P0 和 OFF／ON 20 轮 Replay、非法工具、空反馈及重试边界。A 的直接 FINAL 用 Replay 回归，本批未新增 A 的 Live 请求。只读使用原 Session 1083 重建 `2.4.0-harness.1` 旧投影，未注入新字段，13 个合法候选仍可用。

## 唯一 Live 复测批次

在隔离库新建 B、C 两组 Session。组内对齐 JD、Skill、历史、回答、已校验 Replay Evaluation 和合法候选，只有最后一步 Main 使用 Live。B 先 ON 后 OFF；C 先验证 OFF 再发 ON。四次均为 HTTP 200、`finish_reason=stop`，无 Repair 或重试。

| 场景 | Session | 输出上限 | 输入／输出／总 Token | HTTP 耗时 | 业务结果 |
|---|---:|---:|---:|---:|---|
| B ON | 1180 | 3,000 | 5,234／1,306／6,540 | 8,539 ms | FINAL，接受 |
| B OFF | 1181 | 3,000 | 5,026／1,042／6,068 | 7,316 ms | NEXT，接受 |
| C OFF | 1185 | 6,000 | 6,711／1,109／7,820 | 8,690 ms | FOLLOW_UP，接受 |
| C ON | 1187 | 6,000 | 6,399／485／6,884 | 4,680 ms | TOOL_ACTION 被严格 Schema 拒绝 |

- **B** 两臂均选 `C_b6a706cd2b27cfc8a47e927b`（Python，NEXT，`fact:3017`），问订单缓存一致性及失效／并发。ON 已收到两个 CORE 指针，分别指向 Agent 和大模型；仍可依据真实简历直接做覆盖切换。提示更明确，本批未观察到 JD 工具调用或岗位针对性收益。ON 比 OFF 多 472 Token、慢 1,223 ms。两臂各一次 HTTP。
- **C OFF** 返回可解析 JSON，Controller 接受 `C_007d403753ee444b7beaf7b9`，以 FOLLOW_UP 聚焦 `gap_e966b66c767a0f23b3615679`，询问近似重复 chunk 占位。响应有 764 个正文字符，1,109 个输出 Token 中推理为 764。它在新设置下成功，这次实际用量低于旧上限，不能据此将成功归因于提高上限。
- **C ON** 的原始可解析内容选了 `read_active_knowledge_gaps`，参数为 `{}`，但额外带 `_raw`，其中重复了一份序列化的工具动作。用归档 JSON 重跑验证得到 `tool.read_active_knowledge_gaps._raw: extra_forbidden`。本批无法判断额外字段由模型本身还是托管输出处理添加。未执行工具、没有反馈、未发出 FINAL。系统保留第二答及已校验的 Replay Evaluation，未提交第二个正式 Evaluation 或下一题，Session 1187 保留 state_version 5。

合计 **4 HTTP、23,370 输入、3,942 输出、27,312 Token、30.121 秒**。四条账本均为 SETTLED，实际用量等于扣减，Session 预留已释放。发送审计和配对输入保存在忽略跟踪的 `.runtime/acceptance/p11_harness_live_pair_v1.json` 和 `.runtime/acceptance/p11_harness_live_pair_prepared_v1.json`。

**保持 Harness OFF 为默认。** 本批观测到真实模型主动选对 gap 工具的意图，但没有通过严格动作校验，也没有真实工具反馈后的有效决策。B 未出现岗位收益，当前证据不足以默认开启。保留 ON 供明确选择与后续定向验收。本批已停止，无正式库写入、迁移、生产模型配置变更或 Git 提交。
