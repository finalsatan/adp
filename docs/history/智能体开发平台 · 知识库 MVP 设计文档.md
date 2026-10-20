# 智能体开发平台 · 知识库 MVP 设计文档

> **2026-10-10 说明：本文已被 `智能体开发平台-总体技术方案.md` 取代，正文保留作参考，未做修改。**
>
> - **仍然沿用**（已改写为 TypeScript 版并入新文档）：§6.2–6.5 统一 Model Client、限流、缓存、密钥；§7.2 index_space 向量方案；§7.5 文档状态机；§7.6 Chunk 字段；§9.2、9.4、9.5 每文档一次运行、幂等、重试、原子替换；§11 解析服务协议与 ParsedDocument；§12 进度推送；§14.1 部分 HNSW 索引；§15.3 trace 数据量控制
> - **已废弃或推迟**：面向智能体开发者的定位（改为业务人员 + 研发）；画布 Pipeline、DSL、路由节点（推迟到阶段 E）；人工审核节点（推迟到阶段 E）；Python 后端（改为 TypeScript，仅解析服务用 Python）；ParadeDB pg_search（AGPL，改为应用层 jieba 分词 + tsvector）；Phoenix（ELv2，改为自建 trace 表）；PyMuPDF（AGPL，改为 Docling）；Procrastinate（改为 pg-boss）；不做 API Key / 评测集 / 租户（均已改为从阶段 A 开始）

| 项 | 内容 |
| --- | --- |
| 文档版本 | v1.0 |
| 日期 | 2026-09-17 |
| 状态 | 设计已确认，待实施 |
| 适用范围 | MVP：模型管理 + 知识库入库/检索 Pipeline + 文档解析服务 + 可观测性 |
| 参考项目 | 联通万悟（UnicomAI/wanwu）、万悟文档解析服务（UnicomAI/DocParserServer）、Haystack、MinerU、Docling、ParadeDB、Arize Phoenix |

---

## 1. 背景与目标

### 1.1 背景

我们要建设一个**面向智能体开发者的通用开发平台**，功能范围参考联通万悟，但定位不同：

- 万悟面向的场景更广，包含数字员工、本体智能体、探索广场等模块，这些本平台不需要。
- 本平台保留万悟的**资源库、模型管理、应用开发（Agent / 工作流）**这几个方向，并且把**知识库**作为核心差异点，做得更深。

万悟知识库的做法是：解析 → 分块 → 向量化走一条固定流程，开发者只能通过表单调整参数。分段方式只有"通用分段"和"父子分段"两种；检索方式只能在向量、全文、混合三种模式里选。

### 1.2 目标

1. 知识库的**入库**和**检索**都做成**可自由组合的画布 Pipeline**。开发者可以自己选择解析器、清洗方式、是否人工审核、分块策略、Embedding 模型、召回方式、融合方式和 Rerank 模型。
2. **文档解析做成独立服务**：对外提供统一的输入输出协议，内部可以接入 MinerU、Docling 等多种解析引擎。
3. **从第一天起就具备完整的可观测性**：能追踪每个文档、每个节点、每次模型调用。
4. 架构上为以后的 Agent、工作流、资源库、多租户留好扩展点，但 MVP 不实现这些功能。

### 1.3 约束

| 约束 | 说明 |
| --- | --- |
| 人力 | 1 人开发与维护，因此优先选择**组件少、运维轻、一种后端语言到底**的方案 |
| 部署 | 单机 docker-compose；Worker 需要能横向扩展 |
| 规模 | 单个知识库 ≤ 10 万文档；全平台 ≤ 1000 万 chunk |
| 信创 | 无要求 |
| 许可证 | 以后可能商业化，因此对 AGPL 和 ELv2 许可的组件保持警惕，预留替换路径 |

---

## 2. 范围

### 2.1 MVP 包含

| 模块 | 内容 |
| --- | --- |
| 账号 | 用户名密码登录、JWT、一个默认工作空间 |
| 模型管理 | LLM / Embedding / Rerank / 解析服务实例的登记、连通性测试、限流配置、用量统计 |
| 知识库 | 知识库的增删改查、元数据 schema、文档管理、chunk 查看与编辑、Embedding 重建索引 |
| 入库 Pipeline | 画布编辑、版本发布、路由、解析、清洗、人工审核、分块、元数据提取、向量化、写入索引 |
| 检索 Pipeline | 画布编辑、版本发布、向量召回、BM25 召回、元数据过滤、融合、Rerank、后处理 |
| 命中测试 | 根据检索 Pipeline 声明的输入变量自动生成表单，逐节点展示中间结果 |
| 人工审核 | 待审核列表、解析结果审核（原文与解析结果对照）、分块结果审核 |
| 文档解析服务 | 独立部署；引擎包括 builtin 和 MinerU；提供异步和同步接口 |
| 可观测性 | OTel + Phoenix、运维看板、结构化日志 |
| 其他 | Pipeline 导入导出、内置模板、SSE 进度推送 |

### 2.2 MVP 不包含（后续迭代）

- Agent 应用、工作流应用
- 资源库（MCP、自定义工具、提示词模板、Skill）
- 多租户、角色权限、单点登录
- 开放 API、API Key
- 检索评测集
- 开发者自定义节点（所有节点都由平台内置，不对开发者开放）
- 第二批节点：VLM 解析、LLM 增强（摘要、关键词、假设问题）、HyDE、查询改写
- 第二批解析引擎：Docling；以后按需接入 PaddleOCR-VL

---

## 3. 总体架构

### 3.1 架构图

```mermaid
flowchart TB
    subgraph Client
        WEB["web<br/>React + TS + React Flow"]
    end

    subgraph Platform["平台后端（同一份 Python 代码）"]
        API["api (FastAPI)<br/>REST + SSE<br/>检索执行器（内存同步）"]
        WORKER["worker<br/>入库执行器（持久化队列）"]
    end

    subgraph Parse["文档解析服务（独立）"]
        PS["parse-service (FastAPI)<br/>引擎适配器：builtin / mineru / docling…"]
        PSW["parse workers"]
    end

    subgraph Infra["基础设施"]
        PG[("ParadeDB<br/>业务表 / pgvector / pg_search<br/>Procrastinate 队列")]
        S3[("MinIO<br/>原文件 / 中间产物 / 页面截图")]
        RDS[("Redis / Valkey<br/>限流 / 缓存 / pub/sub")]
        PHX["Phoenix<br/>Trace UI（数据库在 ParadeDB）"]
    end

    subgraph External["外部服务"]
        MINERU["MinerU（GPU）"]
        MODELS["LLM / Embedding / Rerank<br/>OpenAI 兼容 / Jina·Cohere·TEI 风格"]
    end

    WEB -- "REST (JWT)" --> API
    API -- SSE --> WEB
    API --> PG & S3 & RDS
    WORKER --> PG & S3 & RDS
    API -- 入队 --> PG
    PG -- 取任务 --> WORKER
    WORKER -- "异步任务 + 回调<br/>traceparent 透传" --> PS
    PS --> PSW --> MINERU
    PS --> S3
    PS --> PG
    API & WORKER --> MODELS
    API & WORKER & PS -- OTLP --> PHX
    PHX --> PG
```

### 3.2 组件清单

| 组件 | 角色 | 说明 |
| --- | --- | --- |
| web | 前端 | 画布、知识库、审核、命中测试、模型管理、运维看板 |
| api | 平台后端（HTTP） | 内部 REST 接口、SSE 推送、检索执行器 |
| worker | 平台后端（后台任务） | 入库执行器、索引重建、定时清理 |
| parse-service | 独立解析服务 | 统一的解析协议，内部接入多个引擎 |
| ParadeDB | 数据库 | 业务数据 + 向量（pgvector）+ BM25（pg_search）+ 任务队列（Procrastinate）+ Phoenix 的数据库 + 解析服务的数据库 |
| MinIO | 对象存储 | 原始文件、ParsedDocument、页面截图、抽取出的图片、节点中间产物 |
| Redis / Valkey | 缓存 | 全局限流、query embedding 缓存、进度事件的 pub/sub；**只存放丢了也没关系的数据** |
| Phoenix | 可观测性 | 接收 OTLP 数据，提供 trace 界面 |
| MinerU | 外部解析引擎 | 需要 GPU，独立部署 |

