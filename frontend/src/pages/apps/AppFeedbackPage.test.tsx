import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useSearchParams } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  buildFeedbackListParams,
  logConversationPath,
  useApp,
  useAppFeedback,
  type App,
  type FeedbackListItemOut,
  type FeedbackPageOut,
} from "./api";
import AppFeedbackPage from "./AppFeedbackPage";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    useApp: vi.fn(),
    useAppFeedback: vi.fn(),
  };
});

const mockedUseApp = vi.mocked(useApp);
const mockedUseAppFeedback = vi.mocked(useAppFeedback);

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

const ITEM: FeedbackListItemOut = {
  answer_excerpt: "需要二级以上医院的诊断证明和病历。",
  answer_id: 102,
  conversation_id: 10,
  correction: "需要二级以上医院的诊断证明和病历，2023 年起不再要求提供挂号单。",
  created_at: "2025-10-21T09:48:00+08:00",
  handled: false,
  id: 1,
  org: { id: 1, name: "研发中心" },
  question: "病假超过 3 天要什么材料？",
  rating: "down",
  reasons: ["wrong_answer"],
  user: { id: 2, name: "孙七", username: "sunqi" },
};

const PAGE: FeedbackPageOut = {
  items: [ITEM],
  total: 1,
  summary: { up: 86, down: 12, down_with_correction: 5 },
};

function LogsProbe() {
  const [params] = useSearchParams();
  return <div>日志页 conv={params.get("conv")}</div>;
}

beforeEach(() => {
  mockedUseApp.mockReturnValue({ data: APP, isLoading: false } as unknown as ReturnType<typeof useApp>);
  mockedUseAppFeedback.mockReturnValue({
    data: PAGE,
    isLoading: false,
  } as unknown as ReturnType<typeof useAppFeedback>);
});

afterEach(() => {
  vi.clearAllMocks();
});

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <MemoryRouter initialEntries={["/apps/1/feedback"]}>
          <Routes>
            <Route path="/apps/:id/feedback" element={<AppFeedbackPage />} />
            <Route path="/apps/:id/logs" element={<LogsProbe />} />
          </Routes>
        </MemoryRouter>
      </AntApp>
    </QueryClientProvider>,
  );
}

describe("buildFeedbackListParams", () => {
  it("“全部”与未勾选项不下发；未处理下发 handled=false", () => {
    expect(
      buildFeedbackListParams(
        { rating: "all", hasCorrection: false, status: "all", range: "30d" },
        1,
        10,
      ),
    ).toEqual({ range: "30d", page: 1, page_size: 10 });

    expect(
      buildFeedbackListParams(
        { rating: "up", hasCorrection: true, status: "unhandled", range: "7d" },
        2,
        20,
      ),
    ).toEqual({
      rating: "up",
      has_correction: true,
      handled: false,
      range: "7d",
      page: 2,
      page_size: 20,
    });
  });
});

describe("logConversationPath", () => {
  it("带上会话参数供日志页定位", () => {
    expect(logConversationPath(1, 10)).toBe("/apps/1/logs?conv=10");
  });
});

describe("AppFeedbackPage", () => {
  it("显示统计、反馈列与“未处理”，点“对话日志”跳转并带会话参数", async () => {
    renderPage();

    expect(await screen.findByText(/点赞 86 · 点踩 12（纠错 5）/)).toBeInTheDocument();
    expect(screen.getByText("病假超过 3 天要什么材料？")).toBeInTheDocument();
    expect(screen.getAllByText("点踩").length).toBeGreaterThan(0);
    expect(screen.getByText("答案错误")).toBeInTheDocument();
    expect(screen.getByText(/2023 年起不再要求提供挂号单/)).toBeInTheDocument();
    expect(screen.getByText("未处理")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "对话日志" }));
    expect(await screen.findByText("日志页 conv=10")).toBeInTheDocument();
  });

  it("切换筛选后按新条件查询", async () => {
    renderPage();
    await screen.findByText("病假超过 3 天要什么材料？");

    fireEvent.click(screen.getByRole("radio", { name: "点赞" }));
    await waitFor(() => {
      const last = mockedUseAppFeedback.mock.calls.at(-1)?.[1];
      expect(last).toMatchObject({ rating: "up" });
    });
  });

  it("仅有查看权限时接口 403 显示无权限提示", async () => {
    mockedUseAppFeedback.mockReturnValue({
      error: { status: 403 },
      isLoading: false,
    } as unknown as ReturnType<typeof useAppFeedback>);

    renderPage();

    expect(await screen.findByTestId("feedback-forbidden")).toBeInTheDocument();
    expect(screen.getByText("没有权限查看用户反馈")).toBeInTheDocument();
  });
});
