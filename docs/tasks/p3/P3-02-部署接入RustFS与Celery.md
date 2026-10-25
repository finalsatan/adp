# P3-02 部署接入 RustFS、Celery 与解析服务

状态：待验收
前置：P3-01
被依赖：P3-04、P3-06、P3-07、P3-08、P3-09、P3-12、P3-14（凡读写对象存储或调解析服务的后端卡）

## 目标

把第三期的运行组件接起来：对象存储（RustFS）读写封装、`/readyz` 增加对象存储连通检查，部署里加上 rustfs、parser、worker、worker-interactive、beat 五个服务，并加好第三期需要的新环境变量。Celery 应用代码由 P3-08 实现，本卡只提供它运行需要的部署与配置；解析服务 HTTP 客户端 `app/kb/parser_client.py` 提前到本卡建（2026-10-25 用户决定），P3-06、P3-09 复用。

## 依据

- 知识库技术方案 5（总体架构）、6.3（并发与离线）、11.1（对象存储路径）、18（部署变更，**逐条照做**）
- 技术方案 12（部署）
- 需求 4.1.10（处理队列与存储）
- 解析服务 X01 的 `parser/compose.dev.yml`（RustFS 版本与端口）、P1-18 的部署约定
- P3-08（Celery 应用代码）、P3-09（流水线复用本卡的解析服务客户端）——本卡做它们的前置部署，并提前建 `parser_client.py`（2026-10-25 用户决定）

## 要做

### 1. 配置 `app/core/config.py`

新增（知识库技术方案 18 的环境变量）：

- `S3_ENDPOINT`、`S3_BUCKET`、`S3_ACCESS_KEY`、`S3_SECRET_KEY`；`S3_REGION`（默认 `us-east-1`，补充）。
- `PARSER_URL`、`PARSER_TOKEN`（P3-09 的客户端用）。
- `CELERY_BROKER_URL`（Redis 的另一个库号，默认从 `REDIS_URL` 派生为 1 号库，补充；P3-08 的 Celery 应用用）。

生产（`APP_ENV=prod`）缺失 `S3_ENDPOINT`、`S3_BUCKET`、`PARSER_URL` 时启动失败并说明原因；开发 / 测试可空（补充）。

### 2. 对象存储封装 `app/core/storage.py`

用 boto3（与解析服务 `parser/app/storage.py` 同一套，path-style addressing）：

- `Storage` 类持有 boto3 client；构造参数从 `Settings` 取。
- `upload_stream(key, fileobj, content_type=None)`：流式上传（`upload_fileobj`），不把整个文件读进内存。
- `get_object(key, byte_range=None)`：返回 `(body, content_length, content_range)`，`byte_range` 支持 HTTP Range（原文预览用，知识库技术方案 13）。
- `download(key, dest)`、`put_bytes(key, data)`、`get_bytes(key)`。
- `exists(key)`、`delete_prefix(prefix)`（按前缀删除，删除文档 / 知识库时用，知识库技术方案 11.1）、`copy`（两代切换 / 转换产物用，补充）。
- `check()`：桶存在且可访问（`head_bucket`），失败抛异常；`/readyz` 用。
- boto3 是同步库，所有调用用 `anyio.to_thread.run_sync` 放到线程里，不阻塞事件循环（补充）。
- 对象键拼装函数 `kb_original_key(kb_id, doc_id, ext)`、`kb_prefix(kb_id)`、`doc_prefix(kb_id, doc_id)`、`parsed_key(...)`、`images_prefix(...)`、`trial_prefix(...)`，路径照知识库技术方案 11.1。

### 3. `/readyz` 加对象存储检查 `app/core/health.py`

- 加 `_check_storage(request)`：调 `request.app.state.storage.check()`，超时 2 秒，失败时返回异常类型名。
- `checks` 加 `storage`；任一项不是 `ok` 就 503。
- `app/main.py` 把 `Storage` 实例挂到 `app.state.storage`。

