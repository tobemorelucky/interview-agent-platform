# 真实人工来源材料：身份已核对，等待正式导入确认

本轮只核对公开页面/已保存正文身份、替换 JD01/EXP04、更新 manifest 与本地正文。
没有执行 importer、数据库导入、PublicDataResearchAgent、模型、Review/Publish 或 Job Matching。
Importer、manual-source adapter、测试与其他业务代码未修改。

9 项 READY（4 JD + 5 面经）。READY 只表示实际公开正文已取得且来源/身份有依据，
不表示已入库、内容真实性已被独立证实或岗位当前仍接受申请。
来源均为 MANUAL_SOURCE_IMPORT；不是 Live Search，也不是 DEMO/Replay fixture。
9 份 TXT 全部仅留本地且 ignored/untracked；manifest.json / 本 README 可跟踪。

| ID | 公司/招聘机构 | 岗位 | 类型/来源 | chars | SHA-256 前12位 |
|---|---|---|---|---:|---|
| JD01 | 华为 | AI应用工程师 | job_jd / official | 1053 | 90c9bea72a6f |
| JD02 | 中国移动通信集团浙江有限公司嘉兴分公司 | AI工程师 | job_jd / community | 989 | 2882b0b49213 |
| JD03 | 中国工商银行广东省分行 | 科技菁英 | job_jd / community | 947 | de35c41f5697 |
| JD04 | 中国移动通信集团湖北有限公司 | AI研发工程师 | job_jd / community | 1114 | 8425f9c1e930 |
| EXP01 | 华为 | 后端开发岗 | interview_experience / community | 2276 | 325617fdec18 |
| EXP02 | 字节跳动 | 算法岗 | interview_experience / community | 2265 | 87b45ddb672b |
| EXP03 | 上海银行 | 金融科技（应用开发） | interview_experience / community | 940 | f8ab6577cc8d |
| EXP04 | 中国移动研究院 | AI算法工程师 | interview_experience / community | 313 | e4fc8d10b426 |
| EXP05 | 中国工商银行 | 科技菁英 | interview_experience / community | 1084 | baf92e187fa1 |

## 身份与取材边界

- 7 份旧 READY 正文逐文件 hash 与上一轮一致，未为配合身份改写正文。
- manifest.company/job_title 已改为公开页面与正文支持的实际名称，初始声明保留在
  previous_declared_identity；observed_identity 补齐 document_type/source_type。
- JD03 正文招聘机构为“中国工商银行广东省分行”；同页企业发布主体为
  “中国工商银行股份有限公司广东省分行”，单独记录 publisher_legal_name，
  不擅自归并到总行，也不把别的省分行当作同一主体。
- JD04 金种子计划包括湖北移动长江研究院、数智化部等单位，不猜唯一部门/分公司。
  AI研发工程师的博士要求仍保留；高校转载仍是 community。
- EXP01/EXP02 为多场社区汇编。EXP02 含大模型、通用算法、Agent 算法实习及推荐算法实习，
  整篇身份采用算法岗，而不是将所有场次声称为“大模型算法岗”；细分名称保留在原文。
- EXP03 为2026秋招、作者记录2025过程；EXP05 原帖2024年、提及数据中心，
  未确认具体省分行，不能自行猜测或宣称为2027新面经。
- 所有面经继续 community/manual review，不声明为官方证实的亲历事实。

## JD01 来源替换

官方详情：
https://career.huawei.com/cn/job-details?advertisementId=36390

实际从 https://career.huawei.com/cn/campus-recruitment-job-list
点击“AI应用工程师”进入，岗位标记“应届生 / 研发类”、更新于2026-08-17。
详情须展开“岗位意向”，分别选“AI技术应用”与“AI系统软件”才能显示对应正文；
URL 通过 advertisementId=36390 固定岗位，不携带方向选择。
本地 TXT 按两个方向分节保存真实职责、要求、地点，不能将两组要求错误当作同时满足。
manifest.detail_reproduction 记录该复现步骤。
详情未明确展示2027届限制，正文未补写；公司为官网品牌“华为”，不猜具体法人/部门。
Browser未提供HTTP状态，manifest.http_status=NULL，不伪造200。

旧 https://www.huaweicloud.com/lab/security/career.html
保持 replacement_history 中 SOURCE_MISMATCH 历史，不再作为当前 READY URL。
旧来源从未创建正式 Snapshot，新正文不冒充旧 Snapshot。

## EXP04 来源替换

维护者提供的候选页面：
https://www.nowcoder.com/feed/main/detail/7738edfb3f64433ebf70b48377a4f82e

该页面是转发帖，公开原标题链接指向实际原帖：
https://www.nowcoder.com/discuss/427769028658184192

实际打开原帖HTTP200，正文明确“人工智能与智慧运营中心”“AI 算法工程师岗”，
原帖标签明确“中国移动研究院”。本地 TXT 保存原帖标题、主帖正文及原帖标签，
不是根据维护者列出的技术要点生成正文。
source_url 使用实际原帖 URL，requested_replacement_url 与 provenance_chain
保留候选转发页及原帖的关系。未保存作者账户资料、评论、推荐或广告。
旧 AI 视频行为面 URL 保留在 replacement_history 中，不再作为当前 READY source。

## 保存与停止点

acquired_at 为本地正文保存时间，不是网络 Fetch 成功时间或网页发布时间。
完整 URL、identity、字符数、SHA-256、来源替换及取材说明见 manifest.json。
9 份 TXT 均不纳入 Git；没有创建 Source、Snapshot、RefreshTask 或最终业务实体。
Importer 用法仍见 docs/33_manual_public_career_import.md（其中上一轮7 READY/2 mismatch
属于当时真实记录，未覆写历史测试与材料取得事实）。

已停止，等待维护者明确确认正式导入；不得直接批量导入 manifest。
