import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../api/errors";
import { streamSSE, type ChatSseEvent } from "../../lib/sse";
import ChatPage from "./ChatPage";
import {
  deleteFeedback,
  fetchCitationPreview,
  putFeedback,
  useChatApp,
  useConversations,
  useCreateConversation,
  useDeleteConversation,
  useRenameConversation,
  useTurns,
  type Answer,
  type ChatAppInfo,
  type Citation,
  type Turn,
} from "./api";

vi.mock("react-pdf", async () => {
  const React = await import("react");
  return {
    Document: ({ children }: { children?: React.ReactNode }) =>
      React.createElement("div", { "data-testid": "pdf-document" }, children),
    Page: ({ children }: { children?: React.ReactNode }) =>
      React.createElement("div", { "data-testid": "pdf-page" }, children),
    pdfjs: { GlobalWorkerOptions: {} },
  };
});

vi.mock("../../lib/sse", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/sse")>();
  return { ...actual, streamSSE: vi.fn() };
});

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    useChatApp: vi.fn(),
    useConversations: vi.fn(),
    useTurns: vi.fn(),
    useCreateConversation: vi.fn(),
    useRenameConversation: vi.fn(),
    useDeleteConversation: vi.fn(),
    putFeedback: vi.fn(),
    deleteFeedback: vi.fn(),
    fetchCitationPreview: vi.fn(),
  };
});

const mockedStreamSSE = vi.mocked(streamSSE);
const mockedUseChatApp = vi.mocked(useChatApp);
const mockedUseTurns = vi.mocked(useTurns);
const mockedPutFeedback = vi.mocked(putFeedback);
const mockedDeleteFeedback = vi.mocked(deleteFeedback);

const createMutateAsync = vi.fn(async () => ({
  id: 10,
  app_id: 1,
  title: "新会话",
  created_at: "2025-10-21T10:00:00+08:00",
  last_active_at: "2025-10-21T10:00:00+08:00",
}));

const APP: ChatAppInfo = {
  id: 1,
  name: "HR 政策问答",
  description: "员工制度、假期、社保相关问题",
  opening: "你好，我是 HR 政策助手。",
  opening_questions: ["年假有多少天？"],
  suggestions: false,
  allow_download: false,
};

function citation(n: number): Citation {
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
    positions: [],
    qa_id: null,
    question: null,
  };
}

function answer(overrides: Partial<Answer> = {}): Answer {
  return {
    id: 1000,
    turn_id: 100,
    seq: 1,
    content: "工作满 1 年不满 10 年的，年休假 5 天。",
    reasoning: "",
    citations: [],
    cited: [],
    status: "done",
    kind: "generated",
    error_code: null,
    error_message: null,
    feedback: null,
    first_token_ms: null,
    input_tokens: null,
    output_tokens: null,
    suggestions: [],
    total_ms: null,
    trace: null,
    version_no: null,
    created_at: "2025-10-21T10:00:00+08:00",
    updated_at: "2025-10-21T10:00:00+08:00",
    ...overrides,
  };
}

function turn(overrides: Partial<Turn> = {}): Turn {
  return {
    id: 100,
    question: "年假有多少天？",
    rewritten_question: null,
    seq: 1,
    current_answer_id: 1000,
    created_at: "2025-10-21T10:00:00+08:00",
    answers: [answer()],
    ...overrides,
  };
}

function emitRun(onEvent: (event: ChatSseEvent) => void, conversationId = 10) {
  onEvent({
    type: "RUN_STARTED",
    run_id: "run-1",
    conversation_id: conversationId,
    turn_id: 100,
    answer_id: 1000,
  });
  onEvent({ type: "TEXT_DELTA", delta: "回答" });
  onEvent({
    type: "RUN_FINISHED",
    status: "done",
    kind: "generated",
    input_tokens: 10,
    output_tokens: 5,
    first_token_ms: 100,
    total_ms: 500,
  });
}

async function sendQuestion(question: string) {
  fireEvent.change(await screen.findByPlaceholderText("输入问题，Ctrl+Enter 发送"), {
    target: { value: question },
  });
  fireEvent.click(screen.getByRole("button", { name: /发\s*送/ }));
}

