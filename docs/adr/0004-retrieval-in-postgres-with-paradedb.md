# 知识库检索放在 PostgreSQL 内：ParadeDB 做 BM25，pgvector 做向量，每个知识库一张索引表

知识库需要向量检索和中文 BM25 全文检索。评估过的方案：OpenSearch（检索质量好，但多一个 Java 服务，内存占用大，还要和数据库保持同步，团队认为太重）；PostgreSQL 自带的 tsvector 加应用侧 jieba 分词（不加组件，但没有 BM25 的词频饱和和逆文档频率）；zhparser、pg_jieba（要自己编译数据库镜像）。2026-10-22 决定：数据库镜像换成 ParadeDB 官方镜像（`paradedb/paradedb:<版本>-pg16`，基于官方 postgres:16，自带 pgvector 和 pg_search）；每个知识库一张索引表，向量用 pgvector 的 HNSW 索引，全文用 pg_search 的 BM25 索引和内置 jieba 分词器。它仍是同一个 PostgreSQL，索引更新随事务提交，新旧两代分块的切换仍在一个事务里完成。混合检索的最终分数在程序里算（余弦相似度和按词权重的命中比例加权），不直接用 BM25 分数，以便分数阈值有绝对含义。

**Considered Options**：OpenSearch / Elasticsearch；tsvector + 应用侧 jieba；ParadeDB 0.25 起自带的向量索引（beta，暂不用）；单表加按维度的部分索引（WeKnora 的做法，按库过滤时召回率下降）。

**Consequences**：
- 许可证：ParadeDB 社区版是 AGPL-3.0。我们不修改它的代码，应用通过 SQL 访问；私有化交付时要随附它的许可证和源码获取方式。客户不接受 AGPL 时，可以退回 tsvector 方案（检索层留了接口），或者采购企业版。
- 高可用：社区版只支持单机，BM25 索引不会通过物理复制同步到备库。以后做高可用，要么用企业版，要么切换到备库后重建索引。
- 版本：ParadeDB 仍是 0.x 版本，语法会变（0.25 把索引从 bm25 改名为 paradedb）。镜像要锁定版本，升级后执行 `ALTER EXTENSION pg_search UPDATE`。
- 分词：它的 jieba 分词器的文档里没有自定义词典。专有名词切分不准时，退路是在应用里用 jieba 预先分好词，索引字段改用按空格切分的分词器。
