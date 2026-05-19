# 15 开发路线图

## 1. 总体原则

- 先搭骨架，再做业务；
- 先完成单条完整链路，再扩来源；
- 先 Mock，再接真实平台；
- 先人工审核，再考虑自动发布；
- 先工程正确，再谈优化。

---

# 2. Phase 0：工程骨架

目标：

- Monorepo
- Docker Compose
- FastAPI 最小服务
- Vue3 最小应用
- Worker 最小应用
- `.env.example`
- README
- 基础健康检查

验收：

- 所有基础容器可启动；
- API / Web / Worker 各自可运行；
- 文档与目录匹配。

---

# 3. Phase 1：认证与用户系统

目标：

- 注册
- 登录
- JWT
- 当前用户
- USER / ADMIN
- seed admin
- 登录页 / 注册页

---

# 4. Phase 2：面试知识库问答

目标：

- 文档导入
- chunk
- embedding
- Milvus KB collection
- 检索问答
- SSE
- 会话历史
- QA 页面

---

# 5. Phase 3：简历模拟面试

目标：

- 文件上传
- 简历解析
- 结构化简历
- 问题生成
- 答案生成
- 报告存档
- 报告页面

---

# 6. Phase 4：管理员采集任务框架

目标：

- crawl run/task 数据结构
- 管理员创建任务
- 任务状态
- Celery workflow 骨架
- MockSourceAdapter
- 管理后台任务页

---

# 7. Phase 5：牛客真实采集

目标：

- NowcoderAdapter
- Raw Pool
- Parsed Content
- Structured Extraction
- Scoring
- Candidate Pool
- Review
- Publish
- Milvus indexing
- 用户查询正式牛客面经

---

# 8. Phase 6：小红书图文采集

目标：

- XiaohongshuAdapter
- 图片下载
- OCR
- 文本融合
- 复用候选、评分、审核、发布流程

---

# 9. Phase 7：抖音视频采集

目标：

- DouyinAdapter
- 视频下载
- FFmpeg
- ASR
- 抽帧 OCR
- 融合文本
- 复用后续流程

---

# 10. Phase 8：评测与优化

目标：

- RAG 测试集
- 简历分析测试集
- 面经抽取测试集
- 评分一致性评估
- 性能优化
- 监控与部署打磨

---

# 11. 每阶段交付物格式

Claude Code 完成每阶段后，需要汇报：

1. 完成内容
2. 修改文件
3. 新增 migration
4. 新增 API
5. 运行命令
6. 测试结果
7. 已知问题
8. 下一步建议

---

# 12. 当前立即要做的事

你已经完成：

- 项目初步设计；
- 协同开发方案；
- 正式文档包生成。

下一步：

1. 将本正式文档包放入仓库；
2. 启动 Claude Code；
3. 发送 Phase 0 kickoff 指令；
4. 让 Claude Code 先输出计划，不要直接修改；
5. 将计划发回 ChatGPT 审查；
6. 通过后再让 Claude Code 实施。