function renderChat(initialEntry = "/chat/1") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <MemoryRouter initialEntries={[initialEntry]}>
          <Routes>
            <Route path="/chat/:appId" element={<ChatPage />} />
            <Route path="/chat/:appId/:conversationId" element={<ChatPage />} />
          </Routes>
        </MemoryRouter>
      </AntApp>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  mockedUseChatApp.mockReturnValue({
    data: APP,
    isLoading: false,
  } as unknown as ReturnType<typeof useChatApp>);
  vi.mocked(useConversations).mockReturnValue({
    data: { items: [], total: 0 },
    isLoading: false,
  } as unknown as ReturnType<typeof useConversations>);
  mockedUseTurns.mockReturnValue({
    data: undefined,
    isLoading: false,
  } as unknown as ReturnType<typeof useTurns>);
  vi.mocked(useCreateConversation).mockReturnValue({
    mutateAsync: createMutateAsync,
  } as unknown as ReturnType<typeof useCreateConversation>);
  vi.mocked(useRenameConversation).mockReturnValue({
    mutate: vi.fn(),
  } as unknown as ReturnType<typeof useRenameConversation>);
  vi.mocked(useDeleteConversation).mockReturnValue({
    mutate: vi.fn(),
  } as unknown as ReturnType<typeof useDeleteConversation>);
  mockedPutFeedback.mockResolvedValue({} as never);
  mockedDeleteFeedback.mockResolvedValue(undefined);
  vi.mocked(fetchCitationPreview).mockResolvedValue({
    kind: "json",
    preview: {
      render: "markdown",
      file_type: "word",
      kb_id: 101,
      kb_name: "员工制度库",
      doc_id: 3021,
      doc_name: "员工考勤管理制度.docx",
      page_no: null,
      pages: [],
      positions: [],
      content: "第十八条……",
      elements: null,
      generation: 2,
      edited: false,
      stale: false,
      note: null,
      download_allowed: false,
    },
  });
  mockedStreamSSE.mockReset();
  createMutateAsync.mockClear();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("ChatPage", () => {
  it("第一次提问先建会话，由 RUN_STARTED 改写地址且不刷新", async () => {
    mockedStreamSSE.mockImplementation(async (_url, _body, { onEvent }) => {
      emitRun(onEvent);
    });
    renderChat();

    await sendQuestion("年假有多少天？");

    await waitFor(() => expect(window.location.pathname).toBe("/chat/1/10"));
    expect(createMutateAsync).toHaveBeenCalledTimes(1);
    expect(mockedStreamSSE.mock.calls[0]?.[0]).toBe("/api/v1/conversations/10/turns");
  });

  it("停止生成中止请求的 AbortSignal", async () => {
    let captured: AbortSignal | undefined;
    mockedStreamSSE.mockImplementation(
      (_url, _body, { signal, onEvent }) =>
        new Promise<void>((resolve) => {
          captured = signal;
          onEvent({
            type: "RUN_STARTED",
            run_id: "run-1",
            conversation_id: 10,
            turn_id: 100,
            answer_id: 1000,
          });
          signal?.addEventListener("abort", () => resolve());
        }),
    );
    renderChat();
    await sendQuestion("问题");

    fireEvent.click(await screen.findByRole("button", { name: "停止生成" }));
    await waitFor(() => expect(captured?.aborted).toBe(true));
  });

  it("同一会话并发提问时提示上一个回答还在生成", async () => {
    mockedStreamSSE.mockRejectedValue(
      new ApiError(409, { code: "CONVERSATION_BUSY", message: "上一个回答还在生成" }),
    );
    renderChat();
    await sendQuestion("问题");

    expect(await screen.findByText("上一个回答还在生成")).toBeInTheDocument();
  });

  it("点踩原因与纠错内容按结构提交", async () => {
    mockedStreamSSE.mockImplementation(async (_url, _body, { onEvent }) => {
      emitRun(onEvent);
    });
    renderChat();
    await sendQuestion("问题");

    fireEvent.click(await screen.findByRole("button", { name: "点踩" }));
    fireEvent.click(await screen.findByText("答案错误"));
    fireEvent.change(screen.getByPlaceholderText(/纠错/), {
      target: { value: "正确答案" },
    });
    fireEvent.click(screen.getByTestId("feedback-submit"));

    await waitFor(() =>
      expect(mockedPutFeedback).toHaveBeenCalledWith(1000, {
        rating: "down",
        reasons: ["wrong_answer"],
        correction: "正确答案",
      }),
    );
  });

  it("回答下方引用列表只显示 cited 的条目", async () => {
    mockedStreamSSE.mockImplementation(async (_url, _body, { onEvent }) => {
      onEvent({
        type: "RUN_STARTED",
        run_id: "run-1",
        conversation_id: 10,
        turn_id: 100,
        answer_id: 1000,
      });
      onEvent({ type: "CITATIONS", citations: [citation(1), citation(2)] });
      onEvent({ type: "TEXT_DELTA", delta: "见 [1]。" });
      onEvent({
        type: "RUN_FINISHED",
        status: "done",
        kind: "generated",
        input_tokens: 1,
        output_tokens: 1,
        first_token_ms: 1,
        total_ms: 1,
      });
    });
    renderChat();
    await sendQuestion("问题");

    expect(await screen.findByTestId("answer-citation-1")).toBeInTheDocument();
    expect(screen.queryByTestId("answer-citation-2")).toBeNull();
  });

  it("重新生成调 turns:regenerate，历史消息照常显示", async () => {
    mockedUseTurns.mockReturnValue({
      data: [turn()],
      isLoading: false,
    } as unknown as ReturnType<typeof useTurns>);
    mockedStreamSSE.mockImplementation(async () => {});
    renderChat("/chat/1/10");

    expect(await screen.findByText("年假有多少天？")).toBeInTheDocument();
    expect(screen.getByText(/年休假 5 天/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /重新生成/ }));
    await waitFor(() =>
      expect(mockedStreamSSE).toHaveBeenCalledWith(
        "/api/v1/turns/100:regenerate",
        undefined,
        expect.anything(),
      ),
    );
  });

  it("不在使用范围显示没有权限，未发布显示应用暂不可用", () => {
    mockedUseChatApp.mockReturnValue({
      error: new ApiError(403, { code: "APP_NOT_IN_SCOPE", message: "你没有使用该应用的权限" }),
      isLoading: false,
    } as unknown as ReturnType<typeof useChatApp>);
    const { unmount } = renderChat();
    expect(screen.getByText("你没有使用该应用的权限")).toBeInTheDocument();
    unmount();

    mockedUseChatApp.mockReturnValue({
      error: new ApiError(403, { code: "APP_NOT_PUBLISHED", message: "应用尚未发布" }),
      isLoading: false,
    } as unknown as ReturnType<typeof useChatApp>);
    renderChat();
    expect(screen.getByText("应用暂不可用")).toBeInTheDocument();
  });
});
