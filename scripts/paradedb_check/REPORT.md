# ParadeDB 中文检索验证报告（X09）

- 生成时间：2026-10-24 04:52 +0800
- 镜像：`paradedb/paradedb:0.26.2-pg16`
- 数据库：16.15 (Debian 16.15-1.pgdg13+2)，vector=0.8.6，pg_search=0.26.2
- 临时 schema：`x09_check`（运行结束已删除）

## 逐项验证

| # | 项 | 结果 | 摘要 |
| --- | --- | --- | --- |
| 1 | 扩展 vector、pg_search | 通过 | vector=0.8.6, pg_search=0.26.2, PostgreSQL 16.15 (Debian 16.15-1.pgdg13+2) |
| 2 | 建索引表（vector(1024) 与 halfvec(2560)） | 通过 | 已建表：kb_halfvec, kb_index |
| 3 | 建 HNSW 索引（vector_cosine_ops / halfvec_cosine_ops） | 通过 | kb_halfvec_hnsw: True；kb_hnsw: True |
| 4 | 建 BM25 索引（jieba 分词器 + entry_type/doc_id/active） | 通过 | 索引已建，含 3 个 pdb.jieba 字段与 3 个过滤列 |
| 5 | 插入中文样例 | 通过 | 插入 30 条，其中 active=false 4 条；另插入 1 条 2560 维 halfvec 记录 |
| 6a | 全文召回（boost + active/doc_id 过滤 + pdb.score 排序） | 通过 | 召回 5 条；首条 id=3，score=16.1391 |
| 6b | 搜索模式拆词：搜“大桥”召回“长江大桥” | 通过 | 搜“大桥”召回 6 条，含“长江大桥”的条目：True |
| 6c | 查询分词（search_mode=false 返回原词） | 通过 | '南京市长江大桥附近的报销标准' -> ['南京市', '长江大桥', '附近', '的', '报销', '标准'] |
| 6d | 过滤在 BM25 索引内完成（EXPLAIN 下推） | 通过 | Tantivy Query 中出现的字段：['active', 'body', 'doc_id', 'title'] |
| 6e | MVCC：BM25 索引随事务提交生效 | 通过 | 未提交时另一连接搜到 0 条，提交后搜到 1 条 |
| 6f | 向量召回（<=> + hnsw.ef_search + hnsw.iterative_scan） | 通过 | ef_search=20 召回 5 条；带 active 过滤时 iterative_scan off=0、relaxed_order=10（小表强制 enable_seqscan=off 走 HNSW） |
| 6g | 关键词分（strpos 判断查询词命中） | 通过 | 返回 5 条；首条 keyword_score=1.0 |
| 7 | 性能冒烟 | 通过 | 100000 行：插入 7.1s；向量 P50/P95=0.73/1.02ms；全文 P50/P95=12.49/17.68ms |
| 8 | 清理临时 schema | 通过 | schema x09_check 已删除 |

## 实际可用的 SQL

### 1. 扩展 vector、pg_search

- 结果：通过
- 摘要：vector=0.8.6, pg_search=0.26.2, PostgreSQL 16.15 (Debian 16.15-1.pgdg13+2)

```sql
SELECT extname, extversion FROM pg_extension WHERE extname IN ('vector','pg_search')
```

### 2. 建索引表（vector(1024) 与 halfvec(2560)）

- 结果：通过
- 摘要：已建表：kb_halfvec, kb_index

```sql
CREATE TABLE x09_check.kb_index (
    id bigserial PRIMARY KEY,
    entry_type text NOT NULL,
    chunk_id bigint,
    qa_id bigint,
    doc_id bigint,
    generation int NOT NULL,
    active boolean NOT NULL,
    embedding vector(1024),
    title text,
    body text NOT NULL,
    keywords text
)

CREATE TABLE x09_check.kb_halfvec (
    id bigserial PRIMARY KEY,
    active boolean NOT NULL,
    embedding halfvec(2560)
)
```

