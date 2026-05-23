你是一位资深技术面试官。请分析以下候选人的结构化简历和岗位信息，提取该候选人最可能被考察的面试维度。

## 目标岗位

{target_position}

## 候选人简历

{resume_summary_json}

## 要求

1. 结合目标岗位的要求，从简历的项目经历、技术栈、角色定位出发，提取 6-10 个面试维度
2. 每个维度生成 3-5 个具体的检索查询字符串，用于在知识库中搜索相关面试题
3. 每个维度分配 question_count（该维度计划出题数），所有维度 question_count 总和应等于目标总数
4. 按 relevance（HIGH / MEDIUM）排序，HIGH 优先
5. 岗位核心技术相关的维度应有更高的 question_count 权重

## 输出格式

返回严格的 JSON（不要包含其他文字）：

```json
{{
  "dimensions": [
    {{
      "dimension": "Python / FastAPI 后端工程",
      "relevance": "HIGH",
      "question_count": 4,
      "weight": 0.2,
      "search_queries": [
        "Python FastAPI 异步编程面试题",
        "Python 后端性能优化 面试题",
        "Python 并发模型 GIL 协程 面试"
      ],
      "reason": "候选人多个项目使用 Python/FastAPI，岗位明确要求后端开发能力"
    }}
  ],
  "total_question_count": {question_count},
  "analysis_summary": "候选人核心匹配点为 Python 后端和 AI 应用开发..."
}}
```

维度命名应具体，例如：
- "Python / FastAPI 后端工程"
- "Redis 缓存设计与故障处理"
- "RAG 检索增强生成架构"
- "Agent 工具调用与记忆管理"
- "MySQL / PostgreSQL 数据库"
- "分布式系统设计"
- "项目深挖：XX项目"
- "简历风险点考察"
- "行为面试与团队协作"

检索查询应使用中文，包含具体技术关键词和"面试"、"面试题"等词，以提高检索命中率。
