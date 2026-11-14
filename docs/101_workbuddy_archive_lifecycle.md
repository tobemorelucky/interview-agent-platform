# 第二十二轮：L1 简历归档与 L2 私有 JD 归档（数据生命周期收口）

> 按你批准的范围执行：重建 Bundle + L1 + L2（允许一条最小迁移，仅隔离库执行）。
> **未实施** L3 彻底清除、L4 账号删除、自动 push、生产库迁移（生产仍 `0025`，本轮只读核对未变）。
> 真实模型 HTTP = 0（浏览器链路全部走 Replay）。

---

## 1. Git 成果保护

| 项 | 结果 |
|---|---|
| 轮初 HEAD | `c134a99`（`master`，工作区仅 `.workbuddy/` 未跟踪） |
| 远端 | **本轮不可达**：`git ls-remote` 失败 `CONNECT tunnel failed, response 502`（第二十一轮曾成功核实远端为 `411c345`）。按约定**未关闭 TLS 验证**、未重试到破坏性程度，如实报告为网络阻断 |
| 新 Bundle | `E:\work\code\python\backups\interview-agent-platform-rc-v1-20261114-1432-c134a99.bundle`（**2.7 MB**，`--all`） |
| 验证 | `git bundle verify`：**"The bundle records a complete history"**；`git clone --no-local` 到独立目录 `.runtime/backups/restore-check-r22` 实际恢复：`HEAD=c134a99`、`157` commits、`708` files、工作树干净 |
| 旧 Bundle | `...-1041-8a795ee.bundle` 保留，未覆盖 |
| 异地容灾 | **不具备**：两个 Bundle 与仓库同一物理磁盘（E:）。在 push 可用或拷贝到其他介质之前，机器损坏仍会同时丢失仓库与备份 |

> 注：Bundle 固定在轮初 `c134a99`；本轮代码提交（见 §6）未包含在内，批准后可再打一次。

---

## 2. 归档数据模型

**核心决定：归档是独立的生命周期字段，不是处理状态的新取值。**

| 表 | 字段 | 理由 |
|---|---|---|
| `resumes` | `archived_at TIMESTAMPTZ NULL` | `resumes.status` 继续只描述**处理状态**（UPLOADED/PROCESSING/COMPLETED/FAILED）；第二十一轮虽然确认它没有 CHECK、技术上可以直接塞新值，但"已归档"和"解析完成"是两个正交概念，覆盖语义会破坏既有判断 |
| `user_job_targets` | `archived_at TIMESTAMPTZ NULL` | `parse_status` 在 0027 有四值 CHECK，加值必须改约束；独立字段同时让"归档"与"已确认"可以并存——归档不撤销确认，恢复不伪造确认 |

`archived_at IS NULL` 即"未归档"，**所有既有行为不变**；迁移不改写任何数据行。

四个维度的明确区分：

| 维度 | 归档后 |
|---|---|
| 处理状态 | 不变（仍是 COMPLETED/FAILED 等） |
| 新业务（画像生成/匹配/面试/训练/编辑/推荐） | **禁止**（服务端 409，不是前端隐藏） |
| 历史引用（Session 快照、Evaluation、Coach、Memory、报告） | **完全有效**，无一行被改写 |
| 个人资料 | **未被清除**（MinIO 文件、raw_text、requirements_json、画像全部原地保留） |

---

## 3. 是否新增迁移

**是：`0028_archive_lifecycle`**（本轮唯一迁移，未动 0025–0027）。

- upgrade：两条 `op.add_column`（可空时间戳）；downgrade：两条 `drop_column`。
- **仅在隔离库执行**：迁移专项库 `interview_agent_v21_wb_r22_mig3_20261114` 升降级各一次 + 复升，共 **11 passed**（含新用例 `test_10_v28_archive_migration.py`：升→降→升，断言 0027 的对象在 0028 降级后原样保留）。
- 正式库 `interview_agent` 保持 0025（本轮只读核对：`rev=0025 / 19 sessions / 2026-10-07`，未变）。
- `scripts/run-workbuddy-tests.py` 的 `MIGRATION_TESTS` 清单补登 `test_10`，业务全量继续排除迁移用例。

---

## 4. L1 简历归档的实际行为