### 3.3 关键架构原则

1. **模块化单体**：api 和 worker 是同一份代码的两种启动方式，以后需要时再拆成微服务。
2. **强一致的状态都放在 PostgreSQL**：业务数据和任务状态可以在同一个事务里提交。Redis 故障不影响主流程。
3. **一套节点定义，两种执行器**：入库使用持久化队列执行器，检索使用内存同步执行器。
4. **存储层可替换**：向量和全文检索都封装在 `IndexBackend` 接口后面；限流封装在 `RateLimiter` 接口后面。
5. **打点和后端解耦**：代码里只使用 OTel API，数据发往哪个后端由环境变量决定。

---

## 4. 技术栈

| 层 | 选型 |
| --- | --- |
| 后端语言 | Python（用 uv 管理依赖） |
| Web 框架 | FastAPI |
| 数据校验与 schema | Pydantic v2 |
| ORM 与数据库迁移 | SQLAlchemy 2（异步）+ Alembic |
| 任务队列 | Procrastinate（基于 PostgreSQL） |
| 数据库 | ParadeDB（PostgreSQL + pg_search + pgvector） |
| 对象存储 | MinIO（S3 协议） |
| 缓存与限流 | Redis，或 BSD 许可的 Valkey |
| 前端 | Vite + React + TypeScript + React Flow（xyflow）+ TanStack Query |
| 参数表单 | 基于 JSON Schema 自动渲染（rjsf 或同类库）+ 自定义 widget |
| 前后端类型 | 由 FastAPI 导出 OpenAPI，再用 `openapi-typescript` 生成前端类型 |
| 可观测性 | OpenTelemetry SDK + OpenInference 语义规范 + Arize Phoenix |
| 部署 | docker-compose |

### 4.1 仓库结构

```
repo/
├─ backend/
│  ├─ app/api/              # FastAPI 路由（内部接口）
│  ├─ app/domain/           # 账号、工作空间、模型管理、知识库、文档、chunk
│  ├─ app/pipeline/         # 类型系统、节点注册、DSL 校验、两种执行器
│  ├─ app/nodes/            # 内置节点实现（ingest/*, retrieve/*）
│  ├─ app/index/            # IndexBackend 接口 + ParadeDB 实现
│  ├─ app/models_client/    # 统一的 model client（限流、缓存、打点、写 model_call 表）
│  ├─ app/obs/              # OTel / OpenInference 打点辅助模块
│  ├─ app/review/           # 人工审核
│  ├─ app/events/           # SSE 与 Redis pub/sub
│  └─ entrypoints/          # api.py / worker.py
├─ parse-service/
│  ├─ app/api/              # /v1/jobs, /v1/parse:sync, /v1/engines
│  ├─ app/engines/          # builtin.py, mineru.py, docling.py ...
│  ├─ app/schema/           # ParsedDocument（和 backend 共享，以包的形式发布或 vendoring）
│  └─ entrypoints/          # api.py / worker.py
├─ web/
└─ deploy/docker-compose.yml
```

---

## 5. 账号与工作空间

- **登录方式**：用户名密码登录，签发 JWT，前端通过 `Authorization: Bearer` 请求头携带。
- **工作空间**：系统初始化时创建一个默认工作空间，所有用户都在这个工作空间内。**MVP 不区分角色**。
- **数据隔离字段**：所有业务表从第一天起就带 `workspace_id`，以后做多租户时再加 `tenant_id`。
- **接口范围**：MVP 只有供前端使用的内部接口。**不开放 API，不建 `api_key` 表。**

---

## 6. 模型管理

### 6.1 模型类型与协议

| 类型 | 协议 | 必填属性 |
| --- | --- | --- |
| LLM | OpenAI 兼容 `/v1/chat/completions`（可覆盖 vLLM、Ollama、Xinference、DeepSeek、通义兼容模式等） | 上下文长度 |
| Embedding | OpenAI 兼容 `/v1/embeddings` | 向量维度、最大输入长度 |
| Rerank | Jina/Cohere 风格 `/rerank`；HuggingFace TEI 风格 | 最大文档数 |
| 解析服务实例 | 平台自定义协议（见第 11 节） | 服务地址；平台通过 `/v1/engines` 动态获取它支持哪些引擎 |

所有模型都有以下通用属性：名称、Base URL、API Key（加密存储）、**最大并发数**、**QPS 上限**、启用状态，并支持**连通性测试**。

### 6.2 统一 Model Client

平台里所有对模型的调用都必须经过 `models_client`，它负责以下几件事：

```
调用方(节点) ──▶ models_client
                  ├─ RateLimiter.acquire(model_id)      # 在 Redis 里做全局的并发和 QPS 控制
                  ├─ Cache.get(model_id, hash(text))    # 仅对检索时的 query embedding 生效
                  ├─ obs.span(kind=EMBEDDING/LLM/RERANKER)
                  ├─ 发起 HTTP 调用（带超时、带重试）
                  ├─ 写入 model_call 表（token 数、耗时、状态）
                  └─ RateLimiter.release()
```

### 6.3 限流（RateLimiter）

- **正常情况（Redis 可用）**：
  - **并发控制**：每个模型一个信号量。用 Lua 脚本原子执行"检查并占用一个槽位"，每个槽位带 TTL，防止进程崩溃后槽位一直被占着。
  - **QPS 控制**：每个模型一个令牌桶，同样用 Lua 脚本实现。
- **降级（Redis 不可用）**：退化成每个进程各自限流，把总限额平均分给各进程（进程数从配置读取），同时记录告警日志。
- **接口**：`RateLimiter` 接口保持稳定，以后要换实现不影响调用方。

### 6.4 缓存

- **缓存内容**：只缓存检索时 query 的 embedding。
- **Key 格式**：`emb:{model_id}:{sha256(text)}`，TTL 为 1 天。
- **Redis 配置**：关闭持久化；内存淘汰策略设为 `volatile-lru`，这样没有 TTL 的限流 key 不会被淘汰。
- **Redis 不可用时**：直接跳过缓存，每次都调用模型。

### 6.5 安全

- **加密方式**：API Key 使用 Fernet 加密存储，主密钥通过环境变量 `APP_MASTER_KEY` 提供。
- **前端展示**：只显示掩码，例如 `sk-****abcd`。
- **日志与 trace**：都不能记录明文密钥。

---

## 7. 知识库

### 7.1 概念模型

```mermaid
erDiagram
    WORKSPACE ||--o{ KNOWLEDGE_BASE : has
    KNOWLEDGE_BASE ||--o{ INDEX_SPACE : has
    KNOWLEDGE_BASE ||--o{ METADATA_FIELD : defines
    KNOWLEDGE_BASE ||--o{ DOCUMENT : contains
    DOCUMENT ||--o{ CHUNK : produces
    CHUNK ||--o{ CHUNK_EMBEDDING : "embedded in"
    INDEX_SPACE ||--o{ CHUNK_EMBEDDING : holds
    DOCUMENT }o--|| PIPELINE_VERSION : "processed by"
    DOCUMENT ||--o{ PIPELINE_RUN : "has runs"
    KNOWLEDGE_BASE }o--o| PIPELINE : "default ingest"
    KNOWLEDGE_BASE }o--o| PIPELINE : "default retrieve"
```

### 7.2 Embedding 与 Index Space

**两类索引，职责不同：**

| 索引 | 是否依赖 Embedding 模型 | 所在位置 |
| --- | --- | --- |
| BM25 全文索引（关键词检索） | 不依赖 | 建在 `chunk` 表上，用 Jieba 分词 |
| 向量索引（语义检索） | 依赖 | `chunk_embedding` 表，按 `index_space` 区分 |

**规则：**

- 建知识库时选定 Embedding 模型，同时创建第一个 `index_space`，状态为 `active`。
- **v1 版本中，每个知识库同一时刻只有一个 `active` 状态的 space**。这和万悟的"知识库级 Embedding"一致。和万悟不同的是，我们支持更换模型。
- 关键词检索、混合检索与 index_space 无关，始终可以使用。

