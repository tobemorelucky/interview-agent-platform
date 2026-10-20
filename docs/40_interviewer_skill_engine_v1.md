# Interviewer Skill Engine V1

## 范围与边界

本轮为原有 InterviewMainAgent / AnswerEvaluationAgent 增加版本化面试官配置，不新增 Agent，不修改模型路由、Profile、Research、Memory 的领域设计。不新增依赖或 migration，不修改 `.env`。下文测试是配置/确定性策略/Replay 验证，不代表真实模型的语言表现已验证。

原有未提交文件全部保留；`git status` 中的 Profile、Research、Matching、Report、Memory 等已有改动不属于本轮交付。历史 migrations 未编辑。

## 选择与按需加载

目录：`apps/api/src/interview_api/modules/interview/interviewer_skills/`。

- `manifest.json`：三个 Skill 的 ID、中文名、当前版本。
- `policies.json`：轮次权重与交互风格。
- `<skill_id>/<version>/skill.json`：领域、能力点、项目深挖策略、不同水平的探查方法、五维评价锚点。
- 同目录 `SKILL.md`：选中后才加载的专业规则及证据边界。

三个 Skill：`ai_agent_application`、`java_backend`、`bank_it`。
两个轮次：`technical_screen`、`project_deep_dive`。
三个风格：`gentle`、`standard`、`strict`。

鉴权接口 `GET /api/v1/interview/interviewer-options` 仅读取目录元信息，不加载三份完整 Skill。创建会话仅加载所选 Skill；未知 ID 由请求校验拒绝，损坏/缺失配置在创建前拒绝，不留下半成品会话。

原有 `POST /api/v1/interview/sessions` 增加可选字段，例如：

```json
{
  "company_id": 1,
  "job_id": 1,
  "question_count": 6,
  "interviewer_skill_id": "java_backend",
  "interview_round": "project_deep_dive",
  "interviewer_style": "gentle"
}
```

公司、岗位 ID 仅为字段示例，必须替换为当前用户界面中可用的真实目标。没有选择 Skill 时仍可使用原 API 行为；不能只提供非默认轮次/风格而不指定 Skill。

## 版本固定与历史兼容

创建时将选中 Skill 的完整结构、SKILL.md、轮次、风格、公司资料依据、Prompt 版本及内容 fingerprint 写入已有不可变 `InterviewContextSnapshot.context_json.interviewer_config`。快照是本场唯一版本边界。后续回合不重新读取目录或最新 JD。

Skill 资产升级时新增版本目录并更新 manifest；保留旧版本。运行中和历史会话使用快照中的内容，不受 manifest 更新影响。新配置 Main / Evaluation Prompt 为 `1.1.0`，原 Prompt 注册方式及旧版本仍可读取。只有配置存在时使用新 Prompt。

旧 Session 无 `interviewer_config` 时保持原有策略，前端显示“原有通用面试策略”，不伪造历史配置。现有 Report、Memory 和所有权检查继续沿用原路径。

## 不只是 Prompt 的行为差异

### 专业 Skill 与轮次

确定性选题层在真实候选题源中按 Skill 主题优先级、轮次类别缺口、历史覆盖与冷却排序。AI 侧重 RAG/Agent/模型应用，Java 侧重语言机制/数据库/缓存/消息可靠性，银行 IT 侧重数据安全/权限/一致性等。没有来源的能力不能凭配置制造为候选人经历。

技术一面类别权重：项目 35%、基础 35%、岗位技术 20%、场景 10%；项目深挖面：55%、10%、15%、20%。这是候选选择优先级，不是对每场机械保证的题数比例。后者优先项目开场，前者优先基础；无对应可用题源时仍受既有来源和预算规则约束。

### 风格

| 风格 | Main 提问策略 | 有缺失点、且评价建议深入时的 Controller 行为 |
| --- | --- | --- |
| 温和 | 单点澄清，持久化非答案式表达提示 | 选择 FOLLOW_UP |
| 标准 | 机制、理由、trade-off 逐层探查 | 保留评价建议 |
| 严格 | 减少提示，要求具体机制、边界或反例 | 选择 DEEP_DIVE |

若评价已建议 NEXT，不会因风格强迫追问；所有映射之后仍检查原有追问上限、主题冷却、题目/轮次/时间/Token 预算。没有增加追问额度，没有绕开结束条件。

新配置下题目难度由程序给定：首题 MEDIUM，其后沿用当前难度，只有原有基于有效评价的 ADJUST_DIFFICULTY 路径可调整；模型不能因为“严格”自行加难。

Evaluation 使用专业 Skill 的五维评价锚点，但输入先转换为不含风格的评价契约；去掉表达提示，保持同一问题/回答/依据在三种风格下的评分输入与 Prompt 相同。五维 1–5 和原有验证器不变，不允许风格额外扣分。这是输入与程序规则的保证，不宣称随机模型多次生成的分数永远完全一致。

## 公司/JD 依据