### 4. 部署

- `deploy/docker-compose.dev.yml`：加 `rustfs`（`rustfs/rustfs:1.0.3`，S3 59000、控制台 59002，凭据 `adpdev` / `adpdevsecret`）和一次性的 `createbuckets`（`amazon/aws-cli:2.38.4`，建桶 `adp`），照 `parser/compose.dev.yml`。开发 `backend/.env.example` 指向 `http://localhost:59000`、桶 `adp`。
- `deploy/docker-compose.yml` 新增：
  - `rustfs`（版本锁定，数据卷 `adp_rustfs`，只在内网；不对外发布端口）+ 一次性 `createbuckets`。
  - `parser`（构建 `../parser`，`PARSER_TOKEN`、`S3_*`、`PARSER_WORKERS`、`MINERU_URL` / `MINERU_API_KEY` 可空；depends_on rustfs healthy；只在内网）。
  - `worker`（同后端镜像，`celery -A app.worker.celery_app worker -Q ingest,maintenance -c ${WORKER_CONCURRENCY:-2}`）、`worker-interactive`（`-Q interactive -c 2`）、`beat`（`celery -A app.worker.celery_app beat`，只运行一个）。worker 的 Celery 应用代码由 P3-08 建，本卡先把服务与命令写好。
  - api / worker / worker-interactive / beat 注入 `S3_*`、`PARSER_URL=http://parser:59001`、`PARSER_TOKEN`、`CELERY_BROKER_URL`；api 依赖 `migrate` 完成、rustfs healthy。
  - `parser` 健康检查 `/healthz`，`rustfs` 用它自己的健康接口，`worker` 用 `celery inspect ping`（知识库技术方案 18）。
- `deploy/.env.example`、`deploy/gen-secrets.sh`：加 `S3_ACCESS_KEY`、`S3_SECRET_KEY`、`PARSER_TOKEN`（随机生成）、`S3_ENDPOINT`、`S3_BUCKET`、`CELERY_BROKER_URL`、`WORKER_CONCURRENCY`；`gen-secrets.sh` 生成的 `.env` 一并写入。
- `deploy/nginx/nginx.conf`：`/api/` 下 `client_max_body_size` 设为单文件上限的 2 倍以上（默认 512m）、`proxy_request_buffering off`（流式上传）、读取接口透传 `Range`（`proxy_set_header Range $http_range`、`proxy_set_header If-Range $http_if_range`）；知识库技术方案 18。
- `backend/Dockerfile`：无需改（celery 由同一镜像运行，依赖进 `pyproject.toml`）。
- `Makefile`：`dev-up` 现在会带上 rustfs（compose 已加，不用改目标）；`worker`、`beat` 目标由 P3-08 加。

### 5. 依赖

`backend/pyproject.toml` 加 `celery[redis]`、`boto3`、`httpx`（`parser_client` 用，若还只在 dev 组则移到运行依赖）；锁版本进 `uv.lock`。版本选择照 P2-05 的规则（发布满两周）。P3-08 会复用这里的 celery 依赖。

### 6. 解析服务 HTTP 客户端 `app/kb/parser_client.py`

提前到本卡建（2026-10-25 用户决定），P3-06、P3-09 复用：

- httpx 异步客户端，`base_url=PARSER_URL`，每个请求带 `Authorization: Bearer {PARSER_TOKEN}`。
- 方法照解析服务 X01 的接口：`submit_job`（`POST /v1/jobs`）、`get_job`（`GET /v1/jobs/{id}`）、`cancel_job`（`DELETE /v1/jobs/{id}`）、`list_engines`（`GET /v1/engines`）、`convert`（`POST /v1/convert`）、`health`（`GET /healthz`）。
- 只做请求封装与错误映射（超时、非 2xx 转成统一异常）；轮询等待、进度写回、重试留在 P3-09 的流水线里。
- `PARSER_TOKEN` 不进日志、不进错误响应（开发约定 4.5）。

## 文件

- 新建：`backend/app/core/storage.py`、`backend/app/kb/parser_client.py`（P3-06、P3-09 复用）、`backend/tests/core/test_storage.py`、`backend/tests/core/test_readyz_storage.py`、`backend/tests/core/test_parser_client.py`
- 修改：`backend/app/core/config.py`、`backend/app/core/health.py`、`backend/app/main.py`、`backend/.env.example`、`backend/pyproject.toml`、`backend/uv.lock`、`deploy/docker-compose.dev.yml`、`deploy/docker-compose.yml`、`deploy/.env.example`、`deploy/gen-secrets.sh`、`deploy/nginx/nginx.conf`

## 不做

- Celery 应用代码、任务、调度（P3-08）；解析服务客户端的轮询与重试（P3-09）；上传接口（P3-07）；检索（P3-13）。
- MinerU 服务镜像（X08）、解析引擎（X04～X08）。
- 系统设置“上传与解析”分组（P3-01 定义契约，P3-07 / P3-08 实现）。

## 验收标准

- [x] 测试 `test_storage.py`（连 `make dev-up` 起的 RustFS）：`put_bytes` 后 `exists` 为真、`get_bytes` 一致；`upload_stream` 上传一个较大的临时文件（≥ 5MB）后能读回且内容一致；`get_object` 带 Range 返回正确的 `content_range` 和片段；`delete_prefix` 后前缀下的对象都没了；`check()` 在桶存在时通过。
- [x] 测试 `test_readyz_storage.py`：RustFS 正常时 `/readyz` 返回 200 且 `checks.storage == "ok"`；monkeypatch `storage.check` 抛异常时返回 503（不真停 RustFS）。
- [x] 测试 `test_parser_client.py`（用 `httpx.MockTransport`）：六个方法的路径、方法、Bearer 头正确，超时 / 非 2xx 转成统一异常，异常信息里不含 Token。
- [x] `make dev-up` 后 RustFS 健康、桶 `adp` 已建；`docker compose -f deploy/docker-compose.yml config` 能通过（worker / parser 服务的命令与依赖正确）。
- [x] `make backend-lint backend-test` 通过；`make gen-api` 无差异（本卡不改接口）。

## 注意

- 对象存储的凭据、`PARSER_TOKEN` 不进日志、不进错误响应（开发约定 4.5）。
- 生产 api 的 `/readyz` 现在依赖 RustFS；部署顺序要让 rustfs 先 healthy，否则 api 一直不健康（compose 里 `depends_on` 已体现）。
- 端口冲突：`deploy/docker-compose.dev.yml` 的 rustfs 用 59000 / 59002，和 `parser/compose.dev.yml` 的 rustfs 相同；第三期起本地开发用 `make dev-up` 的这一套，单独开发解析服务时才用 `make parser-dev-up`，两个不要同时起（补充）。
- worker / worker-interactive / beat 在 P3-08 之前起不来（缺 Celery 应用代码），本卡只保证 compose 配置正确。

## 问题

- 拆卡方案里没有哪张卡负责“上传与解析”的系统设置分组（需求 10.8、P1-03 说第三期再加）。已定（2026-10-25）：“上传与解析”分组由 P3-01 定义契约（`upload` 分组），P3-07 实现单文件上限、P3-08 实现解析并发两项，前端由 P3-20 实现。
- 已定（2026-10-25）：补一张前端卡 P3-20“前端：系统设置的上传与解析分组”（前置 P3-01，联调需要 P3-07、P3-08，被 P3-19 依赖），照 P1-17 的结构写。

## 完成记录

### 新建

- `backend/app/core/storage.py`：`Storage` 类（boto3 + path-style，所有调用经 `anyio.to_thread.run_sync`）：`upload_stream`（流式 / 自动分片）、`download`、`put_bytes`、`get_bytes`、`get_object`（支持 HTTP Range，返回 `(body, content_length, content_range)`）、`exists`、`delete_prefix`（分页 + 批量删除，返回删除数）、`copy`、`check`（`head_bucket`）；对象键拼装 `doc_file_key`、`kb_original_key`、`kb_prefix`、`doc_prefix`、`parsed_key`、`images_prefix`、`trial_prefix`（照知识库技术方案 11.1）。boto 异常统一转 `StorageError`（只带操作名，不带凭据）。
- `backend/app/kb/parser_client.py`：`ParserClient`（httpx 异步，`base_url=PARSER_URL`，每请求带 `Authorization: Bearer`），六个方法 `submit_job` / `get_job` / `cancel_job` / `list_engines` / `convert` / `health`；超时、非 2xx、非 JSON 统一转 `ParserUnavailableError`（错误里只带状态码与解析服务的 `code`，不含 Token）。
- `backend/tests/core/test_storage.py`：put/get/exists、≥5MB 流式上传读回、Range、copy、delete_prefix、check、键拼装。
- `backend/tests/core/test_readyz_storage.py`：RustFS 正常时 `checks.storage == "ok"`；monkeypatch `check` 抛异常时 503 且 `checks.storage == "RuntimeError"`。
- `backend/tests/core/test_parser_client.py`：六个方法的路径 / 方法 / Bearer 头、非 2xx 与超时映射、异常不含 Token。

### 修改

- `backend/app/core/config.py`：加 `S3_ENDPOINT`、`S3_BUCKET`、`S3_ACCESS_KEY`、`S3_SECRET_KEY`、`S3_REGION`（默认 `us-east-1`）、`PARSER_URL`、`PARSER_TOKEN`、`CELERY_BROKER_URL`；`CELERY_BROKER_URL` 为空时从 `REDIS_URL` 派生 1 号库；`APP_ENV=prod` 缺 `S3_ENDPOINT` / `S3_BUCKET` / `PARSER_URL` 时启动失败并列出缺哪些。
- `backend/app/core/health.py`：加 `_check_storage`（`app.state.storage.check()`，超时 2 秒，失败返回异常类型名），`/readyz` 的 `checks` 加 `storage`。
- `backend/app/main.py`：`app.state.storage = Storage(settings)`。
- `backend/.env.example`：加 S3_*、PARSER_*、CELERY_BROKER_URL（开发指向 localhost:59000 / 59001）。
- `backend/pyproject.toml`、`backend/uv.lock`：加 `celery[redis]==5.6.3`、`boto3==1.43.111`，`httpx` 从 dev 组移到运行依赖。
- `deploy/docker-compose.dev.yml`：加 `rustfs`（`rustfs/rustfs:1.0.3`，59000/59002，卷 `adp_rustfs`）与一次性 `createbuckets`（建桶 `adp`）。
- `deploy/docker-compose.yml`：加 `rustfs`（只在内网）、`createbuckets`、`parser`、`worker`、`worker-interactive`、`beat`；api / worker / worker-interactive / beat 用 `x-backend-env` 注入 `S3_*`、`PARSER_URL`、`PARSER_TOKEN`、`CELERY_BROKER_URL`；api 依赖 `migrate` 完成、`rustfs` healthy、`createbuckets` 完成；worker 健康检查用 `celery inspect ping`；parser 健康检查 `/healthz`；加卷 `adp_rustfs`。
- `deploy/.env.example`、`deploy/gen-secrets.sh`：加 `S3_ACCESS_KEY`、`S3_SECRET_KEY`（随机）、`PARSER_TOKEN`（随机）、`S3_ENDPOINT`、`S3_BUCKET`、`S3_REGION`、`PARSER_URL`、`PARSER_WORKERS`、`CELERY_BROKER_URL`、`WORKER_CONCURRENCY`，生成 `.env` 时一并写入。
- `deploy/nginx/nginx.conf`：`/api/` 加 `client_max_body_size 512m`、`proxy_request_buffering off`、透传 `Range` / `If-Range`。
- 文件列表外（必要的最小改动，见下）：`Makefile`、`backend/tests/conftest.py`、`backend/tests/core/test_health.py`。

### 验收命令结果

- `make dev-up`：`deploy-rustfs-1 ... Healthy`，`createbuckets` 日志 `make_bucket: adp`；`aws s3 ls` 显示桶 `adp`。原有 `deploy-postgres-1`、`deploy-redis-1` 仍 Up（未 down -v，数据未动）。
- `docker compose -f deploy/docker-compose.yml config`：exit 0；服务含 api / beat / createbuckets / migrate / nginx / parser / postgres / redis / rustfs / worker / worker-interactive。
- `docker compose -f deploy/docker-compose.dev.yml --profile init config`：exit 0。
- `make backend-lint`：`All checks passed!` / `152 files already formatted`。
- `make backend-test`：`381 passed, 1 skipped`（含新增 21 个用例；工作区里还有并行 P3-03 的 `tests/kb/` 用例，一起通过）。
- `make gen-api`：`frontend/openapi.json`、`frontend/src/api/schema.d.ts` 无差异。

### 和卡片不一致 / 自己补充的地方

1. **改了 `Makefile` 的 `dev-up`**（卡片写“不用改目标”）：`docker compose up -d --wait` 遇到会退出的 `createbuckets` 会以“container exited (0)”报错（实测 v5.5.1）。因此把 `createbuckets` 放进 `init` profile（`up --wait` 不会带上它），`dev-up` 在长驻服务就绪后再 `up -d createbuckets`。否则 `make dev-up` 必然失败。
2. **`backend/tests/conftest.py`、`backend/tests/core/test_health.py`**：`/readyz` 多了 `storage` 检查，测试环境的 `Settings` 要带上 S3 配置（连 `make dev-up` 的 RustFS），`test_readyz_ok` 的 `checks` 断言要加 `storage`。两处都是接口变化引起的必要同步，卡片文件列表没列。
3. **本地 `.env`**：`deploy/.env` 与 `backend/.env`（都未提交）补上了新增变量，否则 `docker compose config` / 本地开发起不来。新部署用更新后的 `gen-secrets.sh` 生成即可。
4. **依赖版本**：`celery[redis]==5.6.3`（2026-03-26）、`boto3==1.43.111`（2026-10-09，满两周）。锁定 boto3 使原来的传递依赖 1.43.118 降到 1.43.111；celery 的 redis extra 把 `redis` 从 8.1.0 降到 6.4.0（满足 `redis>=5`）。`uv.lock` 只增了 celery 相关包。
5. **`ParserClient` 的入参**：卡片没规定签名，六个方法按“路径 + 请求体 dict”封装（`submit_job(payload)`、`convert(payload)` 等），返回解析服务的 JSON；`ParserClient` 暂不挂到 `app.state`，由 P3-06 / P3-09 按需创建（P3-09 负责轮询与重试）。
6. **`Storage` 多两个补充**：`copy`（卡片要求）之外的通用键拼装 `doc_file_key`（P3-09 的 `converted.*` / `preview.pdf` 可直接复用）；`delete_prefix` 返回删除对象数，便于调用方记录。
7. **`_check_storage` 超时**：按卡片用 `asyncio.wait_for(..., 2)`；boto3 在本地 RustFS 不可达时是立即连接失败，实际不会卡满 2 秒。

### 问题

- 无阻塞问题。唯一与卡片不一致的是上面第 1 条（`Makefile` 的 `dev-up`），属部署实现细节，已按能跑通的方式处理；如不希望改 `dev-up`，可改为给 `createbuckets` 加常驻健康检查，但那会多留一个空闲容器。
