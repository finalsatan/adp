# P2-07 模型体验 SSE 接口

状态：待开始
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

- [ ] 测试 `test_playground.py`（模拟 LiteLLM）：正常对话的事件顺序和内容正确，响应头含 `X-Accel-Buffering: no`；思考内容以 `REASONING_DELTA` 输出；上游报错时最后一条是 `RUN_ERROR`；向量化模型返回 422；停用模型返回 409；用量记录 `source=playground`。
- [ ] 测试 `test_sse.py`：
  - 把心跳间隔调成 0.2 秒、让假模型 1 秒后才出第一个字：收到首个事件前已有若干 `: ping`。
  - **断开**：在测试里用 uvicorn 在随机端口起真实服务，httpx 读到第一个 `TEXT_DELTA` 后关闭连接；断言假模型的生成被取消（假模型里记录取消标志）、并发租约被释放、用量记录为 `CANCELLED`（技术方案 13.2：中途停止生成记录 CANCELLED）。
- [ ] `make backend-lint backend-test` 通过。

## 注意

- 不要用 `BaseHTTPMiddleware`（P1-01 已说明），它会让断开检测和流式输出出问题。如果断开测试过不了，先检查中间件。

## 问题

## 完成记录
