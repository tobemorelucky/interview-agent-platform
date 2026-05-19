# 17 Phase 0 Claude Code 启动指令

## 1. 什么时候使用

当你已经：

- 创建好仓库；
- 将正式文档包放入仓库；
- 将此前的协同开发总方案放入 `docs/00_claude_code_collaboration_plan.md`；
- 准备开始让 Claude Code 写第一批代码；

就可以将下面的指令发给 Claude Code。

---

# 2. 可直接复制的 Phase 0 指令

```text
请先阅读以下文件，并严格以这些文件为依据：
- CLAUDE.md
- docs/00_claude_code_collaboration_plan.md
- docs/01_prd.md
- docs/02_architecture.md
- docs/03_backend_design.md
- docs/04_frontend_design.md
- docs/14_local_development_and_deployment.md
- docs/15_roadmap.md

当前只做 Phase 0：工程骨架与基础设施准备。

本阶段目标：
1. 建立项目 Monorepo 基础目录结构；
2. 初始化 apps/api、apps/web、apps/worker；
3. 后端 Python 依赖管理统一使用 uv；
4. 前端依赖管理统一使用 pnpm；
5. 创建 docker-compose.yml，至少包含：
   - PostgreSQL
   - Redis
   - Milvus Standalone
   - MinIO
6. 创建 .env.example，覆盖 API、DB、Redis、Milvus、MinIO、JWT、模型 Provider 的基础环境变量；
7. API 服务提供最小健康检查接口；
8. Worker 服务提供最小可启动骨架；
9. 前端提供最小欢迎页；
10. README 写明本地启动流程：
    - docker compose up -d
    - API 如何用 uv 启动
    - Worker 如何启动
    - Web 如何用 pnpm 启动
11. 预留 scripts/ 目录，并可先创建占位说明，不要实现后续业务脚本。

强约束：
- 不要实现登录注册；
- 不要实现 RAG；
- 不要实现简历分析；
- 不要实现真实爬虫；
- 不要实现数据库业务表；
- 不要提前实现 Milvus collections；
- 不要擅自替换技术栈；
- 不要引入 poetry、pipenv、conda 作为项目标准；
- 不要把 API、Worker、Frontend 混成一个项目。

工作方式：
1. 先扫描当前仓库；
2. 先输出详细执行计划；
3. 列出预计创建/修改的文件；
4. 列出你认为的风险点和需要注意的假设；
5. 等我确认后再开始修改代码。
```

---

# 3. Claude Code 输出计划后，你要做什么

不要直接同意。  
把 Claude Code 的 Plan 发回 ChatGPT，让我帮你审查：

- 是否漏了文件；
- 是否超出 Phase 0；
- 是否技术栈偏离；
- 是否 Docker 方案有坑；
- 是否虚拟环境方案正确；
- 是否目录结构合理。

---

# 4. Plan 通过后的执行指令

如果计划没问题，再发：

```text
计划通过。请严格按刚才的计划实施 Phase 0。
实施完成后请汇报：
1. 完成了什么；
2. 修改了哪些文件；
3. 运行了哪些命令；
4. 哪些命令成功；
5. 是否有失败或未完成；
6. 当前如何本地启动；
7. 下一步进入 Phase 1 前还缺什么。
```

---

# 5. Phase 0 完成后的验收

你需要至少验证：

```text
docker compose up -d
```

能启动基础设施。

然后验证：

- API 健康检查接口；
- Worker 能启动；
- 前端欢迎页能打开；
- README 步骤能照着执行。

---

# 6. Phase 0 完成后下一步

Phase 0 通过后：

1. 将 Claude Code 输出的变更总结发回 ChatGPT；
2. 我帮你做一次 Phase 0 架构审查；
3. 再生成 Phase 1 的最终执行指令；
4. 启动登录注册和权限系统开发。
