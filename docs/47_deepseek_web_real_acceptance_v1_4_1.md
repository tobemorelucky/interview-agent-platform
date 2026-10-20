# V1.4.1：真实 DeepSeek Web 验收 Attempt 1

日期：2026-10-20。**部分通过，不标记完整验收通过。** 普通官方 DeepSeek API Key 鉴权与真实 Search 成功；真实 JD 完成抽取、审核发布与双用户共享；真实面经在原文字面支持校验失败，未创建/发布最终面经或题目。

## 1. 环境与预算

- Key 来自用户手动填写的本地 `DEEPSEEK_WEB_API_KEY`。这是普通 DeepSeek 官方 API Key，不存在额外“特殊 Web Key”的要求；独立变量仅用于隔离方舟模型凭证。没有输出或提交 Key，没有改 `.env`。
- 模型使用原配置 `openai_compatible / deepseek-v4-pro-ga-260813`，thinking=disabled，无 fallback。Web 接口为固定的 `https://api.deepseek.com/v1/tools/search`。
- 独立验收库 `interview_agent_v14_acceptance_live_20261020_attempt1`，迁移head=0025。准备脚本仅建立专用验收账号，不种植 Source/JD/面经 fixture。脚本准备日志中的“REPLAY database”是旧通用标识，**本轮真实调用均有 LIVE 审计，不使用其 Replay 服务模式**。
- Web上限：Search2、Fetch2，含重试；实际关闭重试并用隔离库持久预算限制Search为2。
- 模型上限：5逻辑请求、含repair最多10HTTP、100,000 Token、600秒。单批锁与不可覆盖的attempt文件，超限前停止；unknown usage保守计入，不当作0。
- 实际：Search2、Fetch0；模型5逻辑/5HTTP、15,236 Token、31.04秒，repair0、fallback0。未追加新批次刷面经成功。

## 2. 两次真实 Search

均 HTTP200、Provider=deepseek_web、mode=LIVE、need_content=true、count=3。关键词不包含任何私人简历/回答/Memory。

| 搜索 | 实际 query | 耗时 |
| --- | --- | ---: |
| 招聘JD | `华为 AI应用工程师 校园招聘 岗位职责 任职要求 (site:career.huawei.com)` | 1.207秒 |
| 技术面经 | `中国移动研究院 AI算法工程师 面经 PyTorch 数据不平衡 (site:nowcoder.com)` | 0.492秒 |

实际返回与保存：

