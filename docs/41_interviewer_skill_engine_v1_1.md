# Interviewer Skill Engine V1.1：岗位联动与动态目录

日期：2026-10-17。仅完成 Skill 动态注册和确定性岗位推荐；没有新增 Agent、模型请求、依赖、基础设施、表或 migration。未修改 `.env`、当前模型配置及原有六 Agent 的职责。仓库进入本轮时已有的大量 tracked/untracked 工作全部保留，未 commit/push。

## 1. 增量结构

```text
已发布 Job / JD / Requirement / Tag
    ├─ 原 JobMatchingAgent：资格、Evidence Matrix、岗位排序（不变）
    └─ Skill Recommendation Service：确定性、只读
          ↑ 动态 Skill Registry 元数据
          ↓
推荐页提示 / 直接选岗 → 自动推荐或用户手选
          ↓
创建面试：重新校验当前岗位、当前推荐
          ↓
原 InterviewContextSnapshot 固定完整配置与选择来源
          ↓
原 Main / Evaluation / Controller
```

`JobMatchingService.serialize` 在响应中增加 `interviewer_skill_recommendation`，使用本次 MatchRun 已固定的岗位输入，不修改候选、排名、资格、Evidence Matrix、生成幂等键或持久化推荐。该附加推荐表示“当前本地 Skill 目录下适合的面试策略”，不是历史岗位排名的一部分；目录升级后此辅助提示可能变化，已创建面试的快照不会变化。

直接选岗读取当前已发布 JD 的结构化字段、要求和标签；历史匹配页使用其固定输入。进入创建页后以当前岗位为准重新推荐，因此历史 JD 与当前 JD 不同时提示可能不同，不复制历史 MatchRun 为面试快照。

## 2. 动态 Registry 与新增步骤

V1 根目录 `manifest.json` 保留为历史文件，不再决定注册范围。V1.1 扫描：

```text
interviewer_skills/<stable_skill_id>/<semver>/
    manifest.json
    skill.json
    SKILL.md
```

新增普通专业 Skill 的步骤：

1. 创建上述目录；稳定 ID 采用小写字母、数字和下划线，版本为三段数字。
2. 按三个现有版本 Manifest 的统一格式填写 `id / label / version / professional_category / applicable_roles / capability_tags / scope / matching`。
3. `matching` 填写岗位类别、名称别名、职责词、能力组合及排除条件；不填写公司名称作为决定性依据。
4. `skill.json` 继续遵循 V1 领域、项目策略、水平探查和五维评价锚点格式；`priority_topics` 优先使用既有主题词表，不改变评分维度或预算规则。
5. 编写本地可信 `SKILL.md`，保留 Evidence 与权限边界。无需修改 Python Skill ID 列表、推荐器、Agent、Controller 或前端选项。
6. 目录元数据缓存最长约 2 秒；随后请求可发现新增版本。维护测试可使用 `discover(root, refresh=True)` 强制刷新。

同一稳定 ID 允许多个历史版本，按数字语义版本选择最高合法 Manifest。Manifest 校验结构、目录身份、版本、正文路径是否存在且位于本地版本目录。目录查询不会读取 Skill 正文；只有创建时读取所选 `skill.json` 和 `SKILL.md` 并校验内容。

非法 Manifest 单独隔离并记录日志；目录接口只返回隔离数量，不把错误文件内容发给普通用户。较新 Manifest 不合法时可以保留较旧合法版本。选中 Skill 正文损坏时该项创建失败，其他合法 Skill 和已有会话仍可使用。

`required_tools` 是能力声明，不是授权。V1.1 不支持执行工具，非空声明会被隔离；新增文件不能获得 shell、代码执行或其他工具权限。

## 3. 推荐算法

策略版本 `1.1.0`，固定输入可重复计算。NFKC、大小写及空白规范化，拉丁词保留边界，Java 不等于 JavaScript。

| 信号 | 分值 | 规则 |
| --- | ---: | --- |
| 岗位专业类别 | 10 | `job_family` 匹配 Manifest 类别 |
| 岗位名称 | 8 | 名称匹配声明的岗位别名 |
| 核心职责 | 6 | 已有结构化职责 / Requirement 匹配职责词 |
| 能力组合 | 每组 3 | 每个能力组至多计一次；不按词频累加 |

三个内置 Skill 各有三个能力组。权重和阈值集中于 `skill_recommendation.py`，不是匹配概率，也不向用户展示百分比。所有匹配附带真实字段路径、Requirement ID（可获得时）、命中词与限定长度的实际文本。公司名不进入输入信号。

