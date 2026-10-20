# 最后一轮：技术面试节奏与适度 UI 收尾

日期：2026-10-07。本轮只涉及展示、选题与确定性主题计数，不新增 Agent、Schema、migration；不改 Profile / Research / Memory，不 commit/push。

## 展示

- 推荐、面试、报告复用现有卡片、间距、按钮和长文本样式，没有替换布局或 UI framework。
- 面试官问题突出；回答、评价、参考回答分区；参考回答仍须该题有效评价后才可打开。
- 轮次显示为“第 N / 总预算轮”，包括追问；处理中显示阶段、累计等待时间，刷新读取持久化状态。
- Profile/JD 版本、覆盖详情、报告 generation history 默认折叠，不删除 provenance 或 Evidence Matrix。

## 选题与边界

- `technical-topics@1.0.0` 从已持久化 SessionQuestion 重建覆盖与次数，写入既有 `candidate_refs_json.topic_policy`；不由模型计算次数，不查询最新 Profile/JD 替换 Snapshot。
- 技术主问题优先级权重：项目 .40 / 基础 .30 / JD 技术 .20 / 场景 .10。是优先级，不承诺短场次精确配比。
- 前五个主问题最多一个背景问题，全场最多两个；学历/专业等硬资格要求不作为口头资格核查题源。没有改变 Matching eligibility。
- 一个项目开场及追问链之后，优先选择另一个有真实证据的项目及尚未覆盖的技术点。
- 已知技术主题连续三轮进入冷却；FOLLOW_UP / DEEP_DIVE 绑定父题，不因省略 Kafka/RAG 或使用“消费线程”等次级词绕过主题计数。
- 技术英文紧接中文使用 ASCII 技术词边界；不把 JavaScript 误识别成 Java。
- 所有题目进行规范化文字 fingerprint 去重。未做 embedding 或语义近似去重，复杂同义问法仍依赖 Prompt 的单点追问约束。
- Main Prompt 当前为 1.0.13，历史版本保留；Evaluation Prompt 1.0.4 没有修改。

## 真实模型验收与保留的失败

真实用户 38，Resume 38 / ProfileVersion 1，华为 Company 4 / Job 4，模型 `doubao-seed-2-1-turbo-260628`，LIVE 模型；回答明确为验收练习，不成为新履历事实。不使用 Replay 模型、不调用 Live Search。

- Session 64：迭代中一次 Evaluation HTTP headers 等待超时，使用同 Answer / 幂等键安全重试成功；历史失败和费用预算保留。
- Session 65：项目开场 → RAG 接口追问 → 检索异常深挖 → Kafka 可靠消费 → 重试退避 → 顺序处理。第五轮后，前期失败调用已计入预算，第六次回答被预算保护拒绝；保留六道题、六个 Answer、五个有效 Evaluation，不伪装 COMPLETED，不重置预算。
- 第三轮生成题曾因中英文词边界主题匹配漏识别被拒绝；修复后同一 Answer 安全重试，复用已验证 Evaluation，没有重复评分。
- 最终六轮验收 Session 66 已 COMPLETED：6 个唯一 Answer、6 个有效 Evaluation，Question 全部 COMPLETED，active_question_id=NULL，state_version=13；12 次真实模型调用、12 个成功 AgentRun，无重试、repair=0、reasoning tokens=0。
- 问题实际顺序：项目实现开场 → 检索/生成接口与异常处理 → RRF 排名融合与原始分数加权取舍 → 切换另一项目的 Kafka 可靠消费 → 重试退避/异常分类深挖 → Query Rewrite 与原查询协作。背景 0/6、技术/项目 6/6；3 个主问题、1 次 FOLLOW_UP、2 次 DEEP_DIVE。短场次没有凑比例，也没有问学历或用面试判断硬资格。
- 本场全程 Snapshot fingerprint `e70f1f5f548880a4d9382ea7ea4b4b1f0eaf43f535db808b678bb6797cfc8024` 不变；来源保留真实 PublicQuestion 52 / fingerprint、CandidateFact 26/18/25、ResumeEvidence，父题绑定追问。
- 最终重新从数据库读取核对 Answer/Evaluation 一一对应及唯一幂等键；deterministic overall_average=3.8667，answered_count=evaluated_count=6，skipped_count=0。没有修改五维评分或使用默认评分。
- 本场真实模型总 usage：input 161894 / output 8166 / reasoning 0；Main 平均 8.25 s，Evaluation 平均 24.63 s。均为 Provider 返回的数值，不估算成本。页面不是即时响应，阶段提示不意味着模型已经完成。

| 轮次 | 问题摘要 | Main latency ms | Evaluation latency ms |
| --- | --- | ---: | ---: |
| 1 | 项目实现开场 | 6915 | 22133 |
| 2 | 检索/生成接口、异常处理 | 7590 | 20390 |
| 3 | RRF vs 原始分数加权 | 9497 | 32100 |
| 4 | Kafka 可靠消费 | 9126 | 25691 |
| 5 | 重试退避与异常分类 | 8064 | 21518 |
| 6 | Query Rewrite 协作 | 8335 | 25924 |

## 自动化与运行边界

- 前端 31/31；TypeScript + production build 通过。
- 后端最终全量 340 passed（独立 regression DB，139.37 s）；本轮新增 15 个测试用例，没有删减原有回归。
- 主题回归新增：背景限额、教育重述拒绝、连续三轮冷却、来源去重保留项目、切换另一项目、选定主题约束、英文紧接中文、父题主题继承、刷新恢复计数/provenance。
- Python compile、精确历史 EOF whitelist、`git diff --check` 通过；0017–0024 八个 migration SHA-256 与 F4 baseline 相同。
- Secret scan 无泄露、无 forbidden artifacts；`.env` / `.runtime` / backup / 浏览器截图保持 ignored。旧 `audit-delivery.py` 的完整 F4 frozen-code 比较会因之后用户授权的业务修复与配置变更失败，不宣称该旧冻结断言通过，也不改写其 baseline。
- API/Worker/Web 由现有 ownership 安全脚本运行，正式数据库 revision 0024；Live Search 冻结，SearXNG 不启动。
- 本轮浏览器在报告页面看到调整后的折叠版本历史；转到推荐页后真实登录已过期，当前停在 `/login`。已请求用户恢复登录，不能将受鉴权阻塞的 Playwright 主流程报告成通过。

## 手测入口

- 推荐：`http://127.0.0.1:5173/jobs/matches/9`；从华为推荐按钮创建自己的新面试。
- 本轮验收记录：`http://127.0.0.1:5173/interview?session_id=66`。
- 已有报告展示：`http://127.0.0.1:5173/interview/reports/37`。

旧会话、旧报告、原公共题目、Profile 与 Memory 没有被本轮验收覆写。