**重建索引（更换 Embedding 模型）的流程：**

```mermaid
sequenceDiagram
    participant U as 用户
    participant API
    participant W as worker
    participant DB as ParadeDB
    U->>API: 更换 Embedding 模型
    API->>DB: 新建 index_space(status=building)，并为新维度建部分 HNSW 索引
    API->>W: 入队 reindex 任务
    loop 分批处理 chunk
        W->>W: 用新模型计算 embed_text 的向量（经过 model client）
        W->>DB: 写入 chunk_embedding(space=new)
    end
    Note over W,DB: 重建期间：检索仍然只用 active space；<br/>新入库的文档同时写入 active 和 building 两个 space
    W->>DB: 在一个事务内：new→active，old→retired
    W->>DB: 异步删除 retired space 的向量和索引
```

### 7.3 元数据

- **知识库级强类型 schema**：字段类型支持 `string`、`number`、`date`、`enum`、`bool`、`string[]`。
- **系统字段**（自动生成，只读）：`file_name`、`file_type`、`uploaded_at`、`uploaded_by`、`pipeline_name`、`pipeline_version`。
- **值的来源**：
  1. 上传时在表单里填写；
  2. Pipeline 中的元数据提取节点，可以用正则、文件名规则提取，第二批再加 LLM 提取；
  3. 在文档列表里手工批量编辑。
- **存储**：`document.metadata` 存文档级的值；chunk 继承文档的元数据，写入 `chunk.metadata`，便于检索时过滤。
- **检索时的使用**：元数据过滤节点根据字段类型提供对应的操作符，比如日期支持范围查询，枚举支持多选。

### 7.4 文档与 Pipeline 的关系

- **默认 Pipeline**：知识库可以设置一个默认入库 Pipeline，上传时如果不选，就用默认的。
- **按批次选择**：一次上传是一个批次（`batch`），整个批次选一个 Pipeline；批次内部如果有不同类型的文件，靠 Pipeline 里的路由节点分流。
- **单套结果**：一个文档同一时刻只有一套生效的 chunk。文档上记录 `pipeline_id` 和 `pipeline_version`。
- **重跑**：文档可以换 Pipeline 或换版本重跑。新结果写入成功后原子替换旧结果（见 9.5 节）；重跑失败时保留旧结果。
- **手工修改的处理**：入库后对 chunk 做过手工修改的（`origin=manual` 或有修改标记），重跑前弹窗提示"有 N 处手工修改，将被覆盖"，用户确认后直接覆盖。
- **Pipeline 升级**：发布新版本后**不会自动重跑**。知识库页面提示"N 个文档可以升级到 vX"，由用户勾选后批量重跑。

### 7.5 文档状态机

```mermaid
stateDiagram-v2
    [*] --> UPLOADED
    UPLOADED --> QUEUED: 创建 pipeline_run
    QUEUED --> PROCESSING
    PROCESSING --> WAITING_REVIEW: 执行到审核节点
    WAITING_REVIEW --> PROCESSING: 审核通过 / 超时自动通过
    WAITING_REVIEW --> FAILED: 驳回 / 超时自动驳回
    PROCESSING --> READY: 写入索引成功
    PROCESSING --> FAILED: 节点失败且重试次数用完
    FAILED --> QUEUED: 重试 / 从断点继续
    READY --> QUEUED: 重跑（旧 chunk 在替换前一直有效）
    READY --> [*]: 删除
```

> 说明：文档处于重跑流程（QUEUED / PROCESSING / WAITING_REVIEW）时，如果它之前已经是 READY，旧 chunk 在替换之前**仍然可以被检索到**。文档上另有一个 `has_active_chunks` 字段，用来单独表示这一点。

### 7.6 Chunk

| 字段 | 说明 |
| --- | --- |
| `content` | 用于检索结果展示和 BM25 的文本 |
| `embed_text` | 实际做向量化的文本，可以和 content 不同，比如在前面拼上标题路径 |
| `parent_id` | 父子分块时指向父块。父块本身也存在 chunk 表里，用 `is_parent` 标记，**不参与召回** |
| `source_refs` | 来源元素的 ID 列表、页码、bbox，用于在原文中定位 |
| `metadata` | 文档元数据加上 chunk 自身的元数据 |
| `keywords` | 关键词（第二批由 LLM 增强节点生成） |
| `enabled` | 是否参与检索 |
| `origin` | `pipeline` 或 `manual` |
| `position` | 在文档内的顺序，用于相邻 chunk 扩展 |

---

## 8. Pipeline 体系

### 8.1 资源模型

- **两种类型**：`kind = ingest | retrieve`。
- **与知识库的关系**：Pipeline 是工作空间内的**独立资源**，不和知识库强绑定，可以被多个知识库复用。
- **版本管理**：
  - 在画布上编辑的永远是 `draft`（草稿）。
  - 点击"发布"时，生成一个**不可修改**的 `vN`，包括 DSL 快照、发布人和发布时间。
  - 上传文档或调用检索时，只能引用已发布的版本。
  - **测试运行**可以直接使用草稿，但结果不写入知识库。

### 8.2 DSL

画布保存的内容是一份 JSON DSL。发布时，后端会做完整的校验：图中不能有环、端口类型必须匹配、必填参数必须填写、引用的模型必须存在。

```json
{
  "kind": "ingest",
  "schema_version": 1,
  "nodes": [
    {"id": "in",     "type": "ingest.input.file",      "params": {}},
    {"id": "route",  "type": "ingest.router",          "params": {
        "rules": [
          {"when": {"ext_in": ["pdf", "png", "jpg", "docx", "pptx", "xlsx"]}, "port": "rich"},
          {"when": {"ext_in": ["txt", "md", "html", "csv"]},                   "port": "plain"}
        ],
        "default_port": "rich"}},
    {"id": "p1",     "type": "ingest.parse",           "params": {"service": "{{model:parse-main}}", "engine": "mineru"}},
    {"id": "p2",     "type": "ingest.parse",           "params": {"service": "{{model:parse-main}}", "engine": "builtin"}},
    {"id": "review", "type": "ingest.review.parsed",   "params": {"timeout_hours": 72, "on_timeout": "approve"}},
    {"id": "chunk",  "type": "ingest.chunk.recursive", "params": {"chunk_size": 512, "overlap": 50}},
    {"id": "embed",  "type": "ingest.embed",           "params": {"batch_size": 32}},
    {"id": "write",  "type": "ingest.write_index",     "params": {}}
  ],
  "edges": [
    {"from": "in.file",       "to": "route.file"},
    {"from": "route.rich",    "to": "p1.file"},
    {"from": "route.plain",   "to": "p2.file"},
    {"from": "p1.doc",        "to": "review.doc"},
    {"from": "review.doc",    "to": "chunk.doc"},
    {"from": "p2.doc",        "to": "chunk.doc"},
    {"from": "chunk.chunks",  "to": "embed.chunks"},
    {"from": "embed.chunks",  "to": "write.chunks"}
  ]
}
```

> `ingest.embed` 节点不需要选择模型。运行时，它自动使用目标知识库当前的 index_space（重建期间也包括 building 状态的 space）对应的模型。所以同一个 Pipeline 可以直接用在不同的知识库上。

### 8.3 类型系统

**端口类型**

| 端口类型 | 含义 |
| --- | --- |
| `File` | 原始文件的引用：MinIO 地址、文件名、MIME、大小、文档元数据 |
| `ParsedDocument` | 解析结果（schema 见 11.3 节） |
| `ChunkList` | chunk 列表（还没有向量） |
| `EmbeddedChunkList` | 带向量的 chunk 列表 |
| `Query` | 检索请求：query 文本、知识库列表、过滤条件、变量 |
| `CandidateList` | 候选 chunk 列表，每条带分数和来源（哪一路召回） |

**连线与汇合规则**

