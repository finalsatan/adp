# MVP 接口约定（执行基线）

API 前缀 `/api`；端口 3001；前端开发端口 5173，通过 Vite proxy 转发 `/api`。生产 API 同时托管 `apps/web/dist`。JSON 使用 camelCase，时间 ISO 字符串，ID 字符串，成功直接返回对象，列表统一 `{items: T[]}`。错误 `{error: string, message: string}`，前端显示 message。所有接口校验输入并返回恰当 400/401/403/404/409。

认证：浏览器会话使用 HttpOnly SameSite=Lax cookie。前端所有 fetch `credentials:'include'`；当前组织请求头 `X-Org-Id`（登录后默认 orgId）。开放应用 API 用 `Authorization: Bearer adp_...`，仅绑定的发布应用；普通资源接口不得接受应用 Key。

## 通用

- `GET /health` → `{status:'ok',version:'0.1.0'}`，不需要登录。
- `GET /api/branding` → `{name:'ADP',subtitle:'智能体开发平台'}`。
- `GET /api/auth/status` → `{initialized:boolean,oidcEnabled:boolean}`。
- `POST /api/auth/setup` `{name,email,password,orgName}` → `{user,organizations}` 并设置 cookie；只允许首次。
- `POST /api/auth/login` `{email,password}` → `{user,organizations}` 并设置 cookie。
- `POST /api/auth/logout`。
- `GET /api/auth/me` → `{user,organizations}`。
- User `{id,name,email,isSystemAdmin:boolean}`；Org `{id,name,parentId?:string|null,role:string,permissions:string[]}`。
- `GET /api/auth/oidc/start`、`GET /api/auth/oidc/callback`：可选真实 OIDC 登录；未配置时不显示入口。
- `GET /api/overview` → `{knowledgeBases,documents,chunks,apps,queries,feedbackPending,recentTraces:Trace[]}`。

## 组织与权限

- `GET/POST /api/organizations`（POST `{name,parentId?}`）→ Org。
- `GET/POST /api/users`（POST `{name,email,password,orgId,roleId?}`）→ User。GET 需要当前组织 `system:manage`，普通组织管理员仅可查询当前组织成员，其返回的成员关系只包括该管理员明确可管理的组织；系统管理员可查询全部用户和成员关系。本人 `/api/auth/me` 保留全部个人组织。
- `GET/POST /api/roles`（POST `{name,permissions:string[],global?:boolean}`）→ `{id,name,permissions,global}`。
- `POST /api/organizations/:id/members` `{userId,roleId}`；`DELETE /api/organizations/:id/members/:userId`。
- `GET /api/organizations/:id/members` → `{items:[{id,name,email,roleId,roleName}]}`；`GET /api/organizations/:id/roles` → `{items:Role[]}`（仅目标组织和全局角色）。均校验目标组织管理权限，不依赖会话当前组织，也不继承父组织权限。
- `GET /api/organizations/:id/member-candidates` → `{items:[{id,name,email}]}`；系统管理员可查全部用户，普通目标组织管理员仅可查自己有明确管理权限的组织成员并集。
- `GET /api/audit` → `{items:[{id,actorName,action,resourceType,resourceId,createdAt,detail}]}`。
- permissions 名称：`knowledge:read|write|grant`，`apps:read|write|publish`，`models:manage`，`eval:manage`，`observe:read`，`keys:manage`，`system:manage`；组织管理员全开，终端用户仅 `apps:read`。用户列表和组织信息按权限裁剪。

## 模型

Model `{id,name,kind:'chat'|'embedding'|'rerank'|'vision',provider:'openai'|'demo',baseUrl,model,scope:'organization'|'platform',enabled,apiKeyMasked,dimensions?,createdAt}`。

- `GET/POST /api/models`，`PATCH/DELETE /api/models/:id`，写入字段 apiKey（可选，空值保留原密钥）。
- `POST /api/models/:id/test` → `{ok,message,latencyMs}`。
- 初始化 demo chat/embedding，名称明确“本地演示”，不用外网。真实模型调用失败不能静默退回 demo。

## 知识库

KB `{id,name,description,visibility:'private'|'organization'|'platform',orgId,ownerId,embeddingModelId,documentCount,chunkCount,chunkConfig,retrievalConfig,access:{view,edit,grant},createdAt}`。`access` 为该资源的实际权限，前端操作根据它判断；不能只使用当前组织菜单权限。