排除条件优先作用于岗位名称及结构化职责，候选被排除后不会自动选中。只有通用 Java / Python / Redis / SQL 等词，没有类别、名称或专业职责信号时不自动推荐。

自动选择需同时满足：有职责或要求、存在未排除候选、具备专业上下文信号、第一名至少 9 分、领先第二名至少 3 分。排序同分时按稳定 Skill ID 排序，但不能用字母顺序掩盖歧义。

- `RECOMMENDED`：可默认选中。
- `INSUFFICIENT_DATA`：岗位内容不足，要求手选。
- `NO_APPLICABLE_SKILL`：现有目录缺少充分适用方向，要求手选。
- `AMBIGUOUS`：相近专业无法可靠区分，要求手选。

没有额外模型调用、embedding、向量索引或整份原始 JD 的重新语义解析。旧 Research 结构化结果通常只保存 requirements，缺失独立职责字段时如实使用要求，不虚构职责。批量当前岗位推荐使用三次公共数据查询，不为每个岗位读取一遍数据库。

## 4. API 与数据保护

- `GET /api/v1/interview/interviewer-options`：鉴权、动态元数据选项；不返回 Manifest 原文、路径、SKILL.md、评分锚点或系统 Prompt。
- `GET /api/v1/interview/targets`：沿用可用岗位列表，附带每项 Skill 推荐。
- `POST /api/v1/interview/skill-recommendations`：鉴权批量推荐，`{"job_ids": [1, 2]}`，每请求最多 50 项，缺失/未发布/不可用 Job 不生成推荐。
- 岗位匹配结果响应：附加 Skill 推荐，不改变原业务计算。
- 创建面试：新增 `interviewer_selection_source`，只接受 `AUTO_RECOMMENDED / USER_SELECTED`，旧客户端默认 `USER_SELECTED`。

创建时服务器重新校验自动选择是否仍为当前推荐，变化时返回 `SKILL_RECOMMENDATION_CHANGED`（409），不写半成品会话。手选允许与推荐不同，保留用户选择。

完整 Manifest、Skill 内容、版本、指纹仍保存在服务端原有不可变 Snapshot。选择来源和当次推荐依据固定于 `interviewer_selection`。模型输入 DTO 不发送该推荐排序元数据，避免把规则分值误作候选人能力分。

创建/读取会话的普通用户 API 通过只读 projection 隐藏内部 Skill 指令、评分 Rubric、原始 Manifest 和公共题源参考要点。数据库快照与内部 Agent/Controller 读取不变；前端仍可显示选中的专业、轮次、风格、领域和版本。

## 5. 前端状态

- 无手选 + 推荐充分：默认选中，来源 `AUTO_RECOMMENDED`。
- 自动状态换岗：根据新岗位推荐更新；不确定时清空并要求手选。
- 用户切换：来源 `USER_SELECTED`；迟到的目录或推荐响应不能覆盖。
- 手选后换岗：保留选择；方向不同或信息不足时提示确认。
- 创建后：本场展示固定配置及来源；左侧是下一场创建表单，不代表运行中会话配置。
- 刷新：本场配置从服务端 Snapshot 恢复；未创建的表单草稿不做跨刷新持久化。

岗位推荐卡只增加一行“面试 Skill + 理由”，不挤占匹配证据和硬资格信息。创建页提供折叠推荐依据，保持原轮次/风格选择，不展示评分规则。

## 6. 正式库真实 JD 只读示例

未执行 Job Matching 或其他真实模型任务，仅对现有已发布数据运行确定性推荐：

| 真实岗位 | 推荐 | 实际依据 |
| --- | --- | --- |
| 华为 · AI应用工程师（Job 4） | AI / Agent 应用开发 | 名称 AI应用；Requirement 15：大模型应用、向量数据库、大模型相关要求 |
| 中国移动通信集团浙江有限公司嘉兴分公司 · AI工程师（Job 1） | AI / Agent 应用开发 | 岗位名称；Requirement 4：人工智能技术、大模型及相关框架/项目要求 |
| 中国工商银行广东省分行 · 科技菁英（Job 2） | 银行信息科技岗位 | 名称科技菁英；Requirement 6：金融科技、应用研发、系统架构/信息安全等方向；不是根据“银行”公司名决定 |
| 中国移动通信集团湖北有限公司 · AI研发工程师（Job 3） | 需要手选 | 当前已发布要求不足以提供专业能力组合，只有名称信号；没有强行自动推荐 |

这些推荐不改变湖北岗位博士硬资格或原 Matching 的 UNKNOWN/NOT_SATISFIED 判定。Skill 适用性与候选人 eligibility 是两个不同问题。

## 7. 验证与命令