- **连线校验**：只有类型相同，或者满足声明的兼容关系（比如 `EmbeddedChunkList` 可以当作 `ChunkList` 使用），两个端口才能连线。前端用 React Flow 的 `isValidConnection` 做即时校验，后端在发布时再完整校验一遍。
- **多输入汇合**：一个输入端口允许有多条入边，但只用在**路由分支汇合**的场景。运行时，这个端口只取唯一一个没有被跳过的上游的输出。
  - 如果所有上游都被跳过了，这个节点也跳过（状态为 `SKIPPED`）。
  - 融合节点（RRF）是例外：它的端口声明为 `many=True`，会收集所有上游的输出。
- **变量引用**：只在少数参数里允许使用，例如 `{{inputs.top_k}}`、`{{model:占位符}}`、`{{file.metadata.department}}`。

### 8.4 节点定义

节点在后端声明，前端通过节点注册接口获取所有节点，自动渲染节点库和参数面板。

```python
class RecursiveChunkerParams(BaseModel):
    chunk_size: int = Field(512, ge=50, le=8192, title="分块长度 (token)")
    overlap: int = Field(50, ge=0, title="重叠长度")
    separators: list[str] = Field(["\n\n", "\n", "。", "；", " "], title="分隔符优先级")
    keep_title_path: bool = Field(True, title="embed_text 前拼接标题路径")

@node(
    type="ingest.chunk.recursive",
    title="递归分块",
    category="分块",
    inputs={"doc": ParsedDocument},
    outputs={"chunks": ChunkList},
    params=RecursiveChunkerParams,
    run_mode="in_worker",          # in_worker | external_wait | human_wait
)
class RecursiveChunker:
    async def run(self, ctx: NodeContext, doc: ParsedDocument, p: RecursiveChunkerParams) -> dict:
        ...
        return {"chunks": chunks}
```

- **注册接口**：`GET /api/pipeline/nodes`，返回每个节点的 `type`、标题、分类、图标、输入输出端口、参数的 JSON Schema 和 `run_mode`。
- **参数表单**：参数的 JSON Schema 可以通过 `json_schema_extra={"widget": ...}` 指定自定义控件。可用的控件有：`model-select`（按模型类型筛选）、`metadata-field-select`、`regex`、`code-lite`（只读预览）。
- **开放性**：**不开放自定义节点**，新增节点只能由平台开发者完成。

### 8.5 MVP 内置节点

**入库 Pipeline**

| 分类 | 节点 | 说明 |
| --- | --- | --- |
| 输入 | 文件上传 | 批次上传时的入口 |
| 输入 | URL 抓取 | 抓取网页，输出 `File` |
| 路由 | 路由 | 按扩展名、MIME、文件名正则、元数据把文件分到不同的输出端口 |
| 解析 | 解析 | 参数为解析服务实例和引擎（`builtin`、`mineru` 或 `auto`），运行方式是等待外部结果 |
| 清洗 | 文本清洗 | 去页眉页脚（按跨页重复内容识别）、正则替换、空白规整、去重 |
| 审核 | 解析结果审核 | 运行方式是等待人工，审核的是 `ParsedDocument` |
| 审核 | 分块结果审核 | 运行方式是等待人工，审核的是 `ChunkList` |
| 分块 | 固定长度 | 按 token 数切分，支持设置重叠 |
| 分块 | 递归分隔符 | 按分隔符优先级逐级切分 |
| 分块 | 按标题结构 | 以标题元素为边界切分，记录标题路径 |
| 分块 | 父子分块 | 先按较大粒度切出父块，再在每个父块内切出子块；只有子块参与召回 |
| 分块 | 语义分块 | 计算相邻句子的 embedding 相似度，在相似度低于阈值的位置断开，受最大长度约束 |
| 分块 | 自定义规则 | 由用户配置分隔符或正则，不需要写代码 |
| 元数据 | 元数据提取 | 用正则或文件名规则提取，写入 schema 里定义的字段 |
| 向量化 | 向量化 | 使用知识库的 index_space 对应的模型，节点内部分批调用 |
| 写入 | 写入索引 | 在一个事务内替换旧 chunk、写入 BM25 数据和向量 |

**检索 Pipeline**

| 分类 | 节点 | 说明 |
| --- | --- | --- |
| 输入 | 检索输入 | **声明对外暴露的变量**（名称、类型、默认值），例如 `query`、`kb_ids`、`filters`、`top_k` |
| 召回 | 向量召回 | 按每个知识库的 active space 分组，分别编码 query、分别召回 |
| 召回 | BM25 召回 | 基于 pg_search，使用 Jieba 分词 |
| 过滤 | 元数据过滤 | 可以作为召回节点的前置条件，也可以单独作为后置过滤 |
| 融合 | RRF / 加权融合 | 有多个输入端口，参数包括 k 值和每一路的权重 |
| 重排 | Rerank | 选择 Rerank 模型，可设置 top_n 和分数阈值 |
| 后处理 | 截断 | 按 TopK 和分数阈值截断 |
| 后处理 | 父块回溯 | 把命中的子块替换为对应的父块，并去重 |
| 输出 | 检索输出 | 输出最终的 chunk 列表，包括内容、分数、来源和定位信息 |

**第二批节点**：VLM 解析、LLM 摘要、关键词提取、假设问题生成、LLM 元数据提取、HyDE、查询改写、多查询扩展、相邻 chunk 扩展。

### 8.6 内置模板

| 类型 | 模板 | 结构 |
| --- | --- | --- |
| 入库 | 通用文档 | 输入 → 路由 → builtin/MinerU 解析 → 清洗 → 递归分块 → 向量化 → 写入 |
| 入库 | 长文档父子分块 | 输入 → MinerU 解析 → 按标题分块（父）+ 固定长度分块（子）→ 向量化 → 写入 |
| 入库 | 需要人工审核 | 输入 → MinerU 解析 → 解析结果审核 → 递归分块 → 分块结果审核 → 向量化 → 写入 |
| 检索 | 混合检索 + Rerank | 输入 → 向量召回 ∥ BM25 召回 → RRF → Rerank → 截断 → 输出 |
| 检索 | 纯向量检索 | 输入 → 向量召回 → 截断 → 输出 |

新建知识库时，默认入库 Pipeline 设为"通用文档"，默认检索 Pipeline 设为"混合检索 + Rerank"。

### 8.7 导入导出

- **导出**：导出的 JSON 包含 DSL 和元信息。DSL 中引用的具体模型会被替换成占位符，例如 `{{model:parse-main}}`，并附上占位符的说明（模型类型、维度等要求）。
- **导入**：导入时弹出映射界面，把每个占位符对应到当前工作空间里的实际模型。导入后生成一个新的 Pipeline 草稿。

---

## 9. 执行引擎

### 9.1 总体设计

执行引擎是自研的，借鉴了两个项目的设计：

- **Haystack 的组件契约和快照思想**：节点声明类型化的输入输出端口；执行状态可以持久化，支持从断点恢复。
- **万悟的"任务记录 + 队列 + Worker"模式**：在万悟里，knowledge-service 创建导入任务后发一条消息，由 Python 端的 Worker 消费处理。

我们没有直接使用 Haystack Pipeline 作为执行内核，原因有三：

1. 它在一个进程内同步执行完整个 DAG，很难做到节点级别的排队、限流和重试；
2. 解析节点要等外部回调，人工审核可能要等几天，这些都不适合放在一个同步运行的进程里；
3. 它的快照格式会随版本变化，不适合作为我们数据库里长期保存的状态格式。

|  | 入库执行器 | 检索执行器 |
| --- | --- | --- |
| 运行位置 | worker（Procrastinate 任务） | api 进程内（asyncio） |
| 状态持久化 | `pipeline_run` / `node_run` | 不持久化；每次调用写一条检索日志 |
| 暂停与恢复 | 支持（人工审核、等待外部服务） | 不支持 |
| 调试方式 | 在画布上逐个节点查看历史输出 | `debug=true` 时在响应里返回每个节点的输入、输出和耗时 |

### 9.2 入库运行模型

