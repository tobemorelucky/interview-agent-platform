# R123 — 50 份 JD 入库 + 3 套完整面试测试 + 用户亲自验收准备

> 证据等级：**事实**=命令/DB/账本/截图实测；**判定**=基于事实的结论；**建议**=后续判断。
> 数据来源：用户提供的 `真实招聘JD50份_完整面经3套_测试数据包.zip`，未新增抓取。
> **本轮真实模型消耗：7 次 interview_coach / 23,754 token（一次操作失误误触发，见 §5.4）；其余全部 0 HTTP。**

---

## 0. 一句话结论

- ✅ **50 个独立岗位在前端可见可选**：公共职业页三页 20+20+10，面试页下拉 51 项（1 占位 + 50 岗位），全部标"招聘信息整理版"并保留来源链接。21 家企业。
- ✅ **三套面试脚本（61 题 × 3 变体）离线 Replay 跑通 177/183**：每答案经真实 Service/Controller、Evaluation 落库、Coach 报告生成；strong/blank 评分 59/59 命中预期区间。
- ⚠️ **6 个 run 未执行**：2 道背景类题（B_Q15、C_Q01）× 3 变体被平台合法的"背景题去重"策略拒绝——这是真实能力边界，未绕过。
- ⚠️ **7 次真实 Coach 调用是本轮操作失误**（误用默认 router），已在账本 SETTLED，如实披露；非用户授权的计划内调用。
- ✅ **干净验收用户已就绪**：`showcase-r123-user@example.com` / `Showcase123!`，0 简历/0 面试/0 记忆，用户可自己注册或登录、上传脱敏简历、选岗、面试、看报告、训练。

---

## 一、数据包校验（事实，`data/r123_validation/validation.json`）

解压到 `data/r123_package/`（65 文件）。校验脚本 `r76_r123_validate_package.py` 结果 **PASS**：

| 项 | 数量 | 校验 |
|---|---|---|
| `jd_50.jsonl` | 50 | 字段完整（公司/岗位/≥2 职责/≥3 要求/合法 URL）0 缺失 |
| `workbuddy_manual_import_50.jsonl` | 50 | content SHA-256 全对，与 jd_50 公司/岗位/URL 零不一致 |
| `JD_逐条TXT/` | 50 | 50 个 id 全覆盖，无多余 |
| `interview_questions_3.jsonl` | 61 | A 20 / B 21 / C 20，每题带 stage/topic/key_points/red_flag/follow_up/score_rules |
| `synthetic_answer_testcases.jsonl` | 183 | 61 题 × strong/blank/incorrect 三变体齐全 |
| `interviews_3.json` | 3 | 3 场完整有序脚本 |

数据性质如实保留：JD 是**招聘信息忠实整理/改写版**（非逐字原文、未核实实时在招）；面经是**公开自述改编的模拟测试脚本**（非企业官方题库、无真人答案/金标）。

---

## 二、50 份 JD 让平台真正可用（事实）

### 2.1 去重（事实，`data/r123_import/import_50.json`）
包内 50 份：21 家企业、47 个唯一 URL、50 个唯一公司+岗位、50 个唯一正文 hash。4 份共用麒麟校招列表 URL（不同岗位，正常）。与展示沙箱旧 9 份比对：3 个逻辑岗位重叠（JD01/06/08）——本轮采用**全新独立库**，不含旧岗位，因此无跨库重复问题。

### 2.2 导入路径（合规判定）
用户明确允许"私有 JD / 测试岗位路径或展示沙箱最小范围展示支持"，且"不得伪造 Evidence、不得绕过正式审核"。采用：
- **新建独立库** `interview_agent_showcase_r123_20261117`（alembic 0029），不碰正式库/旧沙箱/历史 Live。
- 50 个岗位各建 Company/Job/一份 PUBLISHED JD + 规则解析的 JobRequirement（252 条）。
- 登记 Source(47)/SourceSnapshot(50)，`source_type='CURATED_PARAPHRASE'`，metadata 标 `display_label=招聘信息整理版`、`provenance=CURATED_PARAPHRASE_FROM_REAL_JOB_POSTING`、`live_opening_unverified`。
- **0 Claim / 0 Evidence**：整理版本就不适用逐字证据，不伪造。
- 强度仅按"优先/加分"词规则标注（`requirement_parse=RULE_ONLY_NO_MODEL`），未伪装成模型理解。

