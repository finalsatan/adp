# X09 ParadeDB 中文检索验证脚本

状态：待开始
前置：P1-01（用它的开发数据库容器）
被依赖：第三期检索实现、人工实测 H2

## 目标

在真实的 ParadeDB 镜像上跑通知识库技术方案第 11.3、11.4 节写的建表、建索引和查询写法，确认它们在锁定版本上可用，并给出实际可用的 SQL。知识库技术方案写这两节时只核对了官方文档，没有实际运行（知识库技术方案 4.1、19.1）。

## 依据

- 知识库技术方案 K4、K5、11.3、11.4、12.1、12.2、19.1、20（ParadeDB 0.x 版本语法变化快）
- ADR 0004

## 要做

写一个可重复运行的验证脚本 `scripts/paradedb_check/check.py`（psycopg 3，独立的 `requirements.txt` 或 uv 脚本头声明依赖），连 P1-01 的开发容器（数据库 `adp_test`，用迁移账号建临时 schema，结束时删除）。逐项验证并输出结果：

1. 扩展：`vector`、`pg_search` 已安装，记录版本。
2. 建表：照知识库技术方案 11.3 建一张索引表（向量维度 1024，另建一张 2560 维用 `halfvec` 的）。
3. 建 HNSW 索引（`vector_cosine_ops`、`halfvec_cosine_ops`）。
4. 建 BM25 索引：`title`、`body`、`keywords` 用 jieba 分词器，并带 `entry_type`、`doc_id`、`active`。**语法以 ParadeDB 0.26 官方文档为准**（知识库技术方案写的是 `USING paradedb` 和 `(body::pdb.jieba)` 这种写法），不对就查文档改，最终能用的写法记入报告。
5. 插入约 30 条中文样例（制度、报销、差旅类文字，含“长江大桥”这类长词、专有名词、带标题和关键词的条目），一部分 `active=false`。
6. 查询并打印结果：
   - 全文召回：`title ||| :q::pdb.boost(2) OR body ||| :q OR keywords ||| :q::pdb.boost(2)`，加 `active` 和 `doc_id` 过滤，按 `pdb.score(id)` 降序。
   - 搜索模式拆词：搜“大桥”能召回含“长江大桥”的条目（知识库技术方案 11.4：jieba 默认搜索模式会拆出短词）。
   - 查询分词：`SELECT :q::pdb.jieba('search_mode=false')::text[]` 返回问题的原词（知识库技术方案 11.4）。
   - 过滤在索引内完成：用 `EXPLAIN` 看 `active`、`doc_id` 条件是否下推到 BM25 索引。
   - MVCC：在未提交的事务里插入一条，另一个连接搜不到；提交后能搜到（知识库技术方案 K4：索引随事务提交生效）。
   - 向量召回：`ORDER BY embedding <=> :v LIMIT k`，`SET LOCAL hnsw.ef_search`；带过滤条件时 `SET LOCAL hnsw.iterative_scan` 的效果（知识库技术方案 11.3）。
   - 关键词分（知识库技术方案 12.2）：用 `strpos` 判断查询词是否出现在 title、body、keywords 中的 SQL 写法能跑通。
7. 可选的性能冒烟（参数 `--rows 100000`）：插入 10 万条随机向量和中文文本，分别测向量召回、全文召回各 100 次的 P50、P95（知识库技术方案 12.3 的目标：100 万条以内 P95 小于 500 毫秒，这里只做 10 万条的冒烟）。

输出 `scripts/paradedb_check/REPORT.md`：镜像 tag、扩展版本、每一项“通过 / 不通过 + 实际可用的 SQL + 结果摘要”、性能数字。与知识库技术方案写法不同的地方单独列出来。

## 文件

- 新建：`scripts/paradedb_check/check.py`、`scripts/paradedb_check/requirements.txt`（或脚本头的依赖声明）、`scripts/paradedb_check/REPORT.md`、`scripts/paradedb_check/README.md`（怎么运行）
- 修改：根目录 Makefile（`paradedb-check` 目标）

## 不做

- 不写进 backend 代码；第三期实现检索时参考本报告。

## 验收标准

- [ ] `make dev-up paradedb-check` 能重复运行，结束后不留下临时表。
- [ ] `REPORT.md` 覆盖第 6 节的每一项；有不通过的项时写清楚现象和查到的原因，**不要为了“通过”改掉验证内容**。
- [ ] 在完成记录里列出“知识库技术方案 11.3、11.4 中需要修改的写法”，供文档维护人更新知识库技术方案（本卡不改文档）。

## 问题

## 完成记录