- **运行单位**：**每个文档一次运行**（`pipeline_run`），批次只是一个分组标签。失败、审核、重跑都以文档为单位进行，互不影响。
- **节点记录**：每个节点实例对应一条 `node_run`。节点的输出写到 MinIO，`node_run` 表里只保存输出的地址和摘要。
- **调度循环**（伪代码）：

```python
async def on_node_finished(run_id, node_id):
    for nxt in dag.successors(node_id):
        if all_inputs_ready_or_skipped(run_id, nxt):
            if should_skip(run_id, nxt):          # 被路由排除的分支
                mark_skipped(run_id, nxt); await on_node_finished(run_id, nxt)
            else:
                await enqueue("execute_node", run_id=run_id, node_id=nxt)
    if all_terminal(run_id):
        finalize_run(run_id)

@app.task(queue="pipeline", retry=RetryStrategy(max_attempts=3, exponential_wait=5))
async def execute_node(run_id, node_id):
    node = load_node(run_id, node_id)
    match node.run_mode:
        case "in_worker":
            outputs = await node.run(ctx, **load_inputs(run_id, node_id))
            save_outputs(run_id, node_id, outputs)          # 写 MinIO + 更新 node_run
            await on_node_finished(run_id, node_id)
        case "external_wait":
            job_id = await node.submit(ctx, ...)            # 例如提交解析任务
            mark(run_id, node_id, "WAITING_EXTERNAL", job_id=job_id)
        case "human_wait":
            create_review_task(run_id, node_id, ...)
            mark(run_id, node_id, "WAITING_REVIEW")
```

- **外部回调**：解析服务回调 `POST /internal/parse-callback`（用共享密钥签名）后，平台读取结果，把对应节点标记为成功，然后调用 `on_node_finished`。另外有一个兜底的周期任务，对超时未回调的外部任务主动查询状态。
- **幂等性**：每个 `execute_node` 在开始时检查 `node_run` 的状态，已经完成的节点直接跳过。这样队列重复投递消息也不会出问题。

### 9.3 状态定义

| 对象 | 状态 |
| --- | --- |
| `pipeline_run` | `PENDING`、`RUNNING`、`WAITING_REVIEW`、`WAITING_EXTERNAL`、`SUCCEEDED`、`FAILED`、`CANCELED` |
| `node_run` | `PENDING`、`QUEUED`、`RUNNING`、`WAITING_REVIEW`、`WAITING_EXTERNAL`、`SUCCEEDED`、`FAILED`、`SKIPPED` |

`pipeline_run` 表里还保存这次运行的 **trace 上下文**（`traceparent`）。人工审核可能要等几天，恢复执行后仍然挂在同一个 trace 下。

### 9.4 失败与重试

- **自动重试**：节点失败后按指数退避自动重试，默认 3 次。重试次数用完后，节点和整个运行都标记为 `FAILED`。
- **从断点继续**：在界面上操作，从失败的节点重新开始执行，已经成功的节点不会重跑。
- **从头重跑**：创建一个新的 `pipeline_run`。

### 9.5 写入与原子替换

`ingest.write_index` 节点在**一个数据库事务**内完成以下操作：

1. 删除这个文档的旧 chunk 和旧向量；
2. 插入新 chunk（BM25 索引随之更新）；
3. 为每个目标 index_space（active 状态的，重建期间还包括 building 状态的）插入向量；
4. 更新 `document` 的状态、`pipeline_version`、`has_active_chunks`。

事务提交之前，检索看到的一直是旧数据；如果写入失败，事务回滚，旧数据不受影响。

### 9.6 限流与批处理

- **节点内分批**：向量化节点按 `batch_size` 分批调用模型，每一批都要经过 `RateLimiter`。
- **全局控制**：多个 Pipeline、多个 Worker 同时运行时，由 Redis 里的全局信号量和令牌桶统一控制，避免把下游服务打垮。

### 9.7 检索执行器

- **执行方式**：在 API 进程内，按拓扑顺序执行；互不依赖的节点（比如多路召回）用 `asyncio.gather` 并行执行。
- **输入校验**：调用时只接受"检索输入"节点声明过的变量，并按声明的类型校验。
- **返回内容**：`debug=true` 时返回每个节点的输出摘要和耗时（候选列表完整返回，截断到节点的 TopK）；同时返回 `trace_id`，用于跳转到 Phoenix。
- **检索日志**：每次调用写一条检索日志，内容包括 query、Pipeline 版本、耗时、最终结果的 chunk ID。

---

## 10. 人工审核

### 10.1 审核任务

- **创建时机**：执行器运行到审核节点时，创建一条 `review_task`，关联到对应的 run、节点、文档和知识库。
- **审核人**：工作空间内的任何人都可以处理（MVP 不做指派和角色控制）。
- **处理动作**：`approve`（通过）或 `reject`（驳回）。修改会先保存为草稿，点击"通过"时一起提交。
- **超时策略**：节点参数 `timeout_hours` 设置超时时间，`on_timeout` 设置超时后的动作（`approve` 或 `reject`），由周期任务负责检查。
- **结果**：通过后，修改后的数据作为这个节点的输出，Pipeline 从断点继续执行；驳回后，这次运行标记为 `FAILED`，失败原因为"审核驳回"。

### 10.2 解析结果审核界面

```
┌──────────────── 原文 ────────────────┬──────────── 解析结果 ────────────┐
│ 页面缩略图（由解析服务渲染）             │ 元素列表（按阅读顺序）             │
│ 鼠标悬停或选中元素时，高亮对应的 bbox   │  · 编辑文本 / 编辑表格 HTML        │
│ 支持翻页、缩放                         │  · 修改元素类型（标题↔正文）        │
│                                       │  · 合并、拆分、删除元素             │
│                                       │  · 修改标题层级                    │
└───────────────────────────────────────┴───────────────────────────────────┘
```

没有 bbox 的元素（例如 docx 原生解析的结果），界面上退化为只显示渲染后的文本。

### 10.3 分块结果审核界面

- **展示**：chunk 列表，显示每个 chunk 的长度（token 数）、来源页码、父子关系。
- **操作**：编辑内容、编辑 `embed_text`、合并相邻 chunk、拆分 chunk、禁用 chunk、修改 chunk 级元数据。

---

## 11. 文档解析服务（parse-service）

### 11.1 定位

- **独立部署、可单独使用**：不依赖平台的业务数据库，可以给其他系统调用。
- **参考对象**：设计上参考万悟的文档解析服务（DocParserServer）。该项目使用 **AGPL-3.0** 许可证，所以**只参考设计，不拷贝代码**。

|  | 万悟 DocParserServer | 本服务 |
| --- | --- | --- |
| 引擎 | 一个实例只对应一个引擎（通过 `MODEL_TYPE` 选择 mineru 或 paddleocrvl） | 同一份代码包含多个引擎适配器，每个实例通过配置决定启用哪些 |
| 接口 | 只有同步接口 `POST /rag/model_parser_file` | 异步任务接口 + 同步接口 |
| 结构化输出 | 只有 paddleocrvl 版本提供块级 JSON | 所有引擎统一输出 ParsedDocument，带页码和 bbox |
| Office 文档 | 通过 Stirling-PDF 先转成 PDF | 直接用 MinerU 原生解析 DOCX / PPTX / XLSX |
| 在平台中的登记方式 | 登记为一个 OCR 模型 | 登记为"解析服务实例"，可以登记多个 |

### 11.2 接口

```http
GET  /v1/engines
→ [{"name":"builtin","formats":["pdf","txt","md","html","csv"],"bbox":true,"scanned":false},
   {"name":"mineru","formats":["pdf","png","jpg","docx","pptx","xlsx"],"bbox":true,"scanned":true},
   {"name":"auto", ...}]

POST /v1/jobs
{
  "file_uri": "s3://kb-raw/ws1/doc_123/原文.pdf",
  "engine": "mineru",                     // 或 auto
  "options": {"extract_images": true, "render_pages": true, "page_range": null},
  "output_prefix": "s3://kb-artifacts/ws1/doc_123/run_456/parse/",
  "callback_url": "http://api:8000/internal/parse-callback",
  "callback_secret_id": "platform"
}
→ {"job_id": "pj_789"}

GET  /v1/jobs/{job_id}
→ {"status": "queued|running|succeeded|failed", "progress": 0.6,
   "result_uri": "s3://…/parsed.json", "pages": 42, "error": null}

POST /v1/parse:sync          # 小文件或画布测试运行；有文件大小和耗时上限，超出返回 413/504
```

