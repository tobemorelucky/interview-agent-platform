# 04 前端设计

## 1. 前端目标

前端不仅要展示页面，更要清晰承载系统工作流：

- 用户侧：问答、简历分析、面经查询；
- 管理员侧：采集任务、候选审核、正式发布；
- 页面必须体现任务状态、错误状态和结果追踪。

---

# 2. 技术栈

- Vue 3
- TypeScript
- Vite
- Pinia
- Vue Router
- Element Plus 或 Naive UI（二选一，推荐统一后不要混用）
- Axios
- SSE 客户端
- ECharts（后续统计页可用）
- pnpm

---

# 3. 推荐目录结构

```text
apps/web/
├── package.json
├── vite.config.ts
├── src/
│   ├── main.ts
│   ├── App.vue
│   ├── router/
│   │   └── index.ts
│   ├── stores/
│   │   ├── auth.ts
│   │   ├── chat.ts
│   │   └── admin.ts
│   ├── api/
│   │   ├── client.ts
│   │   ├── auth.ts
│   │   ├── rag.ts
│   │   ├── resume.ts
│   │   ├── experience.ts
│   │   └── admin.ts
│   ├── layouts/
│   │   ├── PublicLayout.vue
│   │   ├── UserLayout.vue
│   │   └── AdminLayout.vue
│   ├── pages/
│   │   ├── LoginPage.vue
│   │   ├── RegisterPage.vue
│   │   ├── DashboardPage.vue
│   │   ├── QaPage.vue
│   │   ├── ResumePage.vue
│   │   ├── ResumeReportPage.vue
│   │   ├── ExperienceSearchPage.vue
│   │   ├── HistoryPage.vue
│   │   └── admin/
│   │       ├── AdminDashboardPage.vue
│   │       ├── CrawlTaskCreatePage.vue
│   │       ├── CrawlTaskListPage.vue
│   │       ├── CrawlTaskDetailPage.vue
│   │       ├── CandidateReviewListPage.vue
│   │       └── CandidateReviewDetailPage.vue
│   ├── components/
│   ├── composables/
│   ├── types/
│   └── utils/
```

---

# 4. 路由设计

```text
/login
/register
/dashboard

/qa
/resume
/resume/reports/:id
/experience
/history
/favorites

/admin
/admin/crawl-tasks
/admin/crawl-tasks/create
/admin/crawl-tasks/:id
/admin/candidates
/admin/candidates/:id
```

---

# 5. Layout 设计

## 5.1 PublicLayout

用于：

- 登录
- 注册

## 5.2 UserLayout

用于：

- Dashboard
- QA
- Resume
- Experience
- History

## 5.3 AdminLayout

用于：

- 管理员后台
- 左侧菜单
- 顶部身份区

---

# 6. 页面设计

## 6.1 Dashboard

展示：

- 三大功能入口卡片
  - 知识问答
  - 简历模拟面试
  - 最新面经库
- 最近会话
- 最近报告
- 面经库简要统计（后续）

---

## 6.2 QA 页面

布局建议：

```text
左侧：会话列表
右侧：聊天区
底部：输入框
消息中：引用片段折叠展示
```

功能：

- 新建会话；
- SSE 流式回答；
- 暂停生成（可后续）；
- 引用来源；
- 错误提示；
- 空状态引导。

---

## 6.3 Resume 页面

功能：

- 上传 PDF / DOCX；
- 上传成功后创建分析任务；
- 展示分析进度；
- 完成后跳转报告详情页；
- 失败时支持查看失败原因。

---

## 6.4 Resume Report 页面

展示：

- 简历摘要；
- 技能栈；
- 项目列表；
- 高频追问；
- 每个问题：
  - 问题
  - 回答要点
  - 参考答案
  - 继续追问

---

## 6.5 Experience Search 页面

顶部：

- 查询输入框
- 筛选：
  - 公司
  - 岗位
  - 平台
  - 时间
  - 可信度

结果区：

- 总结卡片
- 高频问题聚合
- 面经条目列表
- 每条内容展示：
  - 标题
  - 公司/岗位/轮次
  - 发布时间
  - 平台
  - 可信度分与解释
  - 摘要
  - 详情入口

重要约束：

- 页面文案不得暗示“正在实时抓全网”；
- 应表述为“查询当前面经库”。

---

# 7. 管理后台设计

## 7.1 采集任务创建页

字段：

- 任务名
- 平台勾选
- 关键词列表
- 每个平台最大采集数
- 下载图片开关
- 下载视频开关
- OCR 开关
- ASR 开关
- LLM 抽取开关
- 是否完成后进入候选池

---

## 7.2 采集任务列表页

展示：

- 任务名
- 创建人
- 状态
- 平台
- 创建时间
- 已发现数量
- 已抓取数量
- 候选数量
- 失败数量

---

## 7.3 采集任务详情页

展示：

- 任务配置
- 阶段统计
- 最近日志
- 失败条目
- 可重试操作

---

## 7.4 Candidate 审核页

列表筛选：

- 平台
- 评分区间
- 是否疑似营销
- 处理状态
- 审核状态

详情页展示：

- 原始链接
- 原始文本
- OCR 文本
- ASR 文本
- 融合文本
- AI 抽取结果
- 评分解释
- 去重信息
- 发布 / 拒绝 / 重跑按钮

---

# 8. 前端状态管理

建议 Pinia 管理：

## auth store

- token
- currentUser
- isAdmin

## chat store

- 当前会话
- 会话列表
- SSE 状态

## admin store

- 当前任务筛选
- 审核页筛选
- 缓存基础字典

---

# 9. API Client 规范

统一 `src/api/client.ts`：

- token 注入；
- 401 统一处理；
- 标准错误消息；
- baseURL 从 env 读取。

---

# 10. 交互原则

- 长任务必须给状态；
- 失败必须可见；
- 空页面必须有说明；
- AI 输出区分“系统生成”和“原始资料”；
- 管理员操作需要二次确认：
  - 发布
  - 拒绝
  - 下架

---

# 11. 前端 Phase 顺序

1. 工程初始化
2. 登录注册
3. Dashboard
4. QA 页面
5. Resume 上传与报告页
6. Experience Search 页面
7. Admin 任务管理
8. Admin 审核页
9. 统计与优化

---

# 12. 验收清单

- 路由完整；
- 登录态正常；
- 管理员路由受保护；
- QA SSE 可用；
- 简历任务状态可见；
- 面经查询不出现实时抓取误导；
- 管理后台可完成审核发布主流程；
- 页面 loading/error/empty 状态齐全。