### 3. 建 HNSW 索引（vector_cosine_ops / halfvec_cosine_ops）

- 结果：通过
- 摘要：kb_halfvec_hnsw: True；kb_hnsw: True

```sql
CREATE INDEX kb_hnsw ON x09_check.kb_index USING hnsw (embedding vector_cosine_ops)
CREATE INDEX kb_halfvec_hnsw ON x09_check.kb_halfvec USING hnsw (embedding halfvec_cosine_ops)
```

<details><summary>输出</summary>

```
CREATE INDEX kb_halfvec_hnsw ON x09_check.kb_halfvec USING hnsw (embedding halfvec_cosine_ops)
CREATE INDEX kb_hnsw ON x09_check.kb_index USING hnsw (embedding vector_cosine_ops)
```
</details>

### 4. 建 BM25 索引（jieba 分词器 + entry_type/doc_id/active）

- 结果：通过
- 摘要：索引已建，含 3 个 pdb.jieba 字段与 3 个过滤列

```sql
CREATE INDEX kb_bm25 ON x09_check.kb_index USING paradedb (
    id,
    (title::pdb.jieba),
    (body::pdb.jieba),
    (keywords::pdb.jieba),
    entry_type,
    doc_id,
    active
)
```

<details><summary>输出</summary>

```
CREATE INDEX kb_bm25 ON x09_check.kb_index USING paradedb (id, ((title)::pdb.jieba), ((body)::pdb.jieba), ((keywords)::pdb.jieba), entry_type, doc_id, active)
```
</details>

### 5. 插入中文样例

- 结果：通过
- 摘要：插入 30 条，其中 active=false 4 条；另插入 1 条 2560 维 halfvec 记录

```sql
INSERT INTO kb_index (...) VALUES (...);  -- 见脚本 sample_rows()
```

### 6a. 全文召回（boost + active/doc_id 过滤 + pdb.score 排序）

- 结果：通过
- 摘要：召回 5 条；首条 id=3，score=16.1391

```sql
SELECT id, entry_type, doc_id, title, pdb.score(id) AS score
FROM x09_check.kb_index
WHERE (title ||| %(q)s::text::pdb.boost(2) OR body ||| %(q)s::text OR keywords ||| %(q)s::text::pdb.boost(2))
  AND active
  AND doc_id = %(doc_id)s
ORDER BY pdb.score(id) DESC
LIMIT 5
```

<details><summary>输出</summary>

```
id=3 type=chunk doc=1 title=差旅费报销制度 score=16.1391
id=4 type=chunk doc=1 title=差旅费报销制度 score=15.6336
id=1 type=chunk doc=1 title=差旅费报销制度 score=5.9526
id=5 type=chunk doc=1 title=差旅费报销制度 score=5.9307
id=2 type=chunk doc=1 title=差旅费报销制度 score=5.0041
```
</details>

### 6b. 搜索模式拆词：搜“大桥”召回“长江大桥”

- 结果：通过
- 摘要：搜“大桥”召回 6 条，含“长江大桥”的条目：True

```sql
SELECT id, body FROM x09_check.kb_index WHERE body ||| '大桥' AND active ORDER BY pdb.score(id) DESC
```

<details><summary>输出</summary>

```
id=24 维护期间长江大桥双向各封闭一条车道。
id=3 长江大桥沿线的出差补贴标准按一类地区执行。
id=15 南京市长江大桥附近的客户拜访可当天往返。
id=4 南京市长江大桥附近的酒店住宿上限为每晚五百元。
id=21 问：长江大桥过桥费能否报销？答：可以，凭通行费发票。
```
</details>

### 6c. 查询分词（search_mode=false 返回原词）

- 结果：通过
- 摘要：'南京市长江大桥附近的报销标准' -> ['南京市', '长江大桥', '附近', '的', '报销', '标准']

