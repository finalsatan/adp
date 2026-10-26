import { fetchWithRefresh } from "../api/client";
import { ApiError, readErrorBody } from "../api/errors";
import type { components } from "../api/schema";

/** 后端 SSE 事件的判别联合（P2-01 生成，模型对话）。 */
export type SseEvent = components["schemas"]["SseEvent"];

/** 应用对话页与调试预览的事件（P4-01 生成，比 SseEvent 多 STEP_* / CITATIONS / SUGGESTIONS / TRACE）。 */
export type ChatSseEvent = components["schemas"]["ChatSseEvent"];

export type KnownSseEvent = SseEvent | ChatSseEvent;

/** 前端认识的事件类型；遇到不认识的 type 直接忽略（技术方案 9.6，向前兼容）。 */
const KNOWN_EVENT_TYPES: ReadonlySet<string> = new Set([
  "RUN_STARTED",
  "TEXT_DELTA",
  "REASONING_DELTA",
  "RUN_FINISHED",
  "RUN_ERROR",
  // P4-01：回答流程与调试预览的事件
  "STEP_STARTED",
  "STEP_FINISHED",
  "CITATIONS",
  "SUGGESTIONS",
  "TRACE",
]);

export interface StreamSSEOptions<E> {
  signal?: AbortSignal;
  onEvent: (event: E) => void;
}

/** 把一段原始事件文本（空行之间的内容）解析成事件；注释行、坏 JSON、未知类型都返回 null。 */
function parseEvent(raw: string): KnownSseEvent | null {
  const dataLines: string[] = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith(":")) {
      continue; // 心跳等注释行
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).replace(/^ /, ""));
    }
  }
  if (dataLines.length === 0) {
    return null;
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(dataLines.join("\n"));
  } catch {
    return null;
  }
  if (parsed && typeof parsed === "object" && "type" in parsed) {
    const type = (parsed as { type?: unknown }).type;
    if (typeof type === "string" && KNOWN_EVENT_TYPES.has(type)) {
      return parsed as KnownSseEvent;
    }
  }
  return null;
}

/**
 * 用 POST 读取 SSE 流（技术方案 9.6：POST 不能用 EventSource）。
 * 非 2xx 时抛出 `ApiError`；`signal` 中止时停止读取且不报错。
 * 事件类型按调用方需要选择（模型对话用 `SseEvent`，应用对话/调试用 `ChatSseEvent`）。
 */
export async function streamSSE<E extends KnownSseEvent = SseEvent>(
  url: string,
  body: unknown,
  { signal, onEvent }: StreamSSEOptions<E>,
): Promise<void> {
  const requestUrl = new URL(url, window.location.origin).toString();
  const init: RequestInit = {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify(body),
  };

  let request: Request;
  try {
    request = new Request(requestUrl, { ...init, signal });
  } catch {
    // 测试环境（jsdom）里的 AbortSignal 与 Node fetch 不同源，改用 reader.cancel 中止
    request = new Request(requestUrl, init);
  }

  let response: Response;
  try {
    response = await fetchWithRefresh(request);
  } catch (error) {
    if (signal?.aborted) {
      return; // 用户点了停止
    }
    throw error;
  }

  if (!response.ok) {
    throw ApiError.from(response.status, await readErrorBody(response));
  }
  if (!response.body) {
    throw new ApiError(response.status, { message: "响应不支持流式读取" });
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const cancelReader = () => {
    void reader.cancel().catch(() => {});
  };
  if (signal) {
    signal.addEventListener("abort", cancelReader, { once: true });
  }

  const flush = (raw: string) => {
    const event = parseEvent(raw);
    if (event) {
      onEvent(event as E);
    }
  };

  try {
    if (signal?.aborted) {
      return;
    }
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      let index = buffer.indexOf("\n\n");
      while (index !== -1) {
        flush(buffer.slice(0, index));
        buffer = buffer.slice(index + 2);
        index = buffer.indexOf("\n\n");
      }
    }
    buffer += decoder.decode();
    if (buffer.trim().length > 0) {
      flush(buffer);
    }
  } catch (error) {
    if (signal?.aborted) {
      return; // 用户点了停止
    }
    throw error;
  } finally {
    signal?.removeEventListener("abort", cancelReader);
  }
}
