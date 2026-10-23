# ParadeDB 中文检索验证脚本（X09）

在 P1-01 的开发数据库容器上，验证知识库技术方案 11.3、11.4、12.1、12.2 写的
建表、建索引和查询写法在锁定版本（`paradedb/paradedb:0.26.2-pg16`）上可用，
并输出实际可用的 SQL 与性能数字。第三期实现检索时参考同目录的 `REPORT.md`。

## 运行

先起开发容器（`deploy-postgres-1`，端口 55432），再跑脚本：

```bash
make dev-up paradedb-check
```

脚本用迁移账号 `adp_migrator` 在 `adp_test` 里建临时 schema `x09_check`，
结束时（含出错时）`DROP SCHEMA ... CASCADE` 删除，不碰其他表、容器和数据卷。

追加性能冒烟（10 万条，约几分钟；运行前根分区剩余空间低于 3 GiB 会自动跳过）：

```bash
make dev-up paradedb-check PARADEDB_CHECK_ARGS="--rows 100000"
```

也可以不经 Makefile 直接跑（脚本头用 PEP 723 声明了 `psycopg[binary]`）：

```bash
uv run scripts/paradedb_check/check.py [--rows 100000]
```

## 连接参数

默认连 `localhost:55432`、库 `adp_test`、账号 `adp_migrator`（密码 `adp_migrator_pw`）。
可用环境变量覆盖：`PARADEDB_HOST`、`PARADEDB_PORT`、`PARADEDB_DB`、`PARADEDB_USER`、
`PARADEDB_PASSWORD`，或直接给 `PARADEDB_DSN`。报告里的镜像 tag 用 `PARADEDB_IMAGE` 覆盖。

## 输出

- 标准输出：每项 `[PASS]/[FAIL]` 与摘要，最后是总体结果；有 `[FAIL]` 时退出码为 1。
- `REPORT.md`：镜像 tag、扩展版本、逐项结果与可用的 SQL、性能数字、与知识库技术方案写法的差异。

## 覆盖的验证项

1. 扩展 `vector`、`pg_search` 已安装及版本。
2. 建索引表（`vector(1024)`；另建一张 `halfvec(2560)`）。
3. HNSW 索引（`vector_cosine_ops` / `halfvec_cosine_ops`）。
4. BM25 索引（`title`/`body`/`keywords` 用 `pdb.jieba`，带 `entry_type`、`doc_id`、`active`）。
5. 插入约 30 条中文样例（制度、报销、差旅，含“长江大桥”等长词，部分 `active=false`）。
6. 全文召回、搜索模式拆词、查询分词、过滤下推（`EXPLAIN`）、MVCC、向量召回
   （`hnsw.ef_search`、`hnsw.iterative_scan`）、关键词分（`strpos`）。
7. 可选性能冒烟：10 万条随机向量和中文文本，向量/全文召回各 100 次的 P50、P95。

## 注意

- 只验证，不改知识库技术方案；发现的写法差异记在 `REPORT.md` 和任务卡 X09 的完成记录里。
- 性能冒烟会占用数百 MB 磁盘和几分钟时间，跑完脚本会删除临时 schema 回收空间。
