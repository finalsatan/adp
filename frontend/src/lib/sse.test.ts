import { afterEach, describe, expect, it, vi } from "vitest";

import { streamSSE, type SseEvent } from "./sse";

const encoder = new TextEncoder();

function jsonResponse(status: number, body: unknown = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function streamResponse(chunks: string[]): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

function requestUrl(input: RequestInfo | URL): string {
  return input instanceof Request ? input.url : String(input);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("streamSSE", () => {
  it("处理被拆开的、多个、注释和未知类型的事件", async () => {
    const chunks = [
      // 一个事件被拆在两个数据块里
      'data: {"type":"TEXT_DELTA","delta":"你',
      '"}\n\n',
      // 一个数据块里有多个事件，夹杂注释行
      ': ping\n\ndata: {"type":"REASONING_DELTA","delta":"想"}\n\n',
      'data: {"type":"RUN_FINISHED","input_tokens":18,"output_tokens":186,',
      '"first_token_ms":620,"total_ms":3100}\n\n',
      // 不认识的事件类型
      'data: {"type":"FUTURE_EVENT","x":1}\n\n',
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => streamResponse(chunks)),
    );

    const events: SseEvent[] = [];
    await streamSSE("/api/v1/models/1/chat", { messages: [] }, {
      onEvent: (event) => events.push(event),
    });

    expect(events).toEqual([
      { type: "TEXT_DELTA", delta: "你" },
      { type: "REASONING_DELTA", delta: "想" },
      {
        type: "RUN_FINISHED",
        input_tokens: 18,
        output_tokens: 186,
        first_token_ms: 620,
        total_ms: 3100,
      },
    ]);
  });

  it("非 2xx 时抛出错误 JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(409, { code: "MODEL_UNAVAILABLE", message: "模型不可用（已停用或已删除）" }),
      ),
    );

    await expect(
      streamSSE("/api/v1/models/1/chat", {}, { onEvent: () => {} }),
    ).rejects.toMatchObject({
      status: 409,
      code: "MODEL_UNAVAILABLE",
      message: "模型不可用（已停用或已删除）",
    });
  });

  it("中途中止时停止读取且不报错", async () => {
    const controller = new AbortController();
    const stream = new ReadableStream<Uint8Array>({
      start(streamController) {
        streamController.enqueue(encoder.encode('data: {"type":"TEXT_DELTA","delta":"你"}\n\n'));
        // 不 close，模拟还在生成
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(stream, { status: 200 })),
    );

    const events: SseEvent[] = [];
    const pending = streamSSE("/api/v1/models/1/chat", {}, {
      signal: controller.signal,
      onEvent: (event) => events.push(event),
    });

    await vi.waitFor(() => expect(events).toHaveLength(1));
    controller.abort();
    await expect(pending).resolves.toBeUndefined();
    expect(events).toHaveLength(1);
  });

  it("首次请求 401 时经刷新后重试", async () => {
    let chatCalls = 0;
    let refreshCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url.includes("/api/v1/auth/refresh")) {
        refreshCalls += 1;
        return jsonResponse(200, { must_change_password: false });
      }
      chatCalls += 1;
      if (chatCalls === 1) {
        return jsonResponse(401, { code: "AUTH_REQUIRED" });
      }
      return streamResponse(['data: {"type":"TEXT_DELTA","delta":"好"}\n\n']);
    });
    vi.stubGlobal("fetch", fetchMock);

    const events: SseEvent[] = [];
    await streamSSE("/api/v1/models/1/chat", { messages: [] }, {
      onEvent: (event) => events.push(event),
    });

    expect(refreshCalls).toBe(1);
    expect(chatCalls).toBe(2);
    expect(events).toEqual([{ type: "TEXT_DELTA", delta: "好" }]);
  });
});
