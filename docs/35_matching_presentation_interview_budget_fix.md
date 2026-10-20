# 真实网页验收修复：推荐展示与面试预算（2026-10-06）

本轮仅修复已存在功能。保留入场前 Profile、人工导入和 Research 的未提交工作；
不新增 Agent、数据库迁移、Live Search 或业务能力，不 commit/push。

## 推荐展示

已有 MatchRun 1 不重新生成、不改 Evidence Matrix。前端纯 presentation adapter 按
JobRequirement 聚合，去重 CandidateFact、Evidence excerpt、解释及准备材料。
默认展示公司/岗位、定性等级、最多 4 条匹配亮点、最多 3 条风险及关键硬资格。
亮点只取现有支持关系，不为凑足数量编造；华为当前只有一项编程要求有支持，
因此显示 Python / Java 的材料依据，不擅自把未覆盖的大模型要求升级为匹配。

详细依据默认折叠，每项先展示至多 3 条不同正文；更多依据再次折叠。
原始 excerpt/offset、画像/JD version、三层 rank 和 fingerprint 不在默认卡片中铺开。
无关 NOT_APPLICABLE 筛选信息隐藏，UNKNOWN 硬资格没有改成已满足。

## Session 52 的真实根因

question_count=10、总轮次=1，只有一题和一个 Answer，零 Evaluation。
Snapshot 时间预算 7200 秒，模型预算 200000；会话约 38 秒即结束，不是题数耗尽。
首题实际审计 total_tokens=20155，但旧逻辑累积保留 106474 的 UTF-8/输出预估额度。
第二个模型调用预估叠加后被拒绝，AnswerEvaluationAgent 根本没有调用。
旧 `budget_complete` 又将未评价题标记 SKIPPED、Answer 标记 COMPLETED 并结束会话。

## 修复后的不变量

- Controller 短事务按独立 call key 预留在途额度；模型调用期间不持行锁。
- 调用结束结算：有 provider usage 时由审计计实际 tokens，释放临时预留；
  无 usage 的失败仍按保守估计收费，不使失败重试成为无限免费调用。
- 估计是 admission estimate，不冒充精确 tokenizer；题数、轮次、时间仍分别受约束。
- 评价调用被拒绝/失败，不再走 `budget_complete`：Answer=FAILED，Question=ANSWERED，
  Session 保持 IN_PROGRESS、原 active question 和同一幂等键可重试。
- 下一题调用失败同样不自动 COMPLETE；成功结果仍经 Controller 校验、事务提交后发 SSE。
- 合法结束仍由真实题数/轮次/审计 usage/经过时间决定，必须先通过本轮 Evaluation。

以上替代 docs/22 中“预留不退回、未评价预算不足可直接结束”的历史策略；
旧阶段文档和旧审计保留，不改写已经发生的事实。

## 提问与依据契约

InterviewMainAgent prompt 1.0.1 将公共原题作为 intent/source，输出自然中文提问。
1.0.0 保留。公共题库原文及 Snapshot 原指纹不修改，来源 ID 继续精确校验。
浏览器验收又发现下一题可带未配对 fact/evidence refs，严格 validator 正确拒绝；
补充 1.0.2：每个引用事实必须带该事实已有 evidence key，不能混用数值 ID；
纯追问不需要添加无关候选人事实，父题 provenance 仍由 Controller 继承。
1.0.1 亦保留。验证器不降级、不自动替模型修引用。
失败审计仅增加来源/ID/未配对 ID/数量诊断，不保存原始响应、简历 excerpt 或隐藏推理。
未获该题有效 Evaluation 前，参考答案、候选人依据及技术详情入口隐藏。

## 自动化

- 独立库 `interview_agent_f4_regression_20261006_interviewfix_b`：303 passed，123.60 秒。
  保留原 299 项，新增 4 项；旧预算测试改为校验失败不结束及幂等安全重试。
- 大上下文、本地无网络 provider fixture：question_count=10，连续 3 个有效 Answer / Evaluation，
  第四题仍 ASKED，Session 仍 IN_PROGRESS，7 次实际审计 usage=140000，预留已清零。
- 无 usage 失败保守收费、自然提问保留公共 fingerprint、引用失败不绕过且同键重试均覆盖。
- 前端 15/15（保留 11 项、新增 4 项）；vue-tsc / production build 成功。
- Python compile / 精确历史 EOF whitelist 检查通过；0017–0024 原始字节未修改。

## 真实浏览器验收

仅操作本人已登录账号的新会话 53。首题以真实 DeepSeek 成功，公共来源未改变。
唯一测试回答明确标记为修复验收输入，不是本人正式面试作答；不提交到 51/52。
首次下一题的引用校验失败已经记录，原回答保留、会话未结束；后续使用同键安全重试。
修复后浏览器正常刷新并使用原幂等键重试，通过真实模型评价和追问生成：

- Session 53 / owner 38 / Snapshot 4：IN_PROGRESS，题目预算 6，turn_count=1，
  state_version=4，active_question_id=31；completion_json=NULL。
- Question 30：公共来源 ID 52、原 fingerprint 固定，COMPLETED。
  Question 31：FOLLOW_UP、parent=30、ASKED，未代用户继续作答。
- Answer 2 只有一条；有效 Evaluation 1 对应 Answer 2 / Question 30。
  五维 4/3/2/4/4，确定性本轮均值 3.4；不是默认分。
- 重试 Evaluation：37.450 秒，input/output/reasoning=19741/2235/1739。
  下一题 Main@1.0.2：33.890 秒，20532/2059/1830。
  实际模型均 `deepseek-v4-pro-ga-260813`，没有 fallback、没有 structured repair。
- 包含保留的失败/重试调用在内，实际审计 total_tokens=111715；在途预留=0，
  budget_exhausted=false。失败历史没有删除。
- 浏览器刷新后仍为 1/6 已评价、可继续追问；只有已评价 Q1 显示折叠参考答案入口，
  未评价追问没有参考答案/技术详情入口。Console 无验收流程新 error。
- Session 51 入场时已不存在（本轮不恢复或重建）；Session 52 保持旧失败结果。
  两者 Session/Question/Message/Answer/Evaluation 的合并数据 hash 前后一致。

截图位于 ignored 的 output/playwright/：match1-simplified.png、
interview53-first-question.png、interview53-evaluation-followup.png。
正规 LIVE API/Worker/Web 保持运行，Search 未启动。停止在用户可继续手测的位置。
