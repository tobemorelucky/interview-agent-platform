# 文档索引

最终产品：面向秋招的多 Agent 求职与模拟面试系统。
历史 Phase 文档保留审计事实；当前行为优先查验已完成的 M1–M6 与 F0–F4 文档，
不要照旧文档执行端口强杀或 `down -v`。

- [项目概览与启动](../README.md)
- [历史架构](02_architecture.md)；最终组件与 Agent 职责边界见根 README 的架构图。
- [公共 Career Data schema](05_database_design.md)、[M2 Research](20_public_data_research_m2b.md)
- [Candidate Profile / ResumeEvidence](21_candidate_profile_m3.md)
- [Interview Core / Controller / Snapshot](22_mock_interview_core_m4.md)
- [Coach / Report / Controlled Memory](23_interview_coach_m5.md)
- [Job Matching / Evidence Matrix](24_job_matching_m6.md)
- [正式库升级与运行基线](24_finalization_baseline.md)
- [F2 真实模型验收（含旧失败）](25_real_model_acceptance.md)
- [F3 中文 UX 与浏览器验收](26_product_ux_finalization.md)
- [F4 工程交付](27_engineering_delivery.md)
- [5–8 分钟 Demo Guide](demo-guide.md)
- [本地部署历史说明与 F4 更正](14_local_development_and_deployment.md)

Real Model、Replay 和 Live Search 的验收含义不同，不相互替代。

- [V1.4 DeepSeek Web 职业资料采集与审核交付](46_deepseek_web_career_v1_4.md)