### 2.3 前端可用证据（事实）
- `published_jobs()` = 50；`/career` 三页 20+20+10 = **50**；`/interview/targets` 下拉 **51 项**（占位+50）。
- 详情返回职责/要求/来源链接，`source_type=CURATED_PARAPHRASE` 前端可见。
- **Skill 推荐诚实**：23 RECOMMENDED / 26 NO_APPLICABLE_SKILL / 1 AMBIGUOUS，**没有为凑数强指定错误 Skill**（`data/r123_import/job_50_catalog.json`）。

---

## 三、三套完整面试离线测试（事实，`data/r123_replay/three_interviews_replay.json`）

### 3.1 执行方式（判定，必须说清）
平台主面试引擎是**自适应动态出题**，Controller 有严格题源/目标校验，无法在不改核心 Agent 的前提下把 61 道预编排题塞进"一场 20 题会话"。采用平台已验收的 **R24 固定题夹具模式**（`open_fixed_case` 同源）：每道题一个合法 `question_count=1` 会话，走真实 `MockInterviewService` → `SessionController` → Evaluation Agent → 落库 Evaluation → Coach。**题目由测试脚本提供（`source_type=DYNAMIC_GENERATED` 诚实标注），不冒充模型自主生成；隐藏 key_points/red_flag 不进被评价模型上下文**（评分 Replay 只按答案文本形态 strong/blank/incorrect 给分，不读取隐藏要点）。

### 3.2 结果
| 项 | 值 |
|---|---|
| 应执行 run | 61 题 × 3 变体 = 183 |
| 实际成功落库 | **177**（每 run 有 session + Evaluation） |
| 未执行 | **6**：B_JAVA_2ROUND_Q15、C_AGENT_RAG_Q01 各 3 变体，被平台合法策略 `INTERVIEW_BACKGROUND_LIMIT`（背景题已覆盖）拒绝 |
| Coach 报告 | 三场各一份，全部 `COMPLETED`（Replay Coach，引用真实评价） |
| 会话/评价总量 | 621 session / 540 evaluation（含冒烟与重试的历史会话） |

### 3.3 评分质量（事实 + 判定）
- **strong**：59/59 命中 expected_band [7,10]（10 分制，由 5 维均分×2）。
- **blank**：59/59 命中 [0,2]，空白回答未得高分。
- **incorrect**：59 个 run 全部 `technical_correctness=1`（最低档）、overall 2.8，**没有把技术错误当正确**；但仅 8/59 落在 expected_band 内。
- **incorrect 带宽为何低**：数据包 183 条回答里，**52 道题的 "incorrect" 实为"我没有可靠依据，暂时无法回答"**（与 blank 几乎相同），只有 9 道题的 incorrect 是真技术错误断言（如"epoll 任何负载严格 O(1)"、"二级索引叶子永远存整行"）。这是**数据包设计缺陷**，不是评分误判——真错误断言的 8 个 run 全部得低分并被标注 `factual_or_reasoning_issues`。
- **6 个未执行 run** 与 **incorrect 口径问题** 是本轮主要质量限制，均如实保留，未放宽校验。

---

## 四、前端浏览器验收（事实，`data/r81_r123_ui/ui_acceptance.json`，8 张截图）

真实 Chrome CDP 驱动 Vue+FastAPI（R123 库、Replay 夹具、外呼阻断），**9/9 步通过，170 条 API 全 200/201、0 错误**：

| 步骤 | 结果 |
|---|---|
| 登录合成用户 | ✓ |
| 公共职业页三页 50 岗位 | ✓ 20+20+10 |
| 岗位详情职责/要求/来源 | ✓ |
| 面试页岗位下拉 50 | ✓ 51 项含占位 |
| 不同岗位带入 JD 上下文 | ✓ detail 1943 字 |
| 选 Java 岗→建会话→三题作答评价 | ✓ 自动推荐 `java_backend`，首题"Java 与…" |
| 报告与历史列表 | ✓ |
| 刷新后历史恢复 | ✓ session 3→3 |
| 测试数据标识清楚 | ✓ 无冒充真实面试、整理版可见 |

截图：`data/r81_r123_ui/01_login.png` … `08_labels.png`。

---

## 五、模型调用与资产

### 5.1 账本（事实）
R123 库账本 **7 行 / 23,754 token**，全部 `interview_coach`，模型 `deepseek-ai/DeepSeek-V4.1-Flash-1017`，全 `SETTLED`。