- **结果写入**：解析结果直接写入 MinIO 的 `output_prefix` 目录下，包括 `parsed.json`、`pages/{n}.png`、`images/{element_id}.png`。
- **回调**：请求体用 HMAC 签名，携带 `job_id` 和最终状态。
- **链路追踪**：请求头里携带 W3C `traceparent`，解析服务产生的 span 挂到平台的 trace 下。

### 11.3 ParsedDocument（统一输出格式）

```python
class PageInfo(BaseModel):
    page_no: int                  # 从 1 开始
    width: float                  # 单位 pt
    height: float
    image_uri: str | None         # 页面缩略图

class Element(BaseModel):
    id: str
    type: Literal["title", "paragraph", "list_item", "table", "image",
                  "formula", "code", "header", "footer", "caption", "other"]
    level: int | None = None      # 标题层级
    text: str | None = None       # 纯文本；公式为 LaTeX
    html: str | None = None       # 表格
    image_uri: str | None = None  # 图片
    page_no: int | None = None
    bbox: tuple[float, float, float, float] | None = None   # 该页内的绝对坐标 (x0, y0, x1, y1)，单位 pt
    order: int                    # 阅读顺序
    extra: dict = {}              # 引擎相关的原始字段

class ParsedDocument(BaseModel):
    schema_version: int = 1
    source: dict                  # file_uri, file_name, mime, engine, engine_version
    pages: list[PageInfo]
    elements: list[Element]
    def to_markdown(self) -> str: ...
```

- **标准格式**：平台内部**只使用**这一种格式。各引擎的适配器负责把自己的原始输出（例如 MinerU 的 `content_list`、Docling 的 `DoclingDocument`）转换成它。
- **Docling 分块器**：以后如果要复用 Docling 的分块器，再补一个 `ParsedDocument → DoclingDocument` 的转换器。
- **共享方式**：`ParsedDocument` 的定义以一个独立 Python 包的形式发布，平台后端和解析服务共同依赖这个包。

### 11.4 引擎

| 引擎 | 阶段 | 说明 |
| --- | --- | --- |
| `builtin` | MVP | 用 PyMuPDF 解析有文本层的 PDF；txt、md、html、csv 用原生方式解析；只需要 CPU |
| `mineru` | MVP | 调用外部 MinerU 服务。3.0 版本支持 DOCX 原生解析，3.1 版本增加 PPTX 和 XLSX 原生解析，许可证也改为基于 Apache 2.0 的 MinerU 开源许可证 |
| `docling` | 第二批 | MIT 许可证，支持的格式很多；可以和 MinerU 对比解析效果 |
| `paddleocr_vl` | 按需 | 万悟的解析服务已经接入了它 |
| `auto` | MVP | 按文件类型和有没有文本层自动选择：有文本层的 PDF 和文本类文件用 builtin，扫描件和 Office 文档用 mineru |

**选择方式**：解析节点里可以显式指定引擎，也可以选 `auto`，让解析服务自己判断。

### 11.5 任务存储与并发

- **任务存储**：PostgreSQL（独立的数据库 `parse_service`）+ Procrastinate。和平台一起部署时，复用同一个 ParadeDB 实例；独立部署时，自带一个 PostgreSQL。
- **并发控制**：每个引擎有自己的队列和 worker 并发数，例如 `mineru` 队列的并发数 = MinerU 服务能承受的并发量。
- **配置方式**：引擎通过配置文件或环境变量配置，包括是否启用、MinerU 的地址、超时时间、并发数。

---

## 12. 进度推送

- **推送方式**：Worker 每次更新 `pipeline_run` 或 `node_run` 的状态后，往 Redis 频道 `ws:{workspace_id}:runs` 发布一条事件，内容包括 `run_id`、`doc_id`、`node_id`、`status`。
- **SSE 接口**：API 提供 `GET /api/events/stream`，按工作空间订阅上述频道，把事件推送给前端。
- **前端处理**：收到事件后，用 TanStack Query 让对应文档的查询失效，**重新拉取最新状态**。事件本身只起通知作用，不作为状态的来源。
- **断线处理**：SSE 断开重连后，先完整拉取一次状态；如果 Redis 不可用，前端自动改为每 3 秒轮询一次。

---

## 13. 命中测试

- **入口**：知识库页面，或者检索 Pipeline 的画布页面。
- **输入表单**：根据"检索输入"节点声明的变量自动生成。`kb_ids` 默认填当前知识库。
- **执行方式**：以 `debug=true` 调用检索执行器。
- **结果展示**：
  - 最终结果列表：内容、分数、来源文档和页码，点击可以打开原文并高亮 bbox；
  - 在画布上点击任意节点，查看该节点的输出，比如各路召回的结果和分数、融合后的排序、Rerank 前后的变化；
  - "在 Phoenix 中查看"链接，跳转到对应的 trace。

---

## 14. 数据模型（核心表摘要）

> 所有业务表都带 `workspace_id`、`created_at`、`updated_at`，下面省略不写。

```sql
-- 账号
user(id, username, password_hash, display_name, status)
workspace(id, name)
workspace_member(workspace_id, user_id)

-- 模型管理
model(id, type /* llm|embedding|rerank|parse_service */, name, provider_protocol,
      base_url, api_key_enc, props jsonb /* dim, max_input, context_len… */,
      max_concurrency, qps_limit, enabled)

-- 知识库
knowledge_base(id, name, description, default_ingest_pipeline_id, default_retrieve_pipeline_id)
metadata_field(id, kb_id, key, type, enum_values, required)
index_space(id, kb_id, embedding_model_id, dim, status /* building|active|retired */)
upload_batch(id, kb_id, pipeline_id, pipeline_version, created_by)
document(id, kb_id, batch_id, file_name, file_uri, mime, size, metadata jsonb,
         status, has_active_chunks, pipeline_id, pipeline_version, error)
chunk(id, kb_id, doc_id, content, embed_text, parent_id, is_parent, position,
      source_refs jsonb, metadata jsonb, keywords text[], enabled, origin, edited)
chunk_embedding(chunk_id, space_id, embedding halfvec)   -- 不限定维度

-- Pipeline
pipeline(id, kind /* ingest|retrieve */, name, description, draft_dsl jsonb, latest_version)
pipeline_version(pipeline_id, version, dsl jsonb, published_by, published_at)
pipeline_run(id, pipeline_id, version, kb_id, doc_id, batch_id, status,
             traceparent, started_at, finished_at, error, is_test)
node_run(id, run_id, node_id, node_type, status, attempt,
         output_uri, output_summary jsonb, external_job_id,
         started_at, finished_at, error)
review_task(id, run_id, node_id, doc_id, kb_id, target_type /* parsed|chunks */,
            status, draft_uri, decided_by, decided_at, due_at)

-- 可观测性（平台侧）
model_call(id, ts, model_id, call_type, run_id, node_run_id, trace_id,
           input_tokens, output_tokens, batch_size, latency_ms, status, error)
           PARTITION BY RANGE (ts)
model_call_daily(day, model_id, calls, errors, tokens_in, tokens_out, p95_latency_ms)
retrieval_log(id, ts, pipeline_id, version, kb_ids, query, latency_ms, result_chunk_ids, trace_id)
```

### 14.1 索引

```sql
-- BM25（pg_search / Jieba）
CREATE INDEX chunk_bm25 ON chunk
  USING bm25 (id, (content::pdb.jieba), kb_id, doc_id, enabled, is_parent)
  WITH (key_field = 'id');

-- 向量：每个 index_space 建一个部分 HNSW 索引，建 space 时由程序动态创建
CREATE INDEX chunk_emb_<space> ON chunk_embedding
  USING hnsw ((embedding::halfvec(1024)) halfvec_cosine_ops)
  WHERE space_id = '<space_uuid>';

-- 常用过滤条件
CREATE INDEX ON chunk (kb_id, doc_id);
CREATE INDEX ON chunk USING gin (metadata);
CREATE INDEX ON node_run (run_id);
CREATE INDEX ON pipeline_run (doc_id, started_at DESC);
```

