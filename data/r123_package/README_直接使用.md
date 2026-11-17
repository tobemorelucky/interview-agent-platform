# 50份真实招聘JD + 3套面经模拟面试（2026-11-17）

这次提供的是**内容本身**，不是给WorkBuddy寻找信息的链接清单，也不是抓取脚本。

## 已交付

- **50份岗位**：21家企业、共47个来源URL，30份来自项目此前保存的招聘页面采集记录，20份本轮重新查到；共超过200条任职要求，每条都有职责、任职条件和原始链接。
- **3套真实面经改编的完整模拟面试**：A安软AI应用2026年线上技术面、B携程Java后端一面/二面（2021年历史面经）、C字节跳动AI Agent 2026年9月23日场次（公开合辑）——各20/21/20问，合计61问。
- **183条合成测试回答**：每题各有strong/blank/incorrect三种测试路径，用于验证评分是否对答非所问、技术错误和空白回答诚实。

## 文件

- `真实岗位JD50份_面试流程3套.xlsx`：Excel查看和筛选50 JD、61题、3流程
- `jd_50.jsonl`：每行一个岗位JSON，字段有`company/position/responsibilities/requirements/jd_text/source_url`
- `jd_50.csv`：Excel可读CSV，UTF-8 BOM
- `JD_逐条TXT/`：50个完整的**整理版职位要求TXT**，不是短标题
- `workbuddy_manual_import_50.jsonl`：每行含 `source_url/source_type/document_type/company/job_title/content` 的正式结构化输入（含hash校验及来源性质备注）
- `jd_50_readable.md`：50份岗位职责与要求的可读版全文
- `interviews_3.json`：3场从开头到收尾完整有序测试及每题隐藏评分要点
- `interview_questions_3.jsonl`：61道可逐题读取的结构化问题
- `interview_A_AI_APP.md`、`interview_B_JAVA_2ROUND.md`、`interview_C_AGENT_RAG.md`：三套逐题面试与评分文档
- `synthetic_answer_testcases.jsonl`：183条合成候选人回答和期望分数区间
- `test_contract.md`：测试规则
- `verification.json`：数量及检查结果

## 使用要点

1. JD是**真实职位信息的忠实整理/改写**，不是企业发布的逐字原文，也没有证实50份全部当前正在招；部分历史/已结束岗位适合模拟面试。是否允许进入PUBLISHED由产品自己的审核与数据展示语义决定，不应伪造页面DOM或哈希。
2. **不必再爬这50个岗位**：WorkBuddy直接读取JSONL或50个TXT，以已整理的职责和要求作为面试上下文。对于严格只接受官网逐字Evidence的公共发布链，可以改走隔离测试数据/用户录入JD路径，不要硬把改写句标为官网逐字证据。
3. 三篇面经来自个人公开自述，不是企业官方题库；提供的是**按公开内容重新编排的完整模拟测试流程**，并非所有原始问题的逐字复刻。原帖没有真人标准答案，评分要点由我们单独编写，仅供系统测试。
4. 第一次验收跑3场分别测试：AI应用与RAG、Java后端两轮、Agent/LangGraph/RAG评测。先试合成答案的高分、空白和错误三类，再使用真实脱敏简历做一次用户全链路验收。
5. 真实模型调用与数据库写入仍需按平台现有预算/权限执行；制作本数据包本身没有进行这些调用。

## 面经原帖

- 安软AI应用实习（2026-07-25）：https://api-cdn.nowcoder.com/feed/main/detail/40920533fad14136bff01c3928c7e953?sourceSSR=post
- 携程Java后端一面/二面（2021-10-24）：https://www.nowcoder.com/discuss/353158802822340608
- 字节AI Agent 2026-09-23一场（公开面经合辑）：https://www.nowcoder.com/discuss/940040254214389760?sourceSSR=post&weFlow=true
