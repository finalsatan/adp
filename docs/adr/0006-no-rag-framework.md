# 不引入 RAG 框架，入库和检索流程自己编排，每一步用成熟库

早先的开源复用调研曾建议用 Haystack 的管线搭建入库和检索。

知识库技术方案评估后，2026-10-22 决定不引入 RAG 框架（Haystack、LlamaIndex、LangChain 的管线），流程由我们自己编排，每一步直接调用成熟库：
- 解析：Docling、MinerU
- 分块：docling-core、langchain-text-splitters（只用这一个包）、Chonkie
- 检索：pgvector、ParadeDB

原因有两点：
- Dify、RAGFlow、WeKnora、FastGPT 的核心流程都是自己写的。
- 我们需要的能力框架帮不上忙，反而多一层抽象。这些能力是：每一步都持久化、失败后从失败的那一步继续、逐步显示进度、命中测试逐步展示中间结果、按知识库隔离存储，以及按 RAGFlow 方式计算有绝对含义的融合分。

**Considered Options**：Haystack 管线。它的 PgvectorDocumentStore 是单表，关键词检索不支持中文，大部分组件要自定义。LlamaIndex、LangChain 的管线也有同样的问题。

**Consequences**：流水线调度、重试、两代切换、检索融合这些编排代码由我们自己维护和测试。以后如果想用某个框架里的单个组件，只引入那一个组件，不引入它的管线。