本轮先做纯函数/Mock 测试，再做隔离 PostgreSQL 回归。真实业务库只读核对 JD；没有创建正式演示 Session。按此前用户授权仅启动原有 PostgreSQL、Redis，不重建容器/数据卷。

- 初次离线针对性：28 passed、3 skipped；跳过项明确要求 PostgreSQL。
- 首次全量隔离库：370 passed、1 failed，失败为原有 Memory 测试所需 Redis 未启动，非业务断言失败。
- 启动原 Redis 后全量隔离库：**371 passed，154.34 秒**。数据库 `interview_agent_f4_regression_skill_v11_final_20261017` 保留。
- 随后补充了会话 API projection 防泄漏测试，并对最新代码再次执行 Skill/推荐/API/Replay PostgreSQL 定向回归：**32 passed，7.00 秒**（无跳过）。这包含新增的防泄漏测试；全量 371 项的那次运行是在该新增测试前收集。
- 前端 `node --test tests/*.test.mjs`：**36/36 passed**，包括真实运行的选择状态 reducer；原有源码断言仍属于静态 UI 合约检查，不冒充组件集成。
- `pnpm build`（vue-tsc + Vite）与 Python compileall 通过。
- Playwright 实际浏览器，所有 API 均被显式 DEMO fixture 拦截：推荐页提示 → 自动选择 → 换岗自动更新 → 手选新增 Python Skill → 再换岗不覆盖 → 创建 → 固定配置 → 刷新恢复；0 JavaScript errors / warnings。这不是真实 API/真实模型 E2E。
- 临时 Python Skill 在测试临时目录生成；实际 Registry API 发现、候选推荐、配置加载均验证，无需修改推荐器或前端核心代码。它没有加入正式 Skill 目录。

主要命令：

```powershell
# apps/api 内，现有项目虚拟环境
.venv/Scripts/python.exe -m pytest -q tests/test_skill_recommendation.py tests/test_interviewer_skills.py -p no:cacheprovider
.venv/Scripts/python.exe ../../scripts/run-regression.py --database interview_agent_f4_regression_skill_v11_final_20261017
# apps/web 内
node --test tests/*.test.mjs
pnpm build
```

第二条只允许全新专用测试库名，已运行过不能直接重复使用同名；保留失败 attempt，不清库重跑。最新定向数据库测试通过进程级测试变量连接隔离库，不打印或修改 `.env`。

## 8. 网页演示

使用原启动脚本，打开 `http://127.0.0.1:5173/jobs/matches`，登录本人账号，打开已有真实匹配结果，无需重新调用 JobMatchingAgent即可看到面试 Skill 提示。点击“开始模拟面试”后确认自动推荐与理由；手动切换 Skill，再换岗位检查选择保留；设置轮次/风格后新建。查看本场固定配置并刷新验证。

也可直接访问 `/interview` 选择已发布岗位，获得相同推荐算法的结果。真正开始面试仍会按当前模型配置调用收费模型；本轮未执行。

## 9. 文件与未解决项

新增：

- `apps/api/src/interview_api/modules/interview/skill_registry.py`
- `apps/api/src/interview_api/modules/interview/skill_recommendation.py`
- 三个已有 Skill 各自的 `1.0.0/manifest.json`
- `apps/api/tests/test_skill_recommendation.py`
- `apps/web/src/types/skill-selection.ts`
- `apps/web/tests/skill-recommendation.test.mjs`
- 本文档

最小修改：

- `apps/api/src/interview_api/modules/interview/skill_engine.py`
- `apps/api/src/interview_api/modules/interview/context.py`
- `apps/api/src/interview_api/modules/interview/schemas.py`
- `apps/api/src/interview_api/modules/interview/router.py`
- `apps/api/src/interview_api/modules/job_matching/service.py`
- `apps/api/tests/test_interviewer_skills.py`
- `apps/web/src/types/interview.ts`
- `apps/web/src/types/job-matching.ts`
- `apps/web/src/api/interview.ts`
- `apps/web/src/pages/InterviewChatPage.vue`
- `apps/web/src/pages/JobMatchingPage.vue`

未引入新 Runtime/向量库/Planner/工具授权系统，没有为 3 个 Skill 设计复杂基础设施。规则推荐不是语义分类器，对新岗位别名、稀疏 JD 和相似专业仍可能要求人工选择；需通过维护 Manifest 扩展覆盖，不承诺准确率。多进程 API 各有最长 2 秒的本地元数据缓存，未提供原子跨进程发布管理平台。真实模型风格效果未重测；历史状态、预算、报告、记忆、隔离等由既有及新增 Replay/数据库测试验证。