> BM25 查询的运算符和评分函数的写法，以最终选定并固定的 ParadeDB 版本文档为准，统一封装在 `IndexBackend` 的实现里。

### 14.2 IndexBackend 接口

```python
class IndexBackend(Protocol):
    async def replace_document_chunks(self, tx, doc_id, chunks, embeddings_by_space) -> None: ...
    async def delete_document(self, tx, doc_id) -> None: ...
    async def vector_search(self, space_id, dim, query_vec, kb_ids, filters, top_k) -> list[Candidate]: ...
    async def bm25_search(self, query, kb_ids, filters, top_k) -> list[Candidate]: ...
    async def create_space_index(self, space_id, dim) -> None: ...
    async def drop_space(self, space_id) -> None: ...
```

---

## 15. 可观测性

### 15.1 总体方案

| 能力 | 方案 |
| --- | --- |
| Trace | OTel SDK 打点，数据通过 OTLP 发送到 **Arize Phoenix** |
| 语义规范 | 以 **OpenInference** 为主，同时补充关键的 `gen_ai.*` 字段（模型名、token 数） |
| 指标 | 平台内置的运维看板，直接从 PostgreSQL 聚合数据；代码里同时用 OTel Metrics 打点，暂不导出 |
| 日志 | 输出到 stdout 的结构化 JSON 日志，每条都带 `trace_id` |
| 调试 | 平台画布上的调试视图，数据来自 `node_run` 表和检索 debug 响应；提供跳转到 Phoenix 的链接 |

**为什么选 OpenInference**：Phoenix 原生读取 OpenInference 属性；用 `gen_ai.*` 属性的 span 也能接收，但界面上的展示效果不如 OpenInference。而且 OTel GenAI 规范本身还没有发布稳定版本。Phoenix 从 15.10.0 开始支持把 `gen_ai.*` 属性自动转换成 OpenInference 格式，所以我们补充的那几个 `gen_ai.*` 字段在 Phoenix 里也能正确识别，以后迁移到其他后端也更容易。

### 15.2 打点规则

- **统一入口**：所有打点都通过 `app/obs` 模块写入，业务代码里不直接拼接属性名。
- **自动插桩**：FastAPI、SQLAlchemy、httpx 的自动插桩**默认关闭**。排查性能问题时，设置 `OBS_INFRA_SPANS=true` 临时打开，这些 span 同样发到 Phoenix。
- **导出配置**：通过环境变量 `OTEL_TRACES_EXPORTER`（可选 `none`、`console`、`otlp`）和 `OTEL_EXPORTER_OTLP_ENDPOINT` 控制。

| 平台概念 | Span kind | 主要属性 |
| --- | --- | --- |
| 一次入库运行（每个文档一次，根 span） | `CHAIN` | `metadata`：`workspace_id`、`kb_id`、`doc_id`、`run_id`、`pipeline_id@version`、`user_id` |
| 一次检索（根 span） | `CHAIN` | `input.value`（query）、`metadata`：`kb_ids`、Pipeline 版本 |
| 一般节点（解析、清洗、分块、写入） | `CHAIN` | `node_id`、`node_type`、状态、输入/输出摘要 |
| 调用解析服务 | `CHAIN` | `engine`、`job_id`、页数；解析服务内部产生的 span 通过 `traceparent` 挂到这个 span 下 |
| 向量召回 / BM25 召回 | `RETRIEVER` | `retrieval.documents`（chunk ID、分数、截断后的内容） |
| Rerank | `RERANKER` | 输入/输出的文档列表、模型名、`top_k` |
| 向量化节点 | `EMBEDDING` | 模型名、条数、批次数、token 数（**每个节点只记一次汇总**） |
| LLM 调用（第二批） | `LLM` | 模型名、token 数、截断后的消息 |
| 人工审核 | span event | 暂停和恢复时各记一个 event：`review.paused` 和 `review.resumed`（带审核人、等待时长） |

### 15.3 数据量控制

- **截断**：每个文本字段最多记录 2000 个字符；chunk 列表只记录条数和前 3 条样例；完整数据只记录它在 MinIO 里的地址。
- **采样**：检索 100% 记录；入库默认 100% 记录，可以通过 `OBS_INGEST_SAMPLE_RATE` 调低。

### 15.4 Phoenix 部署

```yaml
phoenix:
  image: arizephoenix/phoenix:version-X.Y.Z        # 固定一个 ≥ 15.10 的具体版本号
  environment:
    PHOENIX_SQL_DATABASE_URL: postgresql://phoenix:***@paradedb:5432/phoenix
    PHOENIX_ENABLE_AUTH: "true"
    PHOENIX_SECRET: ${PHOENIX_SECRET}
    PHOENIX_TELEMETRY_ENABLED: "false"
    PHOENIX_DEFAULT_RETENTION_POLICY_DAYS: "30"
  ports: ["6006:6006"]                              # 只在内网开放
```

- **项目划分**：`{env}-ingest`（保留 14 天）和 `{env}-retrieve`（保留 30 天）。两个项目的保留期在 Phoenix 里分别配置；清理任务由 Phoenix 按计划自动执行。
- **访问控制**：只有平台管理员可以访问，使用 Phoenix 自己的本地账号（MVP 不做单点登录）。**以后做多租户时，也不把 Phoenix 开放给租户使用**（Phoenix 的许可证是 Elastic License 2.0，限制把它作为托管服务提供给第三方）。

### 15.5 运维看板（平台内置）

| 看板 | 指标 |
| --- | --- |
| 模型 | 调用量、P50/P95 延迟、错误率、token 用量（按模型、按天统计） |
| 文档处理 | 成功率、平均耗时和 P95 耗时、各节点的耗时分布、失败原因 Top N |
| 队列 | Procrastinate 各队列的积压数量、最老任务已等待的时间；解析服务的任务积压 |
| 审核 | 待审核数量、平均等待时长、即将超时的任务数 |

---

## 16. 数据保留

| 数据 | 策略 |
| --- | --- |
| `model_call` | 按月分区，保留 90 天；过期前先聚合到 `model_call_daily`，再删除分区 |
| `node_run` 的输出（MinIO） | 每个文档只保留**最近一次成功运行**的全部节点输出；其他运行的输出删除 |
| `node_run` / `pipeline_run` 的状态记录 | 保留 180 天 |
| 画布测试运行的产物 | 7 天后删除 |
| `retrieval_log` | 保留 30 天；debug 模式下的详细输出不持久化 |
| Phoenix trace | 入库 14 天，检索 30 天 |
| Redis 数据 | 不持久化，都依靠 TTL 过期 |

以上保留天数都可以配置。平台侧的清理任务由 Procrastinate 周期任务在每天凌晨执行。

---

## 17. 部署

### 17.1 docker-compose 服务

| 服务 | 镜像 / 来源 | 说明 |
| --- | --- | --- |
| `web` | 自建（Nginx 托管静态文件，并反向代理 `/api`） |  |
| `api` | 自建 backend 镜像，启动 api 入口 | 可以运行多个副本 |
| `worker` | 自建 backend 镜像，启动 worker 入口 | 可以横向扩展 |
| `parse-api` | 自建 parse-service 镜像 |  |
| `parse-worker` | 自建 parse-service 镜像 | 按引擎配置并发数 |
| `paradedb` | ParadeDB 官方镜像（固定版本号） | 包含 3 个数据库：`platform`、`parse_service`、`phoenix`；Procrastinate 的表分别放在 `platform` 和 `parse_service` 两个库里 |
| `minio` | MinIO | 存储桶：`kb-raw`、`kb-artifacts` |
| `redis` | Redis 或 Valkey | 关闭持久化，淘汰策略为 `volatile-lru` |
| `phoenix` | arizephoenix/phoenix（固定版本号） |  |
| MinerU | 外部部署，需要 GPU | 不放在这个 compose 文件里 |

