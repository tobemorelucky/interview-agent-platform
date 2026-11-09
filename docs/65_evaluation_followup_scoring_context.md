# Evaluation 最小父问答评分语境补齐

2026-10-27。仅补模型输入通道，收费请求0；无数据库迁移、正式库写入、模型路由/密钥修改、Controller状态机或Memory规则修改。不是新评分算法或新评测平台。

## 1. 四个暂定参考边界

用户已确认本轮边界；12样本仍为DRAFT_UNREVIEWED，reviewers未填，ground_truth=false。确认记录不等同独立人工金标准审核。

分数顺序：技术正确性/覆盖/深度/相关性/表达。

| 样本 | 原回答要点 | 暂定范围 | 暂定状态及关键理由 |
|---|---|---|---|
| E03 | 直接相加原始分数，无依据补写并造引用，随后承认遗漏 | 1–2/1–2/1–2/3–5/3–5 | NEEDS_CLARIFICATION。陈述错误方案和承认遗漏不等于展示正确机制；不能同步压低相关性/表达 |
| E02 | 知道不能随便相加、不能造引用，但不会解释原因或解决机制 | 1–2/1–2/1–2/4–5/3–5 | 本题考机制，NEEDS_CLARIFICATION；若目标仅风险识别，应另按对应Rubric，不把本边界套到所有题 |
| E12 | 成功后提交位点、失败重试，未处理数据库提交后重复执行 | 2–3/2–3/1–3/4–5/3–5 | PARTIALLY_DEMONSTRATED；若变体明确错误声称端到端不丢不重，可为NEEDS_CLARIFICATION。区分Offset、数据库事务、业务幂等 |
| E07 | 得到实质提示后，解释唯一约束、事务及提交位点前后的失败窗口 | 4–5/4–5/3–5/4–5/3–5 | 当前gap有充分证据解决且合法引用gap ID时可DEMONSTRATED_IN_SESSION；当前答对不等于独立掌握 |

不是所有分数融合都错，问题是未经校准直接相加异构原始分数；RRF也不是唯一合法方案。当前分档和状态均未修改生产规则，暂定边界只用于有限校准。

## 2. 实际输入及版本

新建Adaptive Session冻结：

```text
adaptive_policy.context_projection = ROLE_CONTEXT_V1
adaptive_policy.evaluation_context = FOLLOWUP_SCORING_V1
Main Prompt = 2.3.1（未改）
Evaluation Prompt = 2.3.2
```

2.3.2保留2.3.1全部评分指令，仅解释父语境的用途、UNKNOWN、截断及不得给父回答记当前分。输出AdaptiveEvaluation Schema、五维1–5及现有状态/原文引用校验未变。

新增Evaluation专用输入：

```json
{
  "scoring_context": {
    "version": "FOLLOWUP_SCORING_V1",
    "question_kind": "FOLLOW_UP",
    "parent_question_id": 19,
    "parent_status": "AVAILABLE",
    "parent": {
      "question": {"id": 19, "content": "真实父题原文", "start": 0, "end": 6, "total_chars": 6, "truncated": false},
      "answer": {"id": 4, "message_id": 29, "content": "真实父答原文", "start": 0, "end": 6, "total_chars": 6, "truncated": false}
    },
    "assistance_status": "UNKNOWN",
    "open_gaps": [{"id": "真实gap ID", "text": "当前目标尚未解决的缺口"}]
  }
}
```

上述数字仅说明字段形状，不是新增正式数据。真实来源ID均来自本场数据库记录，不能由模型填写。

- 首题NEXT且无父链接：NOT_APPLICABLE、parent=null。
- 合法追问或DEEP_DIVE：单个父题及有效父答AVAILABLE。
- 父链接缺失、无有效父答、异常关联：UNAVAILABLE、parent=null、parent_question_id=null，不泄露异场父ID或内容。
- question_kind缺失为UNKNOWN，不凭题面推断。
- open_gaps逐值保留本请求当前目标的既有gap，不能声称已确定本题唯一焦点或已解决。没有加入旧分数、历史能力状态、MemoryGuidedPlan或下一题候选。
- 父题最多512字符、父答最多1024字符，均为连续原文前缀，附精确[start,end)、完整字符数、truncated。没有模型摘要；不能把截断以外内容视为不存在，必要时澄清。当前原始Answer和Rubric完全不截断。

## 3. 生成、鉴权及一致性

调用顺序：

