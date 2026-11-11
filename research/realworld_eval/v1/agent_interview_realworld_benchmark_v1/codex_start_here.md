# 直接交给 Codex 的第一批实施任务（只做 P0+P1，不大范围重构）

仓库 `E:\work\code\python\interview-agent-platform`。请读取 AGENTS.md、README、本项目 docs/69、70 和最新实际工作树。以下已提供 JSONL 资料放在 `research/realworld_eval/v1/`；保持它们与正式数据分离。

**核心目标**：六个现有 Agent 的流程不应只是“一次LLM生成完就下一节点”。逐步建立有限自主工具执行环境（Agent Harness）和基于公开真实岗位/公开面经的每日评测。但优先修复已知业务阻断，不新增第七个模型 Agent、不删Controller、不重构Memory。

**先做P0**：复现 `UNRESOLVED_KNOWLEDGE_GAP`；理清当前答题质量、resolved gap与目标整体状态，给出兼容旧Session的最小修复及完整PostgreSQL回归。防止真实未解决gap被清除。验证新免费Provider JSON/Usage/单次结构化输出能力。不能无限联网重试，真实API次数须明确授权并有本地硬上限；用户已经配置Provider，不要改密钥。

**P1方案及必要的审批**：结合实际源码设计 `ToolRegistry + ToolDispatcher + AgentStepLoop` 的最小版本，仅对 Main 启用3个只读工具。模型可选择调用工具或直接生成AdaptiveSelection终局；每个决策最多2~3轮模型反馈，调用和Tool结果均计入既有审计和预算。保持当前合法候选组合、supporting_refs、Answer引用、Controller提交和Session冻结契约。旧Session不隐式升级。Provider若不支持原生tools，可经同一dispatcher执行严格的JSON Action Envelope，但要在日志中标注其模式，不能假称native tool call。禁止模型自行调用SQL、shell、通用HTTP或写正式状态。关键坏例：无关检索、空结果、工具超时、非法参数、跨租户ID、Prompt注入、越界引用、额度不足。

**评测落地**：为四个JSONL建立只读加载器和schema校验；区分官方JD、面经自述、汇编主题、衍生合成场景。不要把社区主题当完整真实对话或已审核答案。先用5~10个有来源场景建立业务断言，并留出同源隔离的holdout。设计但不立即开通每日自动付费任务。固定Replay测试在真实Service+Controller+隔离PostgreSQL跑；Live只在用户批准硬配额后启动，默认不超过70HTTP/天并严禁自动修复代码。

**先交付设计评审**：列出现有代码已支持的能力、缺少的真实工具步骤、具体要改的类/文件、跨模块风险、请求预算模型、评测集加载的schema、需要新迁移与否。遵循AGENTS.md第11条跨模块审批：在未收到我的确认前不要直接修改多个核心模块。设计获批后再按最小切片实现。

**工程约束**：保护未提交修改、历史失败Session、正式数据库0025及旧演示库；不自动迁移正式库、不改Provider配置和密钥、不更改业务评分阈值、不提交push、不部署、不新增复杂框架。所有工具都必须白名单并按用户权限读取。完成后输出逐项状态、实际测试数量、真实HTTP数量和失败证据。不能用预设Replay输出宣称Agent自主性提升。