### 17.2 资源预估（单机）

- **ParadeDB 的内存需求取决于向量规模**，需要重点规划：以 1000 万个 1024 维向量为例，用 float32 存储约需 38 GB，用 halfvec 约需 19 GB，HNSW 索引的图结构还要额外占用空间。**上线前需要按实际数据量做压测**，再决定是否需要量化。
- **Phoenix**：推荐配置 2 核、2–4 GB 内存。
- **其他服务**：api、worker、解析服务、MinIO、Redis 大约共需 4–8 GB 内存，具体取决于 worker 并发数。

---

## 18. 为后续迭代锁定的约束

1. **Agent 工具化**：检索 Pipeline 的"输入节点"里声明的变量，以后可以直接转换成 Agent 调用检索工具时的参数 schema。
2. **工作流复用**：类型系统、节点注册机制、JSON Schema 自动生成参数表单，这几部分工作流可以直接复用。但工作流的节点之间通过变量引用传数据，所以需要**新写一个执行器**；画布库仍然用 React Flow。
3. **模型调用复用**：Agent 以后直接使用统一的 model client。
4. **多租户**：到时候加 `tenant_id`，并补充角色权限、按资源授权、单点登录（Phoenix 支持 OAuth2 和 LDAP）、按工作空间统计用量和计费（数据来自 `model_call`）。
5. **评测**：以后做检索评测集时，优先评估能否用 Phoenix 的 Datasets 和 Experiments 功能来实现。
6. **开放 API**：到时候再加 `api_key` 表（带 `scopes` 字段），并开放检索 API 和文档管理 API。
7. **资源库**：MCP、自定义 HTTP 工具、提示词模板等，和 Agent 一起设计。

---

## 19. 风险与待验证事项

| # | 风险 / 待验证事项 | 应对 |
| --- | --- | --- |
| R1 | 1000 万 chunk 的向量在单机上内存压力大 | 使用 halfvec；按 index_space 建部分 HNSW 索引；做压测；必要时量化，或者通过 `IndexBackend` 切换到其他向量库 |
| R2 | pg_search 是 AGPL-3.0，Phoenix 是 ELv2 | 内部使用一般没有问题；商业分发之前请法务评估；`IndexBackend` 接口和 OTel 打点都保留了替换路径 |
| R3 | MinerU 原生解析 DOCX / PPTX 时，可能拿不到页码和 bbox | 实际测试；拿不到时，审核界面退化为只显示文本 |
| R4 | Phoenix 的数据库和平台共用 ParadeDB，升级时可能互相影响 | 固定版本号；trace 量大时把 Phoenix 的数据库拆到单独的实例 |
| R5 | Jieba 对专业术语的分词效果 | 预留自定义词典的配置入口，并实测召回效果 |
| R6 | 路由分支汇合的语义比较复杂 | 在 DSL 校验和执行器里覆盖所有边界情况，写单元测试 |
| R7 | 单个文档 chunk 很多时，写入索引的事务比较大 | 压测；如果有必要，改为"写入新版本 → 切换生效版本"的方式 |
| R8 | ParadeDB 的 BM25 查询语法会随版本变化 | 固定版本号，所有查询都封装在 `IndexBackend` 里 |

---

## 20. 参考资料

- 联通万悟：https://github.com/UnicomAI/wanwu
- 万悟文档解析服务 DocParserServer：https://github.com/UnicomAI/DocParserServer
- 万悟工作流（基于 coze-studio 二次开发）：https://github.com/UnicomAI/wanwu-workflow
- Haystack（组件契约、断点与快照）：https://docs.haystack.deepset.ai
- MinerU：https://github.com/opendatalab/MinerU
- Docling：https://github.com/docling-project/docling
- ParadeDB：https://github.com/paradedb/paradedb
- Arize Phoenix：https://github.com/Arize-ai/phoenix
- OpenInference：https://github.com/Arize-ai/openinference
- React Flow：https://reactflow.dev
- Procrastinate：https://procrastinate.readthedocs.io

---

## 附录 A：决策记录

| # | 决策点 | 结论 | 主要理由 |
| --- | --- | --- | --- |
| D1 | 和万悟的关系 | 新建框架，万悟只作参考 | 知识库需要重新设计；避免在一个庞大的代码库上做裁剪 |
| D2 | 租户 | 最终做完整多租户，MVP 不做；表里预留 `workspace_id` | 先把核心功能做出来 |
| D3 | MVP 范围 | 模型管理 + 入库和检索 Pipeline | 知识库是核心差异点 |
| D4 | 应用类型 | Agent + 工作流（MVP 之后做） | 文本问答可以做成 Agent 的模板，不需要单独的应用类型 |
| D5 | Pipeline 和知识库的关系 | Pipeline 是独立资源，按批次选择，支持路由节点 | 可以复用；方便处理混合类型的文档 |
| D6 | 检索的配置方式 | 也用画布 Pipeline | 可组合性是核心需求 |
| D7 | 人工审核 | 阻塞式，可配置超时策略；解析后和分块后两个位置都支持 | 审核的质量控制作用 |
| D8 | 扩展性 | 不开放自定义节点 | 控制复杂度 |
| D9 | 模型管理范围 | LLM、Embedding、Rerank，加上解析服务实例 | 模型凭证统一管理 |
| D10 | Pipeline 版本 | 草稿和发布两种状态，发布后不可修改，不自动重跑 | 控制成本；chunk 的来源可以追溯 |
| D11 | Embedding 所在层级 | 知识库级，通过 index_space 支持重建索引 | 保证向量在同一个空间内；可以更换模型 |
| D12 | 画布的数据流 | 类型化端口，只支持 DAG | 连线时就能发现错误 |
| D13 | 后端语言 | Python | 文档处理的生态更成熟 |
| D14 | 解析结果格式 | 自定义的扁平元素列表，带 bbox | 便于实现审核界面和原文定位 |
| D15 | 手工修改与重跑的冲突 | 直接覆盖，重跑前提示 | 简单可控 |
| D16 | 元数据 | 知识库级强类型 schema | 过滤操作符需要知道字段类型；可以建索引 |
| D17 | 存储 | ParadeDB 一个库存所有数据，外加 `IndexBackend` 抽象 | 组件最少；写入时事务原子 |
| D18 | 执行引擎 | 自研，借鉴 Haystack 和万悟 | 需要节点级的持久化、等待外部结果、限流 |
| D19 | 运行粒度 | 每个文档一次运行，节点内分批，全局限流 | 失败和审核都能隔离到单个文档 |
| D20 | 中间产物 | 存 MinIO，按保留策略清理 | 便于以后迁移到云上的对象存储 |
| D21 | 画布库 | React Flow | 生态成熟，适合类型化端口 |
| D22 | 解析服务 | 独立服务，多引擎适配器，异步和同步接口，自己管理引擎配置 | 可以单独使用；GPU 和 CPU 引擎可以拆开部署 |
| D23 | 解析引擎 | MVP 做 builtin + MinerU；第二批做 Docling | MinerU 格式覆盖广，而且已改为宽松许可证 |
| D24 | 检索执行 | API 进程内同步执行，支持 debug 模式 | 延迟低 |
| D25 | 参数表单 | Pydantic → JSON Schema → 自动渲染 | 一个人维护的成本最低 |
| D26 | 开放 API | MVP 不开放，也不做 API Key | 缩小范围 |
| D27 | 观测后端 | 自研看板 + Phoenix | 只多一个容器，并且有完整的 LLM trace 界面 |
| D28 | 语义规范 | 以 OpenInference 为主，补充 `gen_ai.*` 字段 | Phoenix 原生支持 OpenInference；以后便于迁移 |
| D29 | 限流 | 引入 Redis，用 Lua 实现令牌桶和信号量，支持降级 | 真正的全局限流，部署简单 |
| D30 | Redis 的用途 | 只做限流、缓存、pub/sub；任务队列仍然放在 PostgreSQL | 任务状态和业务数据可以在同一个事务里提交 |
| D31 | 进度推送 | SSE + Redis pub/sub，Redis 不可用时降级为轮询 | 推送及时，而且可以降级 |