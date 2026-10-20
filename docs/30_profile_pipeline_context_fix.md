# 限定范围 Profile Context 修复与真实验收

日期：2026-10-05。仅修改 Candidate Profile 的模型输入边界、alias 解析和
发布时的确定性分组使用方式。不修改 Job Matching / Interview / Coach / Memory。
不修改 Schema、migration、ModelRouter、正式 timeout、原始 Evidence 切分或 validator。

## 分组与证据边界

- 保留逐行 atomic ResumeEvidence 的真实 excerpt、key、hash、offset、page 及数据库 ID。
- 新 context.py 以规范化后的精确文本包含关系关联已有 Report 的项目/教育/工作片段。
  Report 只在服务器用于分组，不作为可引用事实，也不进入模型请求。
- 唯一身份锚点优先于描述锚点。相同优先级的歧义匹配保留 general。
- 同组两个精确锚点之间，仅有精确续文或多个项目技术词的片段可以有界关联；
  不把未知的夹杂正文自动升级为项目证据。技术标签本身不能凭空制造项目使用事实。
- 连续章节标题没有可靠向后范围；标题、姓名、联系方式不作为项目/技能使用证据。
- 这不是通用 PDF 布局恢复，未重写 Parser；无法可靠分组的内容仍保留 general。
- 派生 section 只存在于 Profile 的服务端 validation catalog。原数据库 section 不改。
  原 normalize_skill_evidence_states / resolve_and_validate 函数原样使用，最终状态仍只向下收敛。

## Alias / 最小输入

- 按 Resume/offset/key 确定性排序，每次调用创建 E01..En 与真实 key 的一一映射。
- 模型只能返回当前 alias；未知 alias、真实长 key、大小写变体和 group ID 均拒绝。
- 在原 validator 前精确还原 key。CandidateProfileVersion、Fact-Evidence FK 及历史仍保存真实引用。
- 模型输入保留全部 atomic excerpts、有效 section、alias 和分组引用列表。
  不全量传入 Report，不传 risk_points、basic_info 或 legacy-only 字段。
  注意：这不是删除原始 excerpt 中的个人信息；原材料依然作为私有输入。
- 当前 Profile 的已知引用转换为本次 alias；其它历史引用不暴露给模型。
  用户确认事实继续由既有服务端合并逻辑保留。
- Prompt 新版本 1.0.2 只增加调用级 alias/group 约束；1.0.1 与 1.0.0 保留可查询。
  Schema 未改变。版本变化自然进入既有 generation key；同版本重试的幂等算法不变。

## 发布与历史 correction

正式 Profile Agent 将 alias 映射、section_map 和分组依据记录在已有 AgentRun.output_json。
模型不能提供这份 section_map。发布使用数据库中的真实 Evidence 做最终校验。
已有 current pointer 最后切换、短事务和不可变版本规则均不改变。

Correction 从 base version 的 Agent audit 继承当时的分组；对于连续 correction，
沿 immutable base_version 链查找其 Agent 版本。不根据后来修改的 ResumeReport 重分类旧事实。
未采用本次 context 的旧版本没有该审计字段，继续按原始 catalog 兼容。

## Resume 38：只读 correctness / 体积验收

没有将真实简历正文或联系方式复制进测试 fixture 或本文。
实际读取并断言全部 40 条 Evidence 的 excerpt/hash/offset/key 不变，且切片仍对应 raw_text。

| 内容 | 原请求 | 修复后 |
|---|---:|---:|
| serialized HTTP request chars | 14620 | 9707 |
| 完整 Report 的序列化贡献 | 3188 | 0 |
| Evidence ID/alias 值字符数 | 2680 | 120 |
| Evidence excerpt 字符贡献 | 1450 | 1450 |
| Schema 的序列化贡献 | 2921 | 2921 |
| System 的序列化贡献 | 1295 | 1405 |
| 分组 hints 的序列化贡献 | 0 | 699 |
| 其它 metadata / envelope | 3086 | 3112 |

上述贡献采用同一 HTTP JSON 序列化方法，合计与整体请求一致，不是 Token 估算。
请求减少 4913 chars，约 33.6%；没有减少 atomic Evidence 或原文 excerpt。

| section | 原始数据库 | 派生 Context |
|---|---:|---:|
| general | 34 | 8 |
| education | 1 | 3 |
| projects | 1 | 23 |
| skills | 4 | 6 |

原来 projects 的唯一记录是标题。新 projects 包含真实正文；Kafka / Redis / RAG
分别出现于 4 / 2 / 2 条真实项目 Evidence。三个标题、姓名和联系方式归为 general。
数据库 section 不改，Context 是单独派生层。

## 测试

- 原 215 项保留，新增 13 项：backend **228 passed / 107.16s**，无 skip/fail。
- 独立空白测试库：interview_agent_f4_regression_profile_context_20261005_1。
  使用既有 run-regression.py；名称前缀来自脚本安全限制，不是重新启动 F4 阶段。
- 覆盖 PDF-like 标题置尾、项目技术关联、全部 atomic Evidence 保留、未知文本/歧义不猜测、
  alias 稳定性和未知 alias、真实 key 拒绝、下调规则、unsupported strength、
  幂等持久化、历史不可变、Report 后改不影响 correction 分组、连续 correction。
- M3 Replay fixtures 改为输出 alias，仍验证数据库真实 key/FK；没有接受 Replay 特权引用。
  M4/M5/M6 的业务源码未修改，原回归继续通过。
- frontend **11/11**；pnpm production build/vue-tsc 成功。
- Python compile、git diff --check 成功。
- 正式库 revision 仍 0024；无 migration 修改，0018 字节 hash 与冻结 whitelist 一致。

## 唯一一次 DeepSeek 真实请求

使用修复后的真实 Resume 38 Context，Provider=openai_compatible，
model=deepseek-v4-pro-ga-260813，Prompt=1.0.2，max_tokens=6000。

- 临时诊断上限 120 秒；只有一次 HTTP 调用，无 fallback、无 repair。
- **结果：model_timeout，wall latency=120001 ms。**
- HTTP status、finish_reason、input/output/reasoning tokens 均未取得，保持未知。
- Structured Output、alias resolver、state normalization、最终 validator 未执行，
  不是这些校验失败。不能声称真实 Profile 已通过。
- 未创建正式 generation、AgentRun 或 ProfileVersion；Resume 38 原 generation、
  current pointer、全部 Evidence、旧审计在请求前后完全一致。
- 正式 resume_profile_timeout_seconds 仍为 60；未改 .env、模型路由或 key。
- 未重启现有 API/Worker；本次请求在独立诊断进程使用新代码，正常服务需后续受控重启加载。

## 限制与停止

Correctness / 输入边界修复已通过自动化回归，但请求变小并未解决这次真实模型超时。
不能把超时根因归结为已经减少的请求体积，也不能宣称真实生成成功。
旧 ResumeReport 的首次 LLM 生成与 ready 前置条件仍保留，本次没有重写上传/解析链路。
不继续提高 timeout，不测试 Doubao，不启动 JD importer，不扩展其它业务功能。

本轮未 commit；任务前的 docs/28_source_interview_handbook.md 和
docs/29_project_design_interview_guide.md 继续保留，未修改。
