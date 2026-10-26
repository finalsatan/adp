import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  buildLogListParams,
  useApp,
  useLogConversation,
  useLogConversations,
  type App,
  type LogAnswerOut,
  type LogConversationItemOut,
  type LogConversationOut,
} from "./api";
import AppLogsPage from "./AppLogsPage";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    useApp: vi.fn(),
    useLogConversations: vi.fn(),
    useLogConversation: vi.fn(),
  };
});

const mockedUseApp = vi.mocked(useApp);
const mockedUseLogConversations = vi.mocked(useLogConversations);
const mockedUseLogConversation = vi.mocked(useLogConversation);

const APP: App = {
  id: 1,
  name: "HR 政策问答",
  description: null,
  app_type: "kb_qa",
  chat_path: "/chat/1",
  org: { id: 1, name: "研发中心" },
  owner: { id: 1, username: "zhangsan", name: "张三" },
  visibility: "org",
  status: "published",
  published_version: { id: 9, version_no: 3, published_at: "2025-10-21T10:40:00+08:00" },
  draft_has_changes: false,
  draft_updated_at: null,
  usage_scope: { scope: "platform", orgs: [], users: [] },
  created_at: "2025-10-01T00:00:00+08:00",
  updated_at: "2025-10-20T10:12:00+08:00",
  editable: true,
  manageable: true,
};

const ORG = { id: 1, name: "研发中心" };
const USER = { id: 2, name: "王五", username: "wangwu" };

function conversationItem(
  partial: Partial<LogConversationItemOut> & Pick<LogConversationItemOut, "conversation_id">,
): LogConversationItemOut {
  return {
    app_id: 1,
    has_down_feedback: false,
    has_feedback: false,
    org: ORG,
    started_at: "2025-10-21T10:30:00+08:00",
    title: "年假折算",
    turn_count: 2,
    user: USER,
    user_deleted: false,
    version_no: 3,
    ...partial,
  };
}

function answer(partial: Partial<LogAnswerOut> & Pick<LogAnswerOut, "id">): LogAnswerOut {
  return {
    citations: [],
    cited: [],
    content: "回答内容",
    created_at: "2025-10-21T10:30:00+08:00",
    error_code: null,
    error_message: null,
    feedback: null,
    first_token_ms: null,
    input_tokens: null,
    kind: "generated",
    output_tokens: null,
    reasoning: "",
    seq: 1,
    status: "done",
    suggestions: [],
    total_ms: 3800,
    trace: null,
    turn_id: 1,
    updated_at: "2025-10-21T10:30:00+08:00",
    version_no: 3,
    ...partial,
  };
}

const DETAIL: LogConversationOut = {
  app_id: 1,
  conversation_id: 10,
  created_at: "2025-10-21T10:30:00+08:00",
  org: ORG,
  title: "年假折算",
  user: USER,
  user_deleted: false,
  turns: [
    {
      id: 1,
      created_at: "2025-10-21T10:30:00+08:00",
      current_answer_id: 102,
      question: "我今年 7 月入职的，今年能休几天？",
      rewritten_question: null,
      seq: 1,
      answers: [
        answer({ id: 101, content: "旧回答", seq: 1 }),
        answer({
          id: 102,
          content: "新回答",
          seq: 2,
          total_ms: 2600,
          trace: {
            answer_id: 102,
            app_id: 1,
            created_at: "2025-10-21T10:31:00+08:00",
            steps: [
              {
                name: "generate",
                duration_ms: 2600,
                input: { messages: [{ role: "system", content: "你是公司的人事政策助手。" }] },
                output: "",
                input_tokens: 2688,
                output_tokens: 121,
                model_id: 1,
                model_name: "通义千问-Max",
                prompt_version: "v1",
                started_at: "2025-10-21T10:31:00+08:00",
              },
            ],
          },
        }),
      ],
    },
  ],
};

beforeEach(() => {
  mockedUseApp.mockReturnValue({ data: APP, isLoading: false } as unknown as ReturnType<typeof useApp>);
  mockedUseLogConversations.mockReturnValue({
    data: {
      items: [
        conversationItem({ conversation_id: 10 }),
        conversationItem({
          conversation_id: 11,
          title: "离职证明",
          has_down_feedback: true,
          has_feedback: true,
          user_deleted: true,
          user: { id: 3, name: "周八", username: "zhouba" },
        }),
      ],
      total: 2,
    },
    isLoading: false,
  } as unknown as ReturnType<typeof useLogConversations>);
  mockedUseLogConversation.mockReturnValue({
    data: DETAIL,
    isLoading: false,
  } as unknown as ReturnType<typeof useLogConversation>);
});

afterEach(() => {
  vi.clearAllMocks();
});

function renderPage(initialEntry = "/apps/1/logs?conv=10") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <MemoryRouter initialEntries={[initialEntry]}>
          <Routes>
            <Route path="/apps/:id/logs" element={<AppLogsPage />} />
          </Routes>
        </MemoryRouter>
      </AntApp>
    </QueryClientProvider>,
  );
}

describe("buildLogListParams", () => {
  it("带上时间、反馈筛选与分页，关键词为空不下发", () => {
    expect(buildLogListParams("7d", "all", 1, 50)).toEqual({
      range: "7d",
      feedback: "all",
      page: 1,
      page_size: 50,
    });
    expect(buildLogListParams("30d", "down", 2, 10, "  年假  ")).toEqual({
      range: "30d",
      feedback: "down",
      page: 2,
      page_size: 10,
      keyword: "年假",
    });
  });
});

describe("AppLogsPage", () => {
  it("会话列表标注“用户已删除”与点踩，详情显示旧回答与处理过程", async () => {
    renderPage();

    expect(await screen.findByText("王五")).toBeInTheDocument();
    expect(screen.getByText("用户已删除")).toBeInTheDocument();
    expect(screen.getByTestId("log-conv-11")).toBeInTheDocument();

    expect(await screen.findByText("已重新生成")).toBeInTheDocument();
    expect(screen.getByTestId("log-answer-101")).toBeInTheDocument();
    expect(screen.getByTestId("log-trace-102")).toBeInTheDocument();
    expect(screen.getByTestId("log-trace-step-generate")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "查看完整提示词" }));
    expect(await screen.findByText(/你是公司的人事政策助手/)).toBeInTheDocument();
  });

  it("仅有查看权限时接口 403 显示无权限提示", async () => {
    mockedUseLogConversations.mockReturnValue({
      error: { status: 403 },
      isLoading: false,
    } as unknown as ReturnType<typeof useLogConversations>);

    renderPage();

    expect(await screen.findByTestId("logs-forbidden")).toBeInTheDocument();
    expect(screen.getByText("没有权限查看对话日志")).toBeInTheDocument();
  });
});
