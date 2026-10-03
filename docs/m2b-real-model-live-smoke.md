# M2B Real Model Live Smoke（2026-10-01）

## 结果：FAILED，未进入 M3

本次只执行一个最小 Live smoke。官方页面目标为 Canonical 招聘详情页；
从当前配置的本地 SearXNG 查询发现 URL，不手工注入搜索结果或绕过 Gateway。

执行命令：

```powershell
cd apps/api
$env:DATABASE_URL='postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/interview_agent_m2b_real_smoke_20261001'
uv run alembic upgrade head
uv run python scripts/smoke_public_research_real_model.py --database-url $env:DATABASE_URL --proxy 'http://127.0.0.1:7890'
```

数据库是本次单独创建的隔离 smoke 库；迁移到既有 0020，没有改动业务库。

## 具体失败节点

- 查询：`site:canonical.com/careers/ Python Engineer`。
- SearXNG：`http://localhost:8080`，使用当前引擎配置（未覆盖为 Replay）。
- RefreshTask：`1`，target `JOB_JD`，最终状态 `FAILED`。
- ToolInvocation：`1`，`public.search`，`LIVE`，`SUCCEEDED`，实际结果 `[]`。
- Smoke 节点：`official_job_selection`。
- 错误：`no_official_job_search_result: real search returned no matching official job detail URL`。
- Script exit code：`1`。

这是搜索没有发现可采集的官方招聘 URL，不是模型抽取或 Evidence validator
失败。没有进行 Fetch、模型调用、Claim/Evidence 或 JD 持久化。

当前真实模型配置检查只读取非敏感信息：Provider `openai_compatible`；
endpoint host `ark.cn-beijing.volces.com`；extract / quality model 均为
`doubao-seed-2-0-pro-260215`；密钥已配置。因上游搜索节点未通过，没有调用模型，
因此不能宣称本次验证了模型可达性或真实抽取。

## 保护边界

未使用 Replay、未替换模型、未修改 Prompt、未降低 validator、未手工捏造
SourceSnapshot 或证据。保留全部入场已有未提交修改；JobPilot 保持只读。
只新增 smoke 脚本与本记录，没有新增迁移或 M3 业务实现。

只读检查当前 SearXNG 日志：DuckDuckGo / Startpage 遇到 CAPTCHA；Brave
报告 Too many request。它们说明当前上游搜索可用性受限，不构成降低网页安全
或 Evidence 校验的理由。没有重试其他 provider，也没有更改引擎配置。

数据库核查：SourceSnapshot、Claim、ClaimEvidence、JobDescriptionVersion、
JobRequirement、AgentRun 均为 0 行。新增 smoke 脚本 compileall 与 diff 检查通过；
本次未执行 M3 测试，也不把前次 M2B Replay 验收当作此次真实模型验证。

下一步需先恢复 SearXNG 对官方招聘页的有效搜索结果，然后重跑同一 Live 链路。
只有完整到达 COMPLETED 并核验真实 Evidence 后，才满足进入 M3 的前置条件。