```text
正常Answer持久化
→ core_service.call_agent(Evaluation)
→ evaluation_context：只读解析本题单个父问答
→ Evaluation.reusable（完整输入Hash）
→ 投影后的输入/新冻结Prompt/Schema预算预留
→ Agent.call：相同输入Hash、同一投影
→ 原结构化及Evidence/gap校验
→ Controller原事务提交
```

evaluation_context先验证Session归属，再验证Snapshot属于同一用户/Session且冻结版本相符；当前Question按session_id查询，传入answer_id时还核对当前Answer、Message和原始回答匹配。

父查询要求：

- parent_question_id精确链接，父子同一Session，父题index早于子题；只处理明确FOLLOW_UP/DEEP_DIVE。
- 父题COMPLETED；父Answer也COMPLETED。
- Answer与父Question精确关联，Message同Session且role=USER。
- 必须有同Answer、同Session、同父Question的正式Evaluation。
- 同一Session已验证归属，因此不同用户、同用户不同Session、异常Message及错绑Evaluation都不能混入。

父Question→Answer是唯一关联，Evaluation.answer_id也唯一，因此查询结果确定。语境在复用、Hash和预算之前生成；不修改原payload，序列化沿用确定性键顺序。父内容稳定时重建语境一致，改变字段插入顺序也不会绕开已验证Evaluation复用。

旧Session无evaluation_context标记，继续原2.1.0/2.3.1等投影和冻结Prompt；不回填、不自动升级。V1普通面试及V1.2系统设计不走此新分支。旧ROLE_CONTEXT_V1与新FOLLOWUP_SCORING_V1可以并存，不要求迁移。

## 4. 明确没有实现的提示能力

现有interviewer_guidance是风格生成的表达帮助，不是可信技术提示事件。数据库没有独立的实质提示类型、来源和展示确认。本轮不增加、不猜测、不用题面关键词识别，assistance_status只允许UNKNOWN。

UNKNOWN不是NONE，也不表示未获得提示。Evaluation不得生成可信提示来源；父回答只能解释当前题指代，不能当作当前答对的证据。

Memory仍按原始持久化question_kind/interviewer_guidance等V2.2规则判断assisted，不读取这个UNKNOWN字段来增加独立正确次数。既有FOLLOW_UP/DEEP_DIVE仍保守视作assisted。该补丁不提供比原规则更强的实质提示追踪保证；未知提示不能被本补丁证明为“独立”。E07的提示来自校准注解，不是系统可信提示记录，Live须标“提示感知能力受限”。

## 5. 实际修改文件

- modules/interview/core_service.py：新增只读evaluation_context，并在call_agent复用/Hash/预算前接入。
- modules/interview/context.py：仅新建Adaptive Session冻结新标记和Evaluation版本。
- modules/interview/context_projection.py：保留旧分支，新版只额外携带scoring_context；版本、UNKNOWN及gap一致性校验。Main投影显式丢弃该Evaluation专用字段，防止未来调用者误复用packet。
- modules/interview/adaptive_prompts.py：保留全部旧常量，新增Evaluation2.3.2，不改变Main2.3.1。
- tests/test_v231_context_projection.py：新契约、父关联、异常/归属、截断、复用、预留验证。
- tests/test_evaluation_calibration_contract.py：旧A/B反事实继续固定旧版本；当前服务测试改为验证父语境已可达。
- tests/test_v211_director_contract.py：旧Session fixture明确没有新标记，加入2.3.1旧投影与新2.3.2同时续跑。
- tests/fixtures/evaluation_calibration_v1.json：仅记录用户暂定确认，E02/E12收敛状态边界；仍DRAFT_UNREVIEWED。
- 本文。未修改Controller、Memory、模型路由、正式数据库或历史Live审计。

## 6. 验收与真实失败记录

TDD先新增冻结契约测试，实际因freeze_prompts缺少evaluation_context参数失败；实现后通过。最初数据库测试遇到Docker未运行/PG starting up，不算功能红灯。

仅正常启动Docker Desktop一次；确认原PostgreSQL容器359a220e4ac1、postgres:16-alpine、挂载interview-agent-platform_pgdata后启动原容器一次。正常WAL恢复完成，未执行任何修复、迁移、Compose重建或数据清理。pg_isready未指定账号时日志role root不存在不代表业务账号失败。

隔离测试库interview_agent_v231_context_20261026_a，原0026；测试子进程DATABASE_URL和M1_TEST_DATABASE_URL均指向它，未迁移。仅追加/调整本轮专用测试数据，不修改正式库或历史Live库。

