# 5–8 分钟面试展示指南

本指南使用 **DEMO fixture + Replay 回放模型**，不调用 Live Search 或真实模型。
六 Agent 的真实 DeepSeek 验收属于 F2 历史事实，见 25 文档；现场不冒称 Live 推理。

## 启动与账号

按 README 准备依赖/.env，然后在根目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-interview-platform.ps1 -Demo
```

打开 http://127.0.0.1:5174。首次 seed 一般账号是 `m6-demo-1@example.com`，
公开 fixture 密码 `M6ReplayFixtureDemo!`（不是正式管理员密码）。
已有 seed 不重置；如 id 不为 1，可在专用 DEMO 库查询 `m6-demo-*@example.com`，不要查询或改正式用户。
所有演示岗位、简历和回答均为构造材料。顶部应有 DEMO 横幅，记录显示 Replay。

## 展示顺序

1. 0:00–0:40：登录与首页，说明简历 → 画像 → 岗位 → 面试 → 报告 → 成长记录。
2. 0:40–1:30：我的简历 / 我的画像，展开“查看依据”，说明事实可追溯、版本固定，不展示内部 ID。
3. 1:30–2:30：岗位推荐，打开已有历史 Run；展示满足、UNKNOWN、明确不满足的分层，以及“没证据不等于不会”。
4. 2:30–4:30：选择 AI 应用岗位开始短面试，输入 1–2 个明确 DEMO 回答，展开五维评价和下一步。
   Replay 当前固定返回 NEXT，不假称现场发生了 FOLLOW_UP；补问/深挖预算语义可解释，并指向 M4 测试。
5. 4:30–6:00：打开已有已完成报告；若全新库尚无报告，可在展示前完成 6 轮并生成 Replay 报告。
   本场数据与 Coach 分析分别展示；逐题复盘可展开。现场不必完成全部六轮。
6. 6:00–7:00：展示长期记忆建议与来源，说明只有用户批准/修改后批准才写 Memory。
   若已有建议已批准，展示成长记录和审批来源；要演示待处理建议，可预先完成另一场 Replay 面试。
7. 7:00–8:00：说明确定性统计、Evidence ID resolver、事务与快照冻结；展示文档中的 Real/Replay 验收边界。

建议固定回答：`DEMO 回答：我用 Redis 缓存减少重复查询，写入时处理失效，并考虑 TTL、幂等和并发边界。`
不要把它作为真人用户面试记录。

## 停止

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/stop-interview-platform.ps1 -Demo
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/status-interview-platform.ps1 -Demo
```

默认保留 PostgreSQL/Redis/MinIO 与演示数据。完整非破坏停止加 `-StopInfrastructure`。
不要删除 volume 或重建库；不要将 `.env`、日志、PID、备份或真实简历带到演示提交。
