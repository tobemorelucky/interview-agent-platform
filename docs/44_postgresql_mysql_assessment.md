# 当前项目 PostgreSQL → MySQL 8.0 评估（不实施迁移）

结论：**保留 PostgreSQL**。这不是认为 MySQL 不能承载业务，而是当前正确性保障与回归已依赖 PG；迁移不会增加秋招用户能力，成本明显高于切换连接串。本轮未创建 MySQL 容器、未转换数据、未为 ORM 大规模加方言抽象。

## 真实依赖清单

| 现有位置/机制 | 迁 MySQL 时的实际工作 |
| --- | --- |
| `modules/job_matching/retrieval.py` 的 JSONB cast、jsonb_typeof/array_length/array_elements(_text)、table_valued、相关子查询 | 重写 JSON_TABLE / JSON_EXTRACT 等查询，保持缺失/错误类型保持 UNKNOWN，不把空/NULL 猜为满足 |
| Profile evidence/agent、manual importer、Research、ToolGateway 的 PG insert + ON CONFLICT，及新增 admission | 更换方言 upsert，逐条核实唯一键冲突语义、冲突后的 SELECT/锁与并发可见性，不用无条件覆盖替代复用 |
| Report 模型和 0023 的 `postgresql_where=is_current` partial unique | 设计可空 generated key 唯一约束或 current pointer 表等替代，再验证旧 current 先保留、成功后原子切换 |
| 0021：历史不可变、current pointer/新版本完整性触发器 | 用 MySQL trigger 语法逐表重写，不能只迁数据而丢掉保护 |
| 0022：Snapshot 不可变、Session context/公共引用删除保护 | 重写 JSON 遍历、触发器和引用校验；旧快照内容必须逐项比对 |
| 0023：Report、Observation、Proposal/support ownership/provenance 触发器，jsonb contains / to_jsonb 比较 | 重写跨表保护及并发锁；不能以应用层“应该不会发生”代替 DB 约束 |
| 0024：MatchRun/子记录冻结，to_jsonb 去除允许变更字段后比较 | 重写白名单更新比较、排名约束和状态转换保护 |
| Controller/Memory approval/current 切换的 SELECT FOR UPDATE、key_share，SQLSTATE 23503/23505 的错误识别 | InnoDB 能做事务行锁，但要重新验证隔离级别、锁顺序/死锁/重试、错误码；不是直接移植 PostgreSQL key_share |
| asyncpg、Alembic 全链、事务时间/sequence/FK cycle 建表、Boolean/JSON/时区、规范化名唯一性 | 驱动与 migration 方言、UTC 约定、自增序列/ID 保留、排序规则和大小写/重音差异回归 |
| migration 和数百项真实 PG 集成测试 | 新建独立 MySQL 测试矩阵和迁移桥接脚本；历史 PG migration 保留，不在原地改写 |

项目的大多数 ORM JSON 字段使用通用 SQLAlchemy JSON，**不是所有列都原生 JSONB**；依赖集中在运行查询的 JSONB cast 和触发器内的转换/操作。当前没有查到项目以 GIN 或 PostgreSQL full-text search 作为这些主链路的依赖，不把未使用能力当迁移难点。

## 哪些内容本身不绑定数据库

ResumeEvidence 的稳定 key/hash/offset、Evidence alias/resolver、Skill manifest/version/fingerprint、ContextSnapshot 的 JSON 内容、工具结果、五维评分定义均由 Python 计算，不依赖 PG 生成。它们**保存的表示**可迁，但目前防止历史篡改、错误归属、重复发布和并发覆盖的**约束实现**依赖 PG，必须迁移其语义。

公共/私有分离在两种数据库都可以采用共享公共表 + 用户私有表/owner FK + 服务端授权；不需要每用户复制 Company/JD，也不需要每用户一个数据库。不能把换库当作解决鉴权的方法。

## 实际取舍

- 开发便利：用户熟悉 MySQL 是真实学习优势；但这个仓库已有 PG 迁移/运行/故障记录，保留更方便复现和解释。
- 并发：两者都支持事务与行锁；不能无压测宣称谁有更高 QPS。PG 默认 Read Committed，InnoDB 默认 Repeatable Read，当前依赖短事务与锁后最新状态的逻辑必须重新验证，而非沿用默认值假定相同。[PG 隔离说明](https://www.postgresql.org/docs/current/transaction-iso.html)，[InnoDB 隔离说明](https://dev.mysql.com/doc/refman/8.0/en/innodb-transaction-isolation-levels.html)。
- JSON：当前 PG 查询可保持不动；MySQL 有 JSON_TABLE，但语法与 null/type/数组处理需重新测试。[MySQL JSON_TABLE](https://dev.mysql.com/doc/refman/8.0/en/json-table-functions.html)。
- 数据完整性：PG 当前已落地 partial unique + PL/pgSQL guards；MySQL 需要等价设计，不是不能实现。[PG partial unique](https://www.postgresql.org/docs/current/indexes-partial.html)，[MySQL CREATE INDEX / functional key parts](https://dev.mysql.com/doc/refman/8.0/en/create-index.html)。
- 长期维护：目前维护单一 PG 最少成本；没有组织级 MySQL 基础设施要求或真实生产运维收益，不值得双方言长期背负。

## 将来迁移的必要条件与估算

只有明确部署环境必须 MySQL，或有可验证维护收益，才单独立项。以当前范围估算约 **8–15 个专注工作日**：清点/设计 1–2 天、查询/upsert/trigger/驱动迁移 3–5 天、数据桥接和 ID/时区/JSON/约束校验 2–3 天、全量/并发/浏览器及恢复演练 2–5 天。是工程估算，不是完成承诺，也不包含未知生产数据清洗。

前置验收必须包括：不改原 PG 历史 migrations；原库备份/可回切；空库与有历史数据迁移；全部 FK/唯一/不可变/ownership 负例；同时审批/答题/配额并发；旧 Snapshot/Evidence/hash/工具结果完整一致；全部既有测试不减语义；新旧报告与 Memory 审批可复现。在此之前不推荐迁移。