覆盖：首题、合法父问答、无父链接、无父回答、未提交父答、同用户异Session、跨用户、父自环、非USER Message、Evaluation错绑、错误owner、长父文本；当前原答/Rubric/gap、无历史Memory偏见、UNKNOWN保持、旧Prompt兼容、同键幂等、Evaluation成功/Main失败后复用、合法组合与引用、Token预留/结算及原Memory规则。

一次异常测试把父链接改为自环后又先调用旧规划器，卡在其祖先遍历，已中断测试进程。改为先创建合法请求，再注入数据库异常，只测本次公开评分边界，未修改Controller。**这也暴露了既有规划器对“数据库人为构造自环”缺少防护；本轮未修，不把评分边界拒绝等同整个规划器已能处理任意损坏数据。**正常Controller只创建指向既有父题的新题，不产生该自环。

核心相关回归99 passed/0failed/0skipped，41.04秒。扩大一次得到261 passed/1 failed：原Redis未启动导致Memory审批API获取锁时连接失败；核对原Redis容器e19d488772b4、redis:7-alpine、原redisdata挂载后只启动一次，PONG。兼容部分独立重跑163 passed/0failed/0skipped，96.54秒，覆盖普通面试、报告、Memory审批、两场闭环、Matching、系统设计、Skill及V1.3治理；未为环境错误修改业务逻辑。核心99与兼容163为不重叠集合，共262项，不将重复运行累加。

最后补充Evaluation语境不得泄露到Main的投影断言，先失败，再增加一行显式排除；核心99项最终重跑99 passed/0failed/0skipped，39.65秒。兼容163项在该防御性单行排除前完成，未再次全跑。Replay只证明契约、归属、持久化和状态，不证明真实模型评分准确。没有运行前端/浏览器或全部仓库测试，也没有把旧前端43/43当作本轮结果。正式库只读核对仍0025，隔离库0026。

## 7. 输入增量及下一次Live准备（未执行）

精确记录的是序列化字节，Token为估算而非新usage。按本地保存的A/B完整messages（含Schema/System），保留来源模板，替换四个样本当前题/原答/Rubric，并按新冻结2.3.2加入确定性的scoring_context；不改历史文件或Session。

以原服务商实测旧10851/新3045 input为标尺，同时计算UTF-8字节比与CJK/标识符/标点词法比例，取均值。本机未安装DeepSeek tokenizer。

| 样本 | 旧完整投影input估算 | 2.3.1角色投影估算 | 新2.3.2估算 | 新相对2.3.1增量 | 新完整messages字节 |
|---|---:|---:|---:|---:|---:|
| E03 | 10851 | 3045 | 3478 | +433 | 11382 |
| E02 | 10854 | 3047 | 3315 | +268 | 10924 |
| E12 | 10852 | 3046 | 3314 | +268 | 10927 |
| E07 | 10878 | 3067 | 3484 | +417 | 11394 |

四个新输入合计13591，相对角色投影12205约+11.36%；未恢复到旧全量43435。配对总input约57026，不能用本估算声称真实Token节省率。新增固定说明和一个父问答可解释这部分开销。

限制：这是以真实历史来源模板和当前冻结契约构造的离线反事实wire，并非四次新服务商usage；Kafka专业来源与实际未来Session准备数据可能不同，正式执行前必须重新按当时真实冻结wire预留。长父答受512/1024字符硬上界限制，但极端案例增长高于表内短样本；截断有可见标记，不暗删当前评分依据。

建议下一轮独立授权：**8逻辑/10HTTP/160000 Token/12分钟**，4组旧新配对，不自动重复筛选结果。规划：input57026×1.5=85539，正常output硬上限8×当前配置3000=24000，2个repair另留40000，共149539，剩余约10461机动。repair实际wire/准入需求超过预留时，请求前停止重新报预算，不裁剪依据凑限额。

旧臂究竟用旧完整2.1.0还是角色投影2.3.1须执行前预登记：表内采用更贵的2.1.0作保守预算；若只检验父语境补齐，应优先2.3.1对2.3.2，避免混合多轮优化因素。两臂当前题/原答/Rubric相同，父语境差异是明确自变量。

条件：隔离库正常、新Session冻结2.3.2、四个暂定边界沿用且DRAFT状态不升级、真实wire预算检查通过、另行费用授权。E07仅评价当前回答，不宣称完整提示感知识别。停止条件仍包含非法引用、状态/账本错误或上限达到。

有限配对仅收敛明显评分偏差，不能给统计意义的准确率；通过后应回归15–20轮完整Live、Coach和跨场次Memory闭环，不再无界扩展本地校准。