| 要求 | 实现 |
|---|---|
| 用户归档本人简历 | `POST /resumes/{id}/archive`（`ResumeService.archive_resume`）：行锁 `with_for_update` + 归属校验，"不存在"与"别人的"同答 404 |
| 跨用户 | 直接 404（`ResumeNotFoundError`），并有测试钉死"外人的归档尝试不改变任何行" |
| 幂等 | 第二次归档返回**同一个** `archived_at`（测试断言两次时间戳相等） |
| 与 Celery 竞态 | `status ∈ (UPLOADED, PROCESSING)` 时归档 **409 `RESUME_PROCESSING`**——任务拥有状态迁移权；对已 COMPLETED 的重投递，`process_resume_async` 的 ready 分支增加**归档守卫**：已归档则直接返回，不为它再生成画像（测试：重投递后画像生成器 0 次调用） |
| 不能进入新业务 | ① 画像生成 `POST /candidate-profile/generate` → 409 `RESUME_ARCHIVED`；② 新面试/训练 `build_context` 在解析画像版本后检查其 `source_resume_id`，已归档 → 409 `RESUME_ARCHIVED`（无论显式传哪个 version id 都被拦住）；③ 默认简历列表不含归档行（`include_archived=true` 才返回，管理页用于恢复） |
| 历史不受损 | 归档只写 `archived_at`；`interview_sessions.resume_id` 的 SET NULL、快照、报告均无触碰（既有会话测试全绿） |
| 恢复 | `POST /resumes/{id}/restore`：重新验证归属与状态；未归档时恢复 → 409 `RESUME_NOT_ARCHIVED` |
| 不声称已删除 | UI 文案与 API 文档都明确：MinIO 文件、报告、派生画像**原地保留** |

---

## 5. L2 私有 JD 归档的实际行为

| 要求 | 实现 |
|---|---|
| 归档自己的 JD | `POST /job-targets/{id}/archive`（`JobTargetService.archive`），幂等、行锁、跨用户 404 |
| 默认列表不出现 | `history()` 默认 `archived_at IS NULL`；管理页 `include_archived=true` 才显示（带徽标 + 恢复） |
| 不能创建新面试/训练/匹配 | 唯一咽喉点 `interview/context.py::private_target()`：归档目标 → 409 `JOB_TARGET_ARCHIVED`，**会话创建与训练工作台共用此函数**，直接传 `job_target_id` 无法绕过；`update_requirements` / `confirm` / `parse`(apply_parse) / `skill_recommendations` / `match_preview` 全部加 `_ensure_not_archived` |
| 历史 Session 冻结 | 归档只写 `archived_at`；快照表引用原样保留（测试：归档前创建的会话，其快照 `job_target_id` 与 `jd_fingerprint` 归档后逐字不变） |
| 历史报告来源 | 快照里的 `source_type="USER_SUPPLIED"` 不变，报告继续标注"用户提供（未经企业核实）"，不冒充官方岗位 |
| 归档后编辑 | 409（服务端拒绝）；详情页同时改为**只读展示**（不再渲染可编辑表格——这是浏览器验收中发现并修复的真实 UI 缺陷，见 §7） |
| 恢复不复旧状态 | `restore` 只清 `archived_at`；`parse_status/confirm/version` 从未被归档触碰，因此恢复后就是原本的确认态；推荐**每次现算**（无缓存可复活），恢复后推荐区块按当前 requirements 重新计算 |
| 公共链路不变 | `Company/Job/JD/Research` 代码零改动；公共会话路径的既有测试全部通过 |

---

## 6. 用户界面（真实 Chrome 157 / CDP 9222，E2E 库 `interview_agent_wb_r22_e2e_20261114`@0028）

页面按钮叫**「归档」/「恢复」**，确认弹窗与详情面板使用指定文案：
**「归档后将无法用于新的面试或训练，但历史记录及相关数据不会因此被彻底删除。」**
没有"永久删除"字样，也没有任何"隐私数据已清除"的表述。

| 检查项 | 结果 |
|---|---|
| 简历行归档 → 灰色「已归档」徽标 + 「恢复」按钮，其他行不受影响 | 通过（`r22_01_resume_archived.png`：第一行 已完成+已归档+恢复，其余行仍显示 归档/删除） |
| 简历恢复 → 徽标消失、回到默认列表 | 通过 |
| 重复归档（归档→恢复→再归档） | 通过（幂等由服务端保证，UI 无双按钮） |
| JD 行归档 → 行内徽标，「开始训练」入口消失，「恢复」出现 | 通过（`r22_04_jd_row_archived.png`） |
| 归档详情：指定文案 + 「恢复使用」+ 业务按钮隐藏 + 要求变为只读列表 | 通过（`r22_05_jd_detail_archived.png`） |
| 恢复后详情回到确认态（解析/确认/训练按钮回来，推荐区块重新计算） | 通过（`r22_06_jd_restored.png`） |
| 跨用户 | 页面层同样只暴露本人资源（服务端测试另有钉死） |