| Snapshot | 标题 | URL | 正文字符 |
| --- | --- | --- | ---: |
| 1 | 华为2027届实习生招聘 计算产品线专场 | [哈尔滨工业大学就业站转载](https://career.hit.edu.cn/zhxy-xszyfzpt/zpxx/zczphxq?id=NGE4OTI0ZDEzYWE4NGFjYzk3ZTkwNmQ3YjUxZWUwNWU%3D) | 1611 |
| 2 | AI 应用工程师 | [国家大学生就业服务平台职位页](https://m.job.ncss.cn/student/m/jobs/5j515rAMiAM3eT4g7c7UzK/detail.html?type=null) | 667 |
| 3 | 华为校招 AI应用工程师 | [鼠鼠求职转载](https://www.shushuqiuzhi.com/position/202633) | 562 |
| 4 | 中国移动研究院AI算法工程师面经分享 | [CSDN分享](https://blog.csdn.net/qq_38735017/article/details/129004708) | 754 |
| 5 | 移动研究院算法工程师一面面经热乎的 | [牛客原帖与评论](https://www.nowcoder.com/discuss/427769028658184192?toCommentId=15083088) | 664 |
| 6 | 同一牛客面经帖 | [同帖另一评论参数页](https://www.nowcoder.com/discuss/427769028658184192?toCommentId=15101941) | 2112 |

**来源范围限制未严格执行：** 服务商返回了query限定域名以外的结果，不能把site语法当成来源认证。当前代码没有额外严格结果域名筛选，这是本次发现的限制，未用未经验证的参数改动或第三次Search掩盖。本轮JD选的是Snapshot2，页面自述来源智联招聘，**非华为招聘官网**；明确按community/第三方转载处理。公司招聘官网直发JD尚未验到。

Snapshot5/6为同一帖子不同评论参数，不是两个独立面试来源；本轮只选Snapshot5。其他来源只保存未审核原始快照，没有混入公共已发布列表。所有选中正文超过复用阈值，**没有必要再Fetch**，所以独立Fetch接口连通性仍未实测，不能写成Fetch成功2次。

## 3. JD真实完整链路：通过

Search task1 → Source/Snapshot2 → 内容寻址Research task3 → AgentRun1 → Job/JD/Requirement/ClaimEvidence → 管理员审核 → PUBLISHED → A/B查询同一记录。

- 公司：华为技术有限公司；岗位：AI 应用工程师；位置：四川省成都市，均引用实际正文。未声称该页面是2027官方校招，也未猜测发布时间或当前仍有招聘名额。
- 3条要求、1条地点attribute；5个Claim、6条ClaimEvidence支持关系。最后一条要求跨两个相邻Evidence分块，仍通过同Snapshot连续原文校验。
- 模型quality=suitable、无risk_flags；确定性gate仍为WAITING_REVIEW，因为来源为第三方。这不是模型宣布可信即发布。
- 管理员在真实Vue页面核对正文、要求、来源与依据，审核理由明确“国家就业平台转载、自述智联、非官网、时效未确认”，随后发布JD1。
- 只写隔离库，正式 `interview_agent` 无本轮资料写入。

## 4. 真实面经：失败保留，不发布

Search task2 → Source/Snapshot5 → Research task4 → AgentRun2=FAILED。

三个模型阶段的JSON与Schema均成功：Extraction产生6个候选问题，Routing及Reliability也返回结构化结果。但最终deterministic validator报：

`EvidenceValidationError: Web question must be extractive from cited source text`

这不是HTTP/timeout/JSON错误，也不是通过换模型能证明正确的问题。至少一个生成问题未满足所引Evidence的连续原文字面支持；可能是改写或引用选择问题，**当前审计未保存那条候选问题，不能声称已经确定具体是哪句或臆测成某个事实**。没有重新调用模型恢复/猜测那次响应。

面经实体=0、题目=0，未审核/发布；Snapshot与失败AgentRun/StepRun完整保留。Reliability=0.75只描述来源支持信号，不能盖过Evidence失败，也不能提升为公司官方事实。

## 5. 模型真实用量

每次均为目标DeepSeek、HTTP200、finish_reason=stop、reasoning_tokens=0、cached_tokens=0、repair=false。

| 阶段 | HTTP秒 | 输入Token | 输出Token | total |
| --- | ---: | ---: | ---: | ---: |
| JD extraction | 5.898 | 1379 | 406 | 1785 |
| JD quality | 1.645 | 1177 | 16 | 1193 |
| Interview extraction | 8.749 | 1526 | 715 | 2241 |
| Interview routing | 9.588 | 4083 | 809 | 4892 |
| Interview reliability | 3.832 | 4968 | 157 | 5125 |
| 合计 | 29.712 | 13133 | 2103 | 15236 |

Agent StepRun含程序开销，分别为5936/1668/8778/9610/3854毫秒；与HTTP耗时不是同一测量。5条ModelAdmission全部SETTLED，actual_tokens合计15,236，与HTTP usage一致。没有读取账单/赠送额度，不宣称模型账单为零。

按用户提供的5元/1000次成功请求价格，2次Search估算约0.01元，Fetch0；这是请求数估计，不是实际账单核销。模型未配置价格，不自行估价。

本地ignored数值审计：`.runtime/acceptance/deepseek_web_v141_20261020_attempt1.json`，保留STOPPED和面经失败，不改成全部成功。

## 6. 浏览器与共享验收

使用Playwright、真实Vue/FastAPI/PostgreSQL/Redis/JWT，无route.fulfill，无fixture Provider参与这次正文或抽取。

测试账号属于隔离DEMO身份，**网页来源和模型结果是真实LIVE**，不能因测试账号就把两者混称Replay。专用审阅运行模式禁止新增模型调用，防止浏览器重试消耗超出预算。

1. A登录 `/career`：JD尚未发布时列表为空，未审核不可见。
2. ADMIN登录 → 管理采集页 → task3 → 查来源/正文/Evidence → 填理由 → 发布JD1。
3. A重新登录看到华为JD，刷新仍在。
4. B独立正常登录读取同一JD，管理员入口0。
5. API交叉核对：两用户GET job1均200，投影完全一致、source_type=community；两用户管理接口均403。
6. 数据库逐条核对6个Evidence excerpt与Snapshot offset切片一致，JD=PUBLISHED；浏览器操作后模型账本仍只有5条，Search2/Fetch0，没有隐藏收费请求。

截图：`output/playwright/v141-before-review.png`、`v141-jd-reviewed.png`、`v141-shared-user-a.png`、`v141-shared-user-b.png`，均ignored。面经未完成，未伪造面经审核浏览器成功截图。

## 7. 有界必要修复与测试

发现已有Web字面校验放在InterviewPipelineAdapter整条链之后，导致无效问题还消耗Routing/Reliability两次请求。修复仅对Web来源启用同一校验的提前执行，放在抽取之后、Routing之前；末端防线保留，不降低标准，不改Profile或Agent数量。

同时增加后续失败诊断：`questions[i].question`、截断的问题文本、引用ID、cited literal/其他Evidence literal匹配信号。只记录公开结构化事实子集，不保存整份模型回复/隐藏推理，不自动纠正引用。**这不能追溯恢复本次已经丢失的候选句子，也不能将本次失败改写成成功。**

- 新负例先RED：原代码21 passed/1 failed，证明确实晚验且无field path。
- 修复后V1.4 Provider+真实PG专项：22 passed /14.50秒；Mock/Replay，无付费调用。
- 本轮验收预算专项5项与Provider13项合计18 passed /4.55秒，验证HTTP/Token/时间/模型拒绝及unknown usage保守计入。
- 追加原M2B Research、人工导入、公共发布和本轮预算相关回归：50 passed /12.58秒，全部离线Provider；Python编译、git diff whitespace和实际API Key扫描通过。
- 既有完整425后端与40前端/build属于V1.4上一轮记录，本轮不冒充重新全量跑过。

仅新增验收脚本、预算测试、交付文档；增量修改审阅runtime、Web面经校验时机/诊断及其回归测试。不修改模型路由、Prompt、Evidence准入标准、数据库Schema或正式数据，不commit/push。

## 8. 最终结论与复现

**未达到“JD和面经均真实完成入库发布”的最终验收标准。** 当前通过的是官方普通Key鉴权、真实Search正文、真实JD抽取与Evidence、人工审核、双用户共享。未完成项是面经模型输出对strict extractive contract的满足；另有site范围不严格、官方直发JD与独立Fetch未实测。

模型5逻辑预算已用完，Search2预算已用完，停止真实调用；不续开批次刷成功。下一次若修复面经输出契约，应保留本次失败并另设有界验收授权，不删除validator或改成Replay冒充成功。

可只读/审核复现现有真实结果（禁用新增模型）：

```powershell
apps/api/.venv/Scripts/python.exe -X utf8 apps/api/scripts/v14_acceptance_runtime.py --database interview_agent_v14_acceptance_live_20261020_attempt1 --review-live
node scripts/v12_acceptance_web.mjs
```

Web `http://127.0.0.1:5175`。专用验收ADMIN/A/B与密码见docs46，只在上述隔离库有效。管理页task3看已发布JD、task4看失败面经；普通页 `/career`只显示这条已发布JD。不将该记录写入正式库，不新建架构组件。

交付时关闭本轮专用Playwright浏览器与经PID/完整命令行核验的审阅API/Web。保留隔离数据库、STOPPED attempt文件和截图，不删除任何正式/历史数据。未commit/push。
