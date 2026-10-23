import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { streamSSE, type SseEvent } from "../../lib/sse";
import { useModels, type Model } from "./api";
import PlaygroundPage from "./PlaygroundPage";

vi.mock("./api", () => ({ useModels: vi.fn() }));
vi.mock("../../lib/sse", () => ({ streamSSE: vi.fn() }));

const mockedUseModels = vi.mocked(useModels);
const mockedStreamSSE = vi.mocked(streamSSE);

function model(partial: Partial<Model> & Pick<Model, "id" | "name">): Model {
  return {
    model_type: "chat",
    litellm_model: "dashscope/qwen-max",
    api_base: null,
    api_key_last4: null,
    description: null,
    context_length: 128000,
    max_output: 4096,
    cap_vision: false,
    cap_tools: true,
    cap_thinking: false,
    embedding_dim: null,
    embedding_max_input: null,
    embedding_batch_size: null,
    default_params: {},
    first_token_timeout_s: 30,
    total_timeout_s: 300,
    max_retries: 2,
    rate_qps: null,
    rate_concurrency: null,
    status: "enabled",
    ref_counts: { app: 0, kb: 0, scheme: 0 },
    fields_locked: false,
    last_test_at: null,
    last_test_ok: null,
    last_test_message: null,
    created_at: "2025-10-01T00:00:00+08:00",
    updated_at: "2025-10-01T00:00:00+08:00",
    ...partial,
  };
}

interface StreamCall {
  url: string;
  body: unknown;
  options: { signal?: AbortSignal; onEvent: (event: SseEvent) => void };
}

let calls: StreamCall[];

beforeEach(() => {
  calls = [];
  mockedUseModels.mockReturnValue({
    data: {
      items: [
        model({
          id: 1,
          name: "通义千问-Max",
          default_params: { temperature: 0.5, top_p: 0.8 },
        }),
        model({ id: 2, name: "DeepSeek-V3", max_output: 2048 }),
      ],
      total: 2,
    },
    isLoading: false,
  } as unknown as ReturnType<typeof useModels>);
  mockedStreamSSE.mockImplementation((url, body, options) => {
    calls.push({ url, body, options });
    return new Promise<void>((resolve) => {
      options.signal?.addEventListener("abort", () => resolve());
    });
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <MemoryRouter>
          <PlaygroundPage />
        </MemoryRouter>
      </AntApp>
    </QueryClientProvider>,
  );
}

describe("PlaygroundPage", () => {
  it("发送时每列各发一个请求，流式追加并显示统计", async () => {
    renderPage();
    await screen.findAllByText("通义千问-Max");
    await screen.findAllByText("DeepSeek-V3");

    fireEvent.change(screen.getByPlaceholderText(/输入内容/), {
      target: { value: "差旅标准？" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发\s*送/ }));

    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls.map((call) => call.url)).toEqual([
      "/api/v1/models/1/chat",
      "/api/v1/models/2/chat",
    ]);
    const body = calls[0]!.body as {
      messages: { role: string; content: string }[];
      params: { temperature: number; top_p: number; max_tokens?: number };
    };
    expect(body.messages).toEqual([{ role: "user", content: "差旅标准？" }]);
    expect(body.params.temperature).toBe(0.5);
    expect(body.params.top_p).toBe(0.8);
    expect(body.params.max_tokens).toBe(4096);

    act(() => {
      calls[0]!.options.onEvent({ type: "TEXT_DELTA", delta: "回答一" });
      calls[1]!.options.onEvent({ type: "TEXT_DELTA", delta: "回答二" });
    });
    expect(await screen.findByText("回答一")).toBeInTheDocument();
    expect(await screen.findByText("回答二")).toBeInTheDocument();

    act(() => {
      calls[0]!.options.onEvent({
        type: "RUN_FINISHED",
        input_tokens: 18,
        output_tokens: 186,
        first_token_ms: 620,
        total_ms: 3100,
      });
    });
    await waitFor(() => {
      expect(
        screen
          .getAllByTestId("msg-stats")
          .some((node) =>
            node.textContent?.includes(
              "首字 0.62s · 总耗时 3.1s · 输入 18 / 输出 186 tokens",
            ),
          ),
      ).toBe(true);
    });
  }, 120_000);

  it("停止生成中止全部请求", async () => {
    renderPage();
    await screen.findAllByText("通义千问-Max");

    fireEvent.change(screen.getByPlaceholderText(/输入内容/), {
      target: { value: "你好" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发\s*送/ }));
    await waitFor(() => expect(calls).toHaveLength(2));

    fireEvent.click(screen.getByRole("button", { name: "停止生成" }));

    expect(calls[0]!.options.signal?.aborted).toBe(true);
    expect(calls[1]!.options.signal?.aborted).toBe(true);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /发\s*送/ })).toBeInTheDocument(),
    );
  }, 120_000);

  it("清空对话移除所有列的消息", async () => {
    renderPage();
    await screen.findAllByText("通义千问-Max");

    fireEvent.change(screen.getByPlaceholderText(/输入内容/), {
      target: { value: "你好" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发\s*送/ }));
    await waitFor(() => expect(calls).toHaveLength(2));
    act(() => {
      calls[0]!.options.onEvent({ type: "TEXT_DELTA", delta: "回答一" });
    });
    expect(await screen.findByText("回答一")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "清空对话" }));
    await waitFor(() => expect(screen.queryByText("回答一")).toBeNull());
  }, 120_000);

  it("最多 4 列，可以添加和移除", async () => {
    renderPage();
    await screen.findAllByText("通义千问-Max");

    expect(screen.getAllByTitle("移除")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: /添加对比模型（2\/4）/ }));
    fireEvent.click(screen.getByRole("button", { name: /添加对比模型（3\/4）/ }));
    fireEvent.click(screen.getByRole("button", { name: /添加对比模型（4\/4）/ }));
    expect(screen.getByRole("button", { name: /添加对比模型（4\/4）/ })).toBeDisabled();
    expect(screen.getAllByTitle("移除")).toHaveLength(4);

    fireEvent.click(screen.getAllByTitle("移除")[0]!);
    expect(screen.getAllByTitle("移除")).toHaveLength(3);
  }, 120_000);
});