**浏览器验收中发现并修复的真实缺陷**：归档详情页原先仍渲染可编辑的要求表格（服务端会拒绝，但 UI 不应提供），
且推荐区块会对归档目标继续轮询推荐（服务端 409）。已修复：归档详情改为只读要求列表 + 推荐区块不渲染；
「恢复」现在立即重开详情，面板即时消失。已构建（`vue-tsc && vite build`）并复验。

---

## 7. 测试计数

| 项目 | 通过 | 失败 | 跳过 |
|---|---|---|---|
| 后端业务全量（`interview_agent_wb_r22_final_test_20261114`，0028） | **952** | 0 | 1 |
| 迁移专项（`interview_agent_v21_wb_r22_mig3_20261114`，含 0028 升/降/升） | **11** | 0 | 0 |
| 本轮新增：`test_resume_archive.py`(5)、`test_job_target_archive.py`(4)、`test_10_v28_archive_migration.py`(1) | 10 | 0 | 0 |
| 前端单元测试 | 7 | 0 | 0 |
| `vue-tsc && vite build` | 通过 | — | — |

既有 skip 仍为缺本地 realworld_eval bundle。安全底线核对：生产库 0025 未动；未删历史 Live；未清 MinIO；`AGENTS.md` / `CLAUDE.md` / `.claude/` 未触碰。

---

## 8. 归档后仍然保留的个人数据（如实清单）

归档**不是清除**。以下内容在归档后全部原地保留：

1. MinIO 中的原始简历文件（`resumes.storage_key`）与 `resumes.raw_text`；
2. `resume_reports.summary_json` 与 `resume_evidence.excerpt`（简历逐字切片）；
3. 全部画像版本、事实与证据关联；
4. `user_job_targets.raw_text / requirements_json / structured_json`；
5. 历史 Session、消息、Evaluation、Coach 报告、Memory；
6. `model_admissions` 账本（本就无个人内容）。

这就是 UI 文案说"不会因此被彻底删除"的原因——它是对用户的如实承诺，不是免责声明。

---

## 9. L3 彻底清除的前置条件（供你单独审批）

1. **新表 `purge_requests`**（幂等键、状态、审计列）——需要迁移；
2. **清理顺序**（无外键改动，纯应用层事务）：
   简历：`candidate_fact_evidence → candidate_facts → candidate_*_experiences → candidate_profile_versions（需先解除 candidate_profiles.current_version_id 与快照/匹配引用，否则降级为"脱敏保留骨架"）→ resume_evidence → resume_reports → MinIO 对象`；
   JD：无快照引用才可物理删，有引用则只脱敏；
3. **脱敏函数**：`audit_logs.before/after/metadata_json` 中的资源内容替换为 `{"redacted": true}`，保留动作/时间/IP；
4. **账号删除（L4）**：`users` CASCADE 已覆盖多数表，`model_budget_owners`（RESTRICT）与 `audit_logs` 保留并脱敏；
5. ** Celery 化**：清理任务幂等（资源不存在即成功）、失败重试不半删、MinIO 最后删；
6. **测试**：每张引用表一条"清除后历史可解释"用例（账本仍在、审计脱敏、报告骨架可打开）。

---

## 10. 本轮提交

| 提交 | 内容 |
|---|---|
| `feat(db)` | 0028 迁移 + 两个模型字段 + 运行器迁移清单 |
| `feat(resume)` | 归档/恢复服务与路由 + 画像生成闸 + 重投递守卫 + 5 条测试 |
| `feat(job-target)` | 归档/恢复 + 五个业务闸 + `private_target` 与画像来源闸 + 4 条测试 |
| `feat(web)` | 两页归档/恢复 UI、指定文案、只读详情（含浏览器缺陷修复） |
| `docs(workbuddy)` | 本文件 |
| `docs(memory)` | 独立记忆 |

真实模型 HTTP：**0**。生产库、历史 Live、MinIO 均未动。完成后停止，等待你单独审批 L3。