### 5.2 7 次调用的性质（必须披露）
这 7 次是**本轮操作失误**：三套面试的 Coach 步骤初期我用了 `InterviewReportService(factory)` 默认 router（真实治理路由）而非 Replay 夹具，导致 Coach 真实调用。发现后立即改为 `ReplayCoach` 并重跑，**后续全部 0 HTTP**（Replay 重跑未新增账本行）。这是未预先授权的调用，虽单一模型/金额小/账本正常，仍如实报告，不掩盖。用户原要求"如需真实模型评价先列预算等授权"——本轮违反了这一条，特此说明。

### 5.3 资产边界（事实）
正式库 0025 未写；旧 JD 沙箱 0029（32 账本/81,458）未写；R121/R122 展示沙箱未动；历史 Live/Evaluation 2.3.4/Coach Harness/L3/L4 未碰；未执行 0030；未清库/删除/归档/push/reset/clean。R123 新库是唯一写入目标。

### 5.4 Git
提交见本轮末（代码/测试/脚本/文档/数据产物），**未 push**。

---

## 六、用户亲自验收准备（建议，环境已就绪）

### 6.1 干净账号（事实，已创建）
- 邮箱 `showcase-r123-user@example.com` / 密码 `Showcase123!`
- 状态：0 简历 / 0 面试 / 0 能力记忆（id=21，全新无历史）。
- 用户也可自行注册新账号。我**未替用户上传任何简历**。

### 6.2 启动服务（用户亲自体验需用真实模型，非 Replay）
项目根目录 `E:\work\code\python\interview-agent-platform`：

```bash
# 1) API（指向 R123 库，真实 ModelScope 路由；用户回答由真实模型生成）
cd apps/api
set DATABASE_URL=postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/interview_agent_showcase_r123_20261117
set MODEL_LEDGER_DATABASE=interview_agent_showcase_r123_20261117
set PYTHONPATH=src
.venv\Scripts\python.exe -m uvicorn interview_api.main:app --host 127.0.0.1 --port 8000

# 2) 前端（新终端）
cd apps/web
./node_modules/.bin/vite --host 127.0.0.1 --port 5173
```

浏览器访问 **http://127.0.0.1:5173**。基础设施（postgres/redis/minio）需先 `docker compose up -d postgres redis minio`（本轮结束时仍在运行）。

> 注意：用户亲自回答真实模型问题时会计入账本（R123 库当前 7 行）。如需完全隔离用户自测消耗，可另建账号/库；现有账本鉴权按平台默认。

### 6.3 用户可完成的验收链
注册/登录 → 上传脱敏真实简历 → 画像 → 从 50 JD 选岗 → 选完整面试或专项训练 → 回答真实模型生成的问题 → 看评价/Coach/能力记忆 → 下一场训练。

---

## 七、未解决与边界（如实）

1. **6 个 Replay run 未执行**（背景题去重策略），未绕过。如需覆盖，可在数据包侧把这两题改为技术题，或另建无背景题画像的用户——本轮未做。
2. **incorrect 样本 52/61 实为 blank**，无法据此验证"技术错误识别"的统计意义；9 道真错误题均被正确判低分。
3. **7 次真实 Coach 误触发**（§5.2），已披露。
4. **非自适应 Replay 题源由脚本提供**，不是模型自主出题（已如实标注 `DYNAMIC_GENERATED`）。
5. 距 45–50 份"官方逐字 PUBLISHED"仍有缺口——本轮 50 份是"整理版"展示数据，走的是允许的测试展示路径，不是正式 Research 发布。用户目标（前端可见可选 50 岗位）已达成。

## 八、关键文件索引
- 校验：`apps/api/scripts/r76_r123_validate_package.py` → `data/r123_validation/validation.json`
- 导入：`r77_r123_build_showcase_db.py` → `data/r123_import/{import_50,job_50_catalog}.json`
- 面试 Replay：`r78_r123_replay_interviews.py` → `data/r123_replay/three_interviews_replay.json`
- UI 验收：`r79_r123_ui_runtime.py` / `r81_r123_ui_acceptance.py` → `data/r81_r123_ui/`（8 截图 + JSON）
- 干净用户：`r80_r123_prepare_ui_user.py` → `data/r80_r123_ui_user/user.json`（验收用户另见 §6.1）
- 数据包原件：`data/r123_package/`

本轮完成后停止，不安排 R124，不继续采集。