```sql
SELECT :q::pdb.jieba('search_mode=false')::text[]
```

### 6d. 过滤在 BM25 索引内完成（EXPLAIN 下推）

- 结果：通过
- 摘要：Tantivy Query 中出现的字段：['active', 'body', 'doc_id', 'title']

```sql
EXPLAIN (VERBOSE, COSTS OFF) SELECT id FROM x09_check.kb_index WHERE (title ||| '大桥' OR body ||| '大桥') AND active AND doc_id = 1
```

<details><summary>输出</summary>

```
Custom Scan (ParadeDB Base Scan) on x09_check.kb_index
  Output: id
  Table: kb_index
  Index: kb_bm25
  Worker Selection: Row-capped
  Exec Method: ColumnarExecState
  Columnar: id
  Scores: false
  Tantivy Query: {"boolean":{"must":[{"boolean":{"should":[{"with_index":{"query":{"match":{"field":"title","value":"大桥","tokenizer":null,"distance":null,"transposition_cost_one":null,"prefix":null,"conjunction_mode":false}}}},{"with_index":{"query":{"match":{"field":"body","value":"大桥","tokenizer":null,"distance":null,"transposition_cost_one":null,"prefix":null,"conjunction_mode":false}}}}]}},{"term":{"field":"active","value":true}},{"term":{"field":"doc_id","value":1}}]}}
Query Identifier: -7114189799335323288
```
</details>

### 6e. MVCC：BM25 索引随事务提交生效

- 结果：通过
- 摘要：未提交时另一连接搜到 0 条，提交后搜到 1 条

```sql
-- 连接 A 未提交：
INSERT INTO x09_check.kb_index (entry_type, doc_id, generation, active, body) VALUES ('chunk', 9, 1, true, 'MVCC事务可见性唯一标记词');
-- 连接 B：
SELECT count(*) FROM x09_check.kb_index WHERE body ||| '唯一标记词';
```

### 6f. 向量召回（<=> + hnsw.ef_search + hnsw.iterative_scan）

- 结果：通过
- 摘要：ef_search=20 召回 5 条；带 active 过滤时 iterative_scan off=0、relaxed_order=10（小表强制 enable_seqscan=off 走 HNSW）

```sql
SET LOCAL enable_seqscan = off;  -- 小表测试时强制走 HNSW
SET LOCAL hnsw.ef_search = 20;
SELECT id, embedding <=> :v::vector AS dist FROM x09_check.kb_vec ORDER BY embedding <=> :v::vector LIMIT 5;
-- 带过滤（过滤会排掉大部分候选）：
SET LOCAL hnsw.ef_search = 10;
SET LOCAL hnsw.iterative_scan = relaxed_order;
SELECT id FROM x09_check.kb_vec WHERE active ORDER BY embedding <=> :v::vector LIMIT 10;
```

<details><summary>输出</summary>

```
无过滤计划：
Limit  (cost=9.89..30.12 rows=5 width=16)
  ->  Index Scan using kb_vec_hnsw on kb_vec  (cost=9.89..4096.20 rows=1010 width=16)
        Order By: (embedding <=> '[<向量>]'::vector)

带过滤计划：
Limit  (cost=9.24..4093.70 rows=10 width=16)
  ->  Index Scan using kb_vec_hnsw on kb_vec  (cost=9.24..4093.70 rows=10 width=16)
        Order By: (embedding <=> '[<向量>]'::vector)
        Filter: active
```
</details>

### 6g. 关键词分（strpos 判断查询词命中）

- 结果：通过
- 摘要：返回 5 条；首条 keyword_score=1.0

