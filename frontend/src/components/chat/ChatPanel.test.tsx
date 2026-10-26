import { App as AntApp } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ChatPanel, buildDebugHistory, collectChunkIds, type ChatRunRequest } from "./ChatPanel";
import type { Citation } from "./citation";

function citation(n: number, overrides: Partial<Citation> = {}): Citation {
  return {
    n,
    type: "doc",
    kb_id: 101,
    kb_name: "员工制度库",
    score: 0.83,
    answer: null,
    chunk_ids: [5512],
    content: "第十八条……",
    direct: null,
    doc_id: 3021,
    doc_name: "员工考勤管理制度.docx",
    edited: false,
    file_type: "word",
    generation: 2,
    positions: null,
    qa_id: null,
    question: null,
    ...overrides,
  };
}

function renderPanel(overrides: Partial<Parameters<typeof ChatPanel>[0]> = {}) {
  const startRun = overrides.startRun ?? vi.fn(async () => {});
  const result = render(
    <AntApp>
      <ChatPanel debug startRun={startRun} {...overrides} />
    </AntApp>,
  );
  return { ...result, startRun };
}

async function sendQuestion(question: string) {
  const button = await screen.findByRole("button", { name: /发\s*送/ });
  fireEvent.change(screen.getByPlaceholderText("输入问题，Ctrl+Enter 发送"), {
    target: { value: question },
  });
  fireEvent.click(button);
}

describe("ChatPanel", () => {
  it("流式渲染回答、引用角标与处理过程", async () => {
    const startRun = vi.fn(async ({ onEvent }: ChatRunRequest) => {
      onEvent({ type: "CITATIONS", citations: [citation(1)] });
      onEvent({ type: "TEXT_DELTA", delta: "一类城市住宿费" });
      onEvent({ type: "TEXT_DELTA", delta: "不超过 500 元[1]。" });
      onEvent({ type: "STEP_STARTED", step: "retrieve" });
      onEvent({ type: "STEP_FINISHED", step: "retrieve", duration_ms: 300 });
      onEvent({ type: "STEP_STARTED", step: "generate" });
      onEvent({
        type: "TRACE",
        trace: {
          answer_id: 1,
          app_id: 1,
          created_at: "2025-10-21T10:40:00+08:00",
          steps: [
            {
              name: "generate",
              duration_ms: 2100,
              input: { messages: [{ role: "system", content: "你是助手" }] },
              output: "",
              input_tokens: 2134,
              output_tokens: 96,
              model_id: 1,
              model_name: "通义千问-Max",
              prompt_version: "v1",
              started_at: "2025-10-21T10:40:00+08:00",
            },
          ],
        },
      });
      onEvent({
        type: "RUN_FINISHED",
        status: "done",
        kind: "generated",
        input_tokens: 2134,
        output_tokens: 96,
        first_token_ms: 800,
        total_ms: 3400,
      });
    });

    renderPanel({ startRun });
    await sendQuestion("去上海出差住酒店最多报多少？");

    expect(await screen.findByText(/不超过 500 元/)).toBeInTheDocument();
    expect(screen.getByTestId("citation-1")).toBeInTheDocument();
    expect(screen.getByTestId("chat-trace")).toBeInTheDocument();
    expect(screen.getByTestId("trace-step-generate")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "查看完整提示词" }));
    expect(await screen.findByText(/你是助手/)).toBeInTheDocument();
  });

  it("非 debug 模式不显示处理过程", async () => {
    const startRun = vi.fn(async ({ onEvent }: ChatRunRequest) => {
      onEvent({ type: "TEXT_DELTA", delta: "回答" });
      onEvent({
        type: "TRACE",
        trace: { answer_id: 1, app_id: 1, created_at: "2025-10-21T10:40:00+08:00", steps: [] },
      });
    });
    renderPanel({ startRun, debug: false });
    await sendQuestion("问题");
    await screen.findByText("回答");
    expect(screen.queryByTestId("chat-trace")).toBeNull();
  });

  it("停止生成中止请求", async () => {
    let captured: AbortSignal | null = null;
    const startRun = vi.fn(
      ({ signal }: ChatRunRequest) =>
        new Promise<void>((resolve) => {
          captured = signal;
          signal.addEventListener("abort", () => resolve());
        }),
    );
    renderPanel({ startRun });
    await sendQuestion("问题");

    const stop = await screen.findByRole("button", { name: "停止生成" });
    fireEvent.click(stop);

    await waitFor(() => expect(captured?.aborted).toBe(true));
  });

  it("重新生成用最后一问再发一次", async () => {
    const startRun = vi.fn(async ({ onEvent }: ChatRunRequest) => {
      onEvent({ type: "TEXT_DELTA", delta: "回答" });
    });
    renderPanel({ startRun });
    await sendQuestion("第一问");
    await waitFor(() => expect(startRun).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: /重新生成/ }));
    await waitFor(() => expect(startRun).toHaveBeenCalledTimes(2));
    expect(startRun.mock.calls[1]?.[0].question).toBe("第一问");
  });

  it("第二轮带上第一轮的历史", async () => {
    const startRun = vi.fn(async ({ onEvent }: ChatRunRequest) => {
      onEvent({ type: "TEXT_DELTA", delta: "回答" });
    });
    renderPanel({ startRun });
    await sendQuestion("第一问");
    await waitFor(() => expect(startRun).toHaveBeenCalledTimes(1));
    await sendQuestion("第二问");
    await waitFor(() => expect(startRun).toHaveBeenCalledTimes(2));

    expect(startRun.mock.calls[1]?.[0].history).toEqual([
      { role: "user", content: "第一问" },
      { role: "assistant", content: "回答" },
    ]);
  });
});

describe("buildDebugHistory", () => {
  it("最多最近 20 轮（40 条），空内容不计", () => {
    const messages = Array.from({ length: 45 }, (_, index) => ({
      role: index % 2 === 0 ? ("user" as const) : ("assistant" as const),
      content: `m${index}`,
    }));
    const history = buildDebugHistory(messages);
    expect(history).toHaveLength(40);
    expect(history[0]).toEqual({ role: "assistant", content: "m5" });
    expect(history.at(-1)).toEqual({ role: "user", content: "m44" });

    expect(
      buildDebugHistory([
        { role: "user", content: "  " },
        { role: "assistant", content: "有内容" },
      ]),
    ).toEqual([{ role: "assistant", content: "有内容" }]);
  });
});

describe("collectChunkIds", () => {
  it("从处理过程输出里收集分块 ID", () => {
    expect(
      collectChunkIds({ hits: [{ chunk_id: 1 }, { chunk_ids: [2, 3] }], note: "x" }),
    ).toEqual([1, 2, 3]);
  });
});
