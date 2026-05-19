# 11 Prompt 规范设计

## 1. 目标

本项目涉及多个 LLM 任务。Prompt 必须：

- 可版本化；
- 可评估；
- 可回溯；
- 与业务逻辑解耦；
- 尽量结构化输出。

建议 Prompt 模板放在：

```text
packages/prompt_templates/
```

---

# 2. Prompt 分类

1. 知识库问答 Prompt
2. 简历结构化 Prompt
3. 简历追问生成 Prompt
4. 简历答案生成 Prompt
5. 面经有效性分类 Prompt
6. 面经结构化抽取 Prompt
7. 可信度 AI 评分 Prompt
8. ASR + OCR 融合文本整理 Prompt
9. 面经聚合总结 Prompt

---

# 3. 知识库问答 Prompt

## 3.1 输入

- user question
- retrieved chunks
- citation IDs

## 3.2 输出

- 直接回答
- 必要时分点
- 不确定时明确说明
- 基于检索内容回答

## 3.3 要求

- 不夸大；
- 不引用不存在的来源；
- 回答面向面试准备；
- 可补充“面试时怎么说”。

---

# 4. 简历结构化 Prompt

## 4.1 目标

将简历文本转为结构化 JSON：

```json
{
  "basic_info": {},
  "education": [],
  "skills": [],
  "projects": [],
  "internships": [],
  "awards": []
}
```

## 4.2 要求

- 不编造；
- 字段缺失给空数组/空值；
- 保留原文可追踪片段可后续扩展。

---

# 5. 简历追问生成 Prompt

## 5.1 目标

围绕项目和技能生成：

- 基础理解题
- 技术深挖题
- 工程架构题
- 真实性追问题
- 压力式追问

## 5.2 输出结构

```json
{
  "questions": [
    {
      "category": "project_depth",
      "question": "...",
      "why_ask": "...",
      "risk_level": "HIGH"
    }
  ]
}
```

---

# 6. 简历答案生成 Prompt

输出：

```json
{
  "answer_points": [],
  "sample_answer": "...",
  "follow_up_questions": []
}
```

要求：

- 答案贴合简历；
- 不要写成空泛教科书；
- 面试表达自然；
- 不确定处标注需要用户根据真实经历补充。

---

# 7. 面经有效性分类 Prompt

目标：

判断内容是否与“招聘面试经验”相关。

输出：

```json
{
  "is_interview_experience": true,
  "confidence": 0.94,
  "reason": "..."
}
```

---

# 8. 面经结构化抽取 Prompt

输入：

- fused_text
- platform metadata

输出：

```json
{
  "company": "",
  "position": "",
  "stage": "",
  "experience_summary": "",
  "questions": [
    {
      "raw_question": "",
      "canonical_question": "",
      "answer_clue": "",
      "question_type": "",
      "confidence": 0.0
    }
  ],
  "marketing_signals": [],
  "uncertain_fields": []
}
```

要求：

- 不将泛化内容强行抽成具体公司；
- 面试轮次不明则置空；
- 问题数量可为 0；
- 需要区分真实题目与作者给的建议性题目。

---

# 9. 可信度 AI 评分 Prompt

LLM 不直接负责最终总分，而输出特征评分：

```json
{
  "specificity_score": 0.0,
  "realism_score": 0.0,
  "marketing_risk_score": 0.0,
  "content_completeness_score": 0.0,
  "reasons": []
}
```

最终分由程序聚合。

---

# 10. ASR + OCR 融合 Prompt

输入：

- ASR transcript
- OCR text timeline / merged text
- metadata

输出：

```json
{
  "fused_text": "",
  "likely_questions": [],
  "notes": []
}
```

目标：

- 去掉明显重复；
- 保留关键信息；
- 不抹掉题目列表；
- 不虚构视频未说的内容。

---

# 11. 面经聚合总结 Prompt

用户查询正式面经库后，对若干已检索内容做总结。

输出：

- 查询结论
- 高频问题
- 相同点
- 差异点
- 代表性来源
- 可信度注意事项

要求：

- 只基于已检索内容；
- 不暗示系统实时搜索外网；
- 不把单条来源说成普遍趋势。

---

# 12. Prompt 版本化

建议每个 Prompt：

- 有文件名；
- 有版本号；
- 在任务记录中保存使用版本。

示例：

```text
resume_question_generation_v1.md
experience_structured_extract_v1.md
reliability_ai_score_v1.md
```

---

# 13. Prompt 评测建议

- 建立小样本 golden set；
- 修改 prompt 后跑回归；
- 对结构化 JSON 校验；
- 记录失败案例。

---

# 14. Prompt 验收

- Prompt 文件独立；
- 结构化任务优先返回 JSON；
- 业务层可记录 prompt version；
- 文本生成与事实抽取明确区分。