chunkConfig `{strategy:'structure'|'semantic'|'parent-child',maxChars:800,overlap:100}`；retrievalConfig `{mode:'hybrid'|'keyword'|'vector',topK:5,threshold:0,rerankModelId?:string|null,rewriteModelId?:string|null}`。

- `GET/POST /api/knowledge-bases`，`GET/PATCH/DELETE /api/knowledge-bases/:id`。
- `GET/POST /api/knowledge-bases/:id/grants`（POST `{userId,level:'view'|'edit'|'grant'}`），`DELETE .../grants/:userId`。
- `GET /api/knowledge-bases/:id/grant-candidates` → `{items:[{id,name,email}]}`；校验目标知识库实际授权权，支持所有者与显式授权者，不要求系统管理权限。普通授权者仅可查询所属组织成员、所有者和已有显式授权用户，系统管理员可查询全部用户。
- `GET /api/knowledge-bases/:id/documents` → Document 列表。
- `POST /api/knowledge-bases/:id/documents` multipart `file`；或 JSON `{name,content}` 用于文本创建。文件最大 20MB。
- `PUT /api/documents/:id/file` multipart file 或 JSON `{name,content}`，替换版本并入队。
- `DELETE /api/documents/:id`、`POST /api/documents/:id/reprocess`。
- `GET /api/documents/:id` → Document（含 parsedText）；`GET /api/documents/:id/source` 返回原文件（鉴权）。
- Document `{id,kbId,name,mime,size,status:'QUEUED'|'PARSING'|'PROCESSING'|'READY'|'FAILED',progress,error,chunkCount,version,createdAt}`。
- `GET /api/documents/:id/chunks` → Chunk 列表；`PATCH /api/chunks/:id` `{content?,enabled?}`；`POST /api/chunks/:id/split` `{offset}`；`POST /api/chunks/:id/merge` `{nextChunkId}`。
- Chunk `{id,documentId,content,headingPath:string[],position,enabled,edited,parentId?,isParent?,sourceRef:{page?,sheet?,row?,paragraph?,heading?}}`。
- 已有文档时不可更改知识库 Embedding 模型（409）。分块配置保存后由用户主动选择重新处理，避免覆盖人工编辑。
- `POST /api/knowledge-bases/:id/search` `{query,config?:retrievalConfig}` → `{items:Hit[],stages:[{name,durationMs,items:Hit[]}],traceId}`。
- Hit extends Chunk `{score,keywordScore?,vectorScore?,rerankScore?,documentName,kbId}`。

## 应用与问答

App `{id,name,description,visibility,orgId,kbIds:string[],chatModelId,systemPrompt,retrievalConfig,status:'draft'|'published',publishedVersion?:number,access:{view,edit,publish},createdAt}`。

- `GET/POST /api/apps`，`GET/PATCH/DELETE /api/apps/:id`。
- `POST /api/apps/:id/publish` → `{version:number,url:'/chat/:id',embedCode:string}`。
- `GET /api/published/apps/:id` → `{id,name,description,version,demo:boolean}`，遵守可见范围，浏览器登录或绑定 Key。
- `GET/POST /api/apps/:id/conversations`（POST `{title?}`）→ Conversation `{id,title,createdAt,updatedAt}`；query `published=true` 用于终端会话。
- `GET /api/conversations/:id/messages` → Message 列表，严格仅本人会话。
- `POST /api/apps/:id/chat` `{message,conversationId?,draft?:boolean,stream?:boolean}`。draft 仅编辑者可用；默认调用已发布版本。默认 JSON → `{conversationId,message:Message,traceId}`。stream=true 使用 SSE：`retrieval`、`delta` `{text}`、`citations`、`done`（最终完整响应）、`error`。前端可先 JSON 跑通并再启用流式。
- Message `{id,role:'user'|'assistant',content,citations:Citation[],traceId?,feedback?:{rating:1|-1,correction?:string},createdAt}`；Citation `{index,chunkId,documentId,documentName,content,sourceRef,headingPath}`。
- `GET /api/citations/:chunkId?appId=...` 提供经发布应用权限验证的原文片段；终端页点击引用可弹出原文定位，不要求知识库管理权限。
- `POST /api/messages/:id/feedback` `{rating:1|-1,correction?:string}`。
- `GET /api/feedback` → `{items:[{id,messageId,question,answer,rating,correction,appId,createdAt}]}`。

