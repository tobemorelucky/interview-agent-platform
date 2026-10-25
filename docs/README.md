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
- [V1.4.1 真实接口验收（含面经失败记录）](47_deepseek_web_real_acceptance_v1_4_1.md)
- [V1.4.2 面经证据锚定、Fetch 与域名约束（含真实验收限制）](48_interview_experience_evidence_v1_4_2.md)
- [V2.1 目标驱动自适应面试（离线/浏览器通过，真实模型题源校验失败）](49_goal_driven_adaptive_interviewer_v2_1.md)
- [V2.1.1 Director题源组合契约修复（集成/Replay及6请求Live通过，完整多轮待验收）](50_director_source_contract_v2_1_1.md)
- [V2.2 能力记忆、复测与自适应面试联动（隔离库/Replay验收）](51_capability_memory_v2_2.md)
- [V2.2 Live 准备与 0026 迁移审查（等待分别授权）](52_v22_live_preparation_and_migration_review.md)
- [V2.2 DeepSeek Live 记忆联动验收（Replay 历史 + Live 决策）](53_v22_live_memory_acceptance.md)
- [V2.1/V2.2 可重复演示：启动与7分钟路径](54_repeatable_demo_guide.md)
- [V2.1/V2.2 秋招源码定位与真实机制](interview/09_v21_v22_source_map.md)
- [秋招口述稿与24个技术追问](interview/10_v21_v22_interview_materials.md)
- [正式库0026发布步骤与授权检查清单（本轮未执行）](57_production_0026_release_checklist.md)