```sql
WITH q AS (SELECT unnest(%(terms)s::text[]) AS term)
SELECT t.id,
       COALESCE(SUM(CASE WHEN strpos(t.title, q.term) > 0
                          OR strpos(t.body, q.term) > 0
                          OR strpos(t.keywords, q.term) > 0
                         THEN 1 ELSE 0 END), 0)::float
       / (SELECT count(*) FROM q) AS keyword_score
FROM x09_check.kb_index t
LEFT JOIN q ON true
WHERE (t.title ||| %(q)s::text OR t.body ||| %(q)s::text OR t.keywords ||| %(q)s::text)
  AND t.active
GROUP BY t.id
ORDER BY keyword_score DESC, t.id
LIMIT 5
```

<details><summary>输出</summary>

```
id=3 keyword_score=1.000
id=4 keyword_score=1.000
id=21 keyword_score=1.000
id=1 keyword_score=0.500
id=2 keyword_score=0.500
```
</details>

### 7. 性能冒烟

- 结果：通过
- 摘要：100000 行：插入 7.1s；向量 P50/P95=0.73/1.02ms；全文 P50/P95=12.49/17.68ms

### 8. 清理临时 schema

- 结果：通过
- 摘要：schema x09_check 已删除

## 性能冒烟（10 万条）

- 行数：100000，插入耗时 7.1s（含建索引前）
- 向量召回（100 次）：P50 0.73 ms，P95 1.02 ms，最大 3.51 ms
- 全文召回（100 次）：P50 12.49 ms，P95 17.68 ms，最大 32.05 ms

知识库技术方案 12.3 的目标是 100 万条以内 P95 < 500 ms；本次只做 10 万条冒烟。

## 与知识库技术方案 11.3、11.4 写法的差异

知识库技术方案 11.3、11.4 的主体写法在 ParadeDB 0.26.2 / pgvector 0.8.6 上直接可用，只有一处需要补充：参数占位符的类型（见下）。逐条实测确认的语法：

- 索引访问方法名 `USING paradedb` 可用（0.26.2 里 `bm25` 仍作为别名存在）。
- 表达式字段写法 `(title::pdb.jieba)`、`(body::pdb.jieba)`、`(keywords::pdb.jieba)` 可用，字段可空不影响建索引。
- 全文召回操作符 `|||`（match disjunction）与 `::pdb.boost(2)`、`pdb.score(id)` 可用。注意：同一个参数占位符同时用于带 `::pdb.boost(...)` 和不带 boost 的位置时，服务端参数类型推断会报 `invalid typemod -1`；需显式写 `:q::text::pdb.boost(2)`，或给每个位置用不同占位符（第三期实现注意）。
- jieba 默认开启搜索模式：`'长江大桥'::pdb.jieba` 会拆出 `长江`、`大桥`；`::pdb.jieba('search_mode=false')` 只返回原词，与 11.4 一致。
- 在 BM25 索引里带上 `entry_type`、`doc_id`、`active` 后，这些过滤条件会下推到索引（EXPLAIN 的 Tantivy Query 里出现对应 term），无需回表过滤。
- HNSW 索引 `vector_cosine_ops` / `halfvec_cosine_ops`、`ORDER BY embedding <=> :v`、`SET LOCAL hnsw.ef_search`、`SET LOCAL hnsw.iterative_scan = relaxed_order` 均可用；带过滤且候选不足时，开启 iterative_scan 能补足结果（见 6f 实测数字）。验证脚本在向量用例里加了 `SET LOCAL enable_seqscan = off`：数据量小时规划器会选 Seq Scan，看不出 HNSW 迭代扫描的效果；线上 100 万条会自然选 HNSW。
- 关键词分的 `strpos` 写法可用（12.2）。

## 磁盘占用

- start：`/dev/vda2        59G   49G  7.9G  87% /`
- start_free_gb：`7.9 GiB`
- end：`/dev/vda2        59G   50G  7.5G  87% /`
- end_free_gb：`7.4 GiB`

临时 schema 在测量 end 前已删除，表文件已回收。若 end 比 start 少几百 MB，通常是 PostgreSQL 保留的 WAL 段（`max_wal_size` 默认 1GB）：插入 10 万条会产生大量 WAL，不是临时表残留。