只在创建快照时读取已有已发布 JD、Requirement 及其 Source/SourceSnapshot 元信息。保留来源身份，包括 community，不把社区资料升级为官方来源。具有来源链和要求时标记 `SOURCED_JOB_CONTEXT`；来源链不足时为 `GENERIC_SKILL_FALLBACK`。

即使有 JD，也只能说明岗位要求，不能推出“某公司必考题/内部偏好”。原有审核题源与确切 provenance 仍由 Controller 验证，不新增网络请求、搜索或公司 Prompt 库。

## 前端操作

1. 使用项目原启动脚本 `./scripts/start-interview-platform.ps1` 启动，打开正常 Web 地址 `http://127.0.0.1:5173/interview`，登录已有账号。
2. 确保已存在当前 CandidateProfile 和可用已发布岗位；本轮不会自动造数据。
3. 在左侧选择公司/岗位、轮数、专业 Skill、轮次与风格，再点击“新建”。从推荐页跳转现在只预选目标，不立即创建，避免绕过配置选择。
4. 顶部展示本场固定的 Skill / 轮次 / 风格，“本场面试官配置（已固定）”展开可查看领域、规则依据及版本。
5. 温和风格可看到表达提示；严格风格不显示提示。提交回答后原评价/SSE/安全重试路径不变。
6. 刷新后仍读取会话快照，不把左侧下一场的默认选项误作为本场配置。
7. 如需人工比较，可用同一目标分别选择不同专业/轮次/风格创建会话。真实创建并开始面试会使用当前已配置模型，可能收费；本轮未执行此类真实调用。

## 实际验证（2026-10-16）

- 按用户后续明确授权，仅启动已有 PostgreSQL 和 Redis 容器，未重建、删除或改动 volume。
- 隔离 PostgreSQL 定向回归：169 passed，90.56 秒；覆盖 Skill、M4、主题策略、恢复、M5 Report/Memory/两场闭环、M6 及 Profile Evidence Selection。
- 独立全量数据库 `interview_agent_f4_regression_skill_v1_full_20261016`：**360 passed，176.04 秒**。使用现有 `scripts/run-regression.py`，包含真实 PostgreSQL 和 migration 测试；不是正式业务库。两个本轮隔离测试库保留，不删除。
- 前端 `node --test tests/*.test.mjs`：**33/33 passed**。
- `pnpm build`：TypeScript 检查和 Vite production build 成功。
- Python compileall 与本轮文件 whitespace 检查通过。
- Playwright：独立 DEMO 浏览器中拦截全部 API 为明确标识的 fixture，验证三项选择、创建请求、固定配置、温和提示、刷新恢复和折叠详情；控制台 0 errors / 0 warnings。截图：`output/playwright/interviewer-skills-v1.png`（ignored 本地产物）。这不是 LIVE 模型 E2E。
- PostgreSQL Replay 集成验证：严格风格三轮有效 Answer/Evaluation 完成；相同幂等键不重复调用；完整快照不重载；三个评分保持 fixture 原值；跨用户读取 404。
- 单元测试验证三种风格评分输入/Prompt 相同，Skill/轮次选择不同，限制不可突破，实际临时配置升级后旧快照不漂移，community 身份不被改变。

真实模型对不同风格的自然语言表现尚未验证；禁止将上述 Mock/Replay 行为测试写成真实模型效果验收。没有新增付费调用、改变默认模型或修改密钥。

## 本轮文件

新增：

- `apps/api/src/interview_api/modules/interview/skill_engine.py`
- `apps/api/src/interview_api/modules/interview/interviewer_skills/manifest.json`
- `apps/api/src/interview_api/modules/interview/interviewer_skills/policies.json`
- 三个 Skill 各自的 `1.0.0/skill.json` 与 `1.0.0/SKILL.md`（6 个文件）
- `apps/api/tests/test_interviewer_skills.py`
- `apps/web/tests/interviewer-skills.test.mjs`
- 本文档

最小增量修改：

- `apps/api/src/interview_api/modules/interview/schemas.py`
- `apps/api/src/interview_api/modules/interview/context.py`
- `apps/api/src/interview_api/modules/interview/controller.py`
- `apps/api/src/interview_api/modules/interview/core_service.py`
- `apps/api/src/interview_api/modules/interview/agents.py`
- `apps/api/src/interview_api/modules/interview/router.py`
- `apps/api/src/interview_api/modules/interview/topics.py`（原有未跟踪文件，仅增数据安全主题识别）
- `apps/web/src/types/interview.ts`
- `apps/web/src/api/interview.ts`
- `apps/web/src/pages/InterviewChatPage.vue`
- `apps/web/tests/job-matching.test.mjs`（推荐跳转改为先选配置，不自动创建）

无数据库迁移，无新表。没有 commit 或 push。临时 UI 验证服务和独立浏览器验收后关闭；用户授权启动的原 PostgreSQL/Redis 保留运行。

## 后续边界

V1 已有严格结构化 Artifact + Controller，不需要为 Skill 加原生 Function Calling。只有未来确有受控工具执行需求时再引入。交互式系统设计面试需要可验证场景状态与环境反馈，应单独评估，不能把这次配置系统宣称为已实现的环境执行能力。