## API Key

- `GET /api/api-keys` → `{items:[{id,name,prefix,appId,createdAt,revokedAt}]}`。
- `POST /api/api-keys` `{name,appId}` → `{id,key,prefix}`，明文只在此返回。
- `DELETE /api/api-keys/:id` 吊销。

## 追踪与评测

- `GET /api/jobs/:id` → `{id,type,status:'QUEUED'|'RUNNING'|'COMPLETED'|'FAILED',progress,error,attempts,createdAt}`。按任务关联资源鉴权；生成评测题的前端用其轮询完成或失败，错误经过密钥脱敏。
- `GET /api/traces` → Trace 列表；`GET /api/traces/:id` → `{...Trace,spans:Span[]}`。
- Trace `{id,kind:'ingest'|'search'|'chat'|'eval',name,status,durationMs,createdAt,tokenCount?,appId?}`。
- Span `{id,name,kind,durationMs,input,output,status}`，不含 API Key。
- `GET/POST /api/eval-sets`（POST `{name,description?,appId}`）→ EvalSet `{id,name,description,appId,caseCount,createdAt}`。
- `GET/POST /api/eval-sets/:id/cases`，POST `{question,expectedAnswer,documentId?,tags?:string[]}` 或 `{cases:[...]}` 批量导入。
- `DELETE /api/eval-cases/:id`。
- `POST /api/eval-sets/:id/generate` `{count:5,kbId}` → 入队 `{jobId}`，用真实 chat 模型按文档生成；演示 provider 明确报不支持，不能伪造生成。
- `POST /api/eval-sets/:id/from-feedback` `{feedbackId,expectedAnswer?}` → case。
- `POST /api/eval-sets/:id/runs` `{name?,configA:retrievalConfig,configB?:retrievalConfig}` → EvalRun。
- `GET /api/eval-sets/:id/runs` → EvalRun 列表；`GET /api/eval-runs/:id` → EvalRun + results。
- EvalRun `{id,setId,name,status:'QUEUED'|'RUNNING'|'COMPLETED'|'FAILED',progress,configA,configB?,metricsA?,metricsB?,error?,createdAt}`。
- metrics `{total,answerMatch,citationAccuracy,retrievalRecall,refusalRate,avgLatencyMs}`，比例 0..1；答案指标明确为“词项匹配参考值”，无业务确认不能称正确率。
- result `{caseId,question,expectedAnswer,answerA,answerB?,citationsA?,citationsB?,scoresA,scoresB?}`。

### 业务人工评审

- `PATCH /api/eval-results/:id/review` `{variant:'A'|'B',answerCorrect:boolean,citationsCorrect:boolean,comment?:string}`，仅该评测集的有权限评测管理员可用，仅已完成运行可评审；B 必须实际存在。返回 `{review, humanMetrics}`。
- EvalRun 增加 `humanMetrics:{A:{reviewed,total,answerAccuracy:number|null,citationAccuracy:number|null},B?:同A}`。比例只以已评审条目为分母，未评审时为 null。
- result 增加 `reviews:{A?:{answerCorrect,citationsCorrect,comment,reviewerName,reviewedAt},B?:同A}`，保留评审人和时间并审计变更。复评同一配置覆盖该配置的原判断，不能影响另一配置。
- 自动指标与人工指标分别展示。人工答案正确率/引用准确率是业务人员针对已完成运行逐条审核的记录，不得用词项匹配代替；仍需业务确认试点问题集与阈值。

## 独立解析服务

端口 8001；`GET /health`、`GET /v1/engines`；`POST /v1/parse` JSON `{fileName,mime?,dataBase64}` 或 `{fileName,text}` → `{schemaVersion:1,text,elements:[{id,type,text,level?,page?,sheet?,row?,paragraph?,headingPath?:string[],order}],engine,warnings:string[]}`。内部调用携带 `X-Parser-Token`（环境变量 PARSER_TOKEN，配置后强制验证）。20MB、压缩解包上限、超时，不读取客户端传入的任意磁盘路径。

pi 可以完善约定遗漏，但改变路径/字段前需向主协调者说明，前后端需要同步；正常开发无需等待。
