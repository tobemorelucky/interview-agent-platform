# 13 测试与评估策略

## 1. 目标

保证项目：

- 功能正确；
- 边界不被破坏；
- AI 输出可持续改进；
- 离线流水线可回归验证。

---

# 2. 测试分层

## 2.1 Unit Tests

测试：

- 评分聚合公式
- 状态机
- 权限判断
- 文本清洗
- 过滤器解析
- Provider fake 实现
- 发布前校验

---

## 2.2 Integration Tests

测试：

- API + PostgreSQL
- API + Milvus
- Worker + Redis
- MinIO 文件上传
- 发布流程后索引流程

---

## 2.3 End-to-End Tests

关键流程：

1. 注册 -> 登录
2. QA 提问 -> 流式回答
3. 上传简历 -> 报告生成
4. 管理员创建 Mock 采集任务
5. Candidate 审核 -> Publish
6. 普通用户搜索到正式发布内容

---

# 3. AI 功能评测

## 3.1 RAG 问答

可建立人工小集：

```text
question
reference_answer
expected_keywords
expected_sources
```

评估：

- 检索召回
- 答案正确性
- 引用相关性
- 幻觉率

---

## 3.2 简历分析

样本：

- 后端简历
- AI 应用简历
- 数据简历
- 项目描述模糊简历

评估：

- 问题针对性
- 深挖程度
- 答案是否贴合简历
- 是否发现弱点

---

## 3.3 面经抽取

样本分平台：

- 牛客文本
- 小红书图文
- 抖音视频

人工标注：

- 是否面经
- 公司
- 岗位
- 轮次
- 题目列表
- 是否营销

评估：

- 实体抽取准确率
- 题目抽取准确率
- 分类准确率
- 营销风险判断一致性

---

# 4. 回归测试

修改以下模块后应跑回归：

- Prompt
- Extraction schema
- Scoring rules
- Retrieval logic
- Publishing logic

---

# 5. Mock 与 Fake

建议准备：

- FakeLLMProvider
- FakeEmbeddingProvider
- FakeOCRProvider
- FakeASRProvider
- MockSourceAdapter

用于：

- 快速测试；
- 避免 CI 调用真实外部模型；
- 保证状态流测试稳定。

---

# 6. CI 建议

最少：

- backend lint
- backend tests
- frontend type check
- frontend build
- migration smoke check

后续可加：

- Docker compose smoke test
- basic API health check

---

# 7. 手工验收清单

## 7.1 权限

- 普通用户不能访问 admin API；
- 用户不能看他人简历；
- 未登录不能访问受保护资源。

## 7.2 内容边界

- Candidate 不出现在普通搜索；
- Published 未索引不应假装可搜；
- Raw 不出现在用户页面。

---

# 8. 性能关注

- QA 响应首 token 时间；
- 面经查询耗时；
- 向量查询耗时；
- Worker 单任务耗时；
- 大文件 ASR 耗时。

---

# 9. 测试数据存放

建议：

```text
packages/evals/
├── rag_qa/
├── resume_analysis/
├── ingestion/
└── fixtures/
```

---

# 10. 测试验收

- 至少有核心状态机测试；
- 至少有 auth 测试；
- 至少有 publish 流程测试；
- Prompt 输出 schema 可校验；
- Mock Pipeline 可完整跑通。
