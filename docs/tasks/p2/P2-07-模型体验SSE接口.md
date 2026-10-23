# P2-07 模型体验 SSE 接口

状态：待验收
前置：P2-05
被依赖：P2-10；第四期的对话和调试接口复用本卡的 SSE 工具

## 目标

实现 `POST /api/v1/models/{id}/chat`：流式返回模型输出，支持心跳、客户端断开即停止，以及可复用的 SSE 工具。

## 依据

- 技术方案 9.6（**逐条照做**）、D9
- 需求 3.6
- P2-01 的 SSE 事件定义

## 要做

### 1. SSE 工具 `app/gateway/sse.py`（第四期复用）

- `format_event(event: SseEvent) -> bytes`：`data: <json>\n\n`，JSON 用 `ensure_ascii=False`。
- `sse_response(events: AsyncIterator[SseEvent], request) -> StreamingResponse`：
  - `media_type="text/event-stream"`；响应头 `Cache-Control: no-cache`、`X-Accel-Buffering: no`（让 nginx 对这个响应关闭缓冲，见 P1-18）。
  - 心跳：每 15 秒没有其他输出时发一行注释 `: ping\n\n`；等首字和重试期间也照发（技术方案 9.6）。实现上把事件流和心跳计时器合并，**不能**因为等心跳而推迟真正的事件。
  - 客户端断开：检测到断开后取消产生事件的任务（gateway 随之取消上游、释放租约、记 `CANCELLED` 用量，P2-05 已实现）。按所用 Starlette 版本的行为实现（断开时生成器被取消，或轮询 `request.is_disconnected()`），**用测试证明**断开后上游调用确实被取消。

### 2. 接口（console.use）

- 模型必须是对话类型（否则 422 `MODEL_TYPE_MISMATCH`）、已启用（否则 409 `MODEL_UNAVAILABLE`，在开始流式之前就返回普通 JSON 错误）。
- 请求里的 `messages` 原样作为历史（体验页不保存对话，技术方案 9.6）；`params` 覆盖模型默认参数；`max_tokens` 不能超过模型的 `max_output`（超过时按 `max_output` 截断，补充）。
- 事件顺序：`RUN_STARTED`（run_id 为 uuid4 hex）→ 若干 `TEXT_DELTA` / `REASONING_DELTA` → `RUN_FINISHED`（token 数和耗时）；出错时以 `RUN_ERROR` 结束（code 为调用错误代码或 `MODEL_RATE_LIMITED`，message 为中文说明）。限流在流开始之后才发生时同样用 `RUN_ERROR`（补充：为简单起见，限流检查放在 gateway 里，统一以事件返回）。
- 用量来源 `playground`，发起人为当前用户（需求 3.6）。
- 多模型对比由前端对每个模型各发一个请求（技术方案 9.6），后端不需要特别处理。

## 文件

- 新建：`backend/app/gateway/sse.py`、`backend/app/models/playground.py`、`backend/tests/gateway/test_sse.py`、`backend/tests/models/test_playground.py`
- 修改：`backend/app/models/router.py`

## 验收标准

- [x] 测试 `test_playground.py`（模拟 LiteLLM）：正常对话的事件顺序和内容正确，响应头含 `X-Accel-Buffering: no`；思考内容以 `REASONING_DELTA` 输出；上游报错时最后一条是 `RUN_ERROR`；向量化模型返回 422；停用模型返回 409；用量记录 `source=playground`。
- [x] 测试 `test_sse.py`：
  - 把心跳间隔调成 0.2 秒、让假模型 1 秒后才出第一个字：收到首个事件前已有若干 `: ping`。
  - **断开**：在测试里用 uvicorn 在随机端口起真实服务，httpx 读到第一个 `TEXT_DELTA` 后关闭连接；断言假模型的生成被取消（假模型里记录取消标志）、并发租约被释放、用量记录为 `CANCELLED`（技术方案 13.2：中途停止生成记录 CANCELLED）。
- [x] `make backend-lint backend-test` 通过。

## 注意

- 不要用 `BaseHTTPMiddleware`（P1-01 已说明），它会让断开检测和流式输出出问题。如果断开测试过不了，先检查中间件。

## 问题

无。

## 完成记录

### 新建

- `backend/app/gateway/sse.py`：`format_event()`（`data: <json>\n\n`，`ensure_ascii=False`）、`sse_response()`。事件流和心跳用 `asyncio.wait` 合并：单独任务取下一个事件，超时 `HEARTBEAT_INTERVAL_S`（默认 15 秒）就发 `: ping`，事件一到立刻输出。响应头 `Cache-Control: no-cache`、`X-Accel-Buffering: no`。客户端断开时 Starlette 1.9.0（uvicorn ASGI 2.3 路径）取消本生成器，生成器 `finally` 取消取事件的任务，把取消传给 gateway。
- `backend/app/models/playground.py`：`chat()`。流式开始前读模型做类型（422）/启用（409）检查；`_build_params()` 去掉未填参数并把 `max_tokens` 截断到 `max_output`；`_events()` 把 gateway 片段翻成 SSE 事件（RUN_STARTED → TEXT_DELTA / REASONING_DELTA → RUN_FINISHED），`ModelCallError` 和 `AppError`（限流等）以 RUN_ERROR 结束；耗时在服务层用 `monotonic()` 计。
- `backend/tests/gateway/test_sse.py`、`backend/tests/models/test_playground.py`：共 9 个用例。

### 修改

- `backend/app/models/router.py`：只把 `playground_chat` 占位换成调用 `playground.chat()`（新增 `request`、`ctx`、`redis` 参数），并把 `from app.models import service, testing` 改成 `playground, service, testing`。
- `backend/tests/test_contract_p2.py`：第二期占位接口已全部实现，删掉最后一个占位用例（chat）及只为它服务的 `PLACEHOLDER_CALLS`、`_CHAT_MODEL`、`admin_client` 夹具和相应 import。

### 验收命令结果

- `uv run pytest tests/gateway/test_sse.py tests/models/test_playground.py -q`：`9 passed`。
- `make backend-lint`：`All checks passed!` / `131 files already formatted`。
- `make backend-test`（独立测试库，Redis 15 号库）：`307 passed, 1 skipped`。
- `make gen-api`：`frontend/openapi.json` 和 `src/api/schema.d.ts` 无变化（P2-01 已把该路径的 SSE 响应结构写进契约），未产生可提交的前端改动。

### 和卡片不一致 / 自己补充的地方

1. **`sse_response` 的断开处理用 Starlette 取消生成器的方式，没有轮询 `request.is_disconnected()`**。锁定的 Starlette 1.9.0 + uvicorn 0.54 走 ASGI 2.3 路径，`StreamingResponse` 会用 `listen_for_disconnect` 取消流任务，断开时生成器确实被取消（测试验证）。`request` 参数保留在签名里供第四期复用，当前未使用。
2. **等待取事件任务时用 `asyncio.shield`**。本生成器被取消后 `await anext_task` 会被“再次取消”连带取消 `anext_task`（asyncio 取消会取消当前 `_fut_waiter`），导致 gateway 正在写的 `CANCELLED` 用量记录被取消。改用 `await asyncio.shield(anext_task)`，让 gateway 的清理（P2-05 的 shield 写库）跑完。
3. **耗时由服务层自己计**。`chat_stream` 只产出 `ChatChunk`（文本/思考/用量），没有 `first_token_ms`/`total_ms`，`playground._events()` 用 `monotonic()` 自行记录，与 gateway 内部记的用量略有口径差（含流式开始前的读模型等开销）。
4. 心跳间隔通过模块常量 `HEARTBEAT_INTERVAL_S` 读取，测试用 monkeypatch 调成 0.2 秒；`sse_response` 也提供 `heartbeat_interval` 参数。
5. `test_contract_p2.py` 不在本卡“文件”清单里，但它是第二期占位的契约测试，chat 实现后必须去掉该占位项才能让全量测试通过，改动已最小化（见上）。

### 留下的问题 / 建议

- 无阻塞问题。第四期复用 `app/gateway/sse.py` 时，注意 `sse_response` 的断开取消依赖 Starlette 取消生成器；若以后升级 Starlette 到走 ASGI 2.4 路径（发送失败才抛 `ClientDisconnect`），需要补 `request.is_disconnected()` 轮询。
