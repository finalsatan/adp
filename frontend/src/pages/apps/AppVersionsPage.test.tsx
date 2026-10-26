import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useApp, useAppVersions, useRestoreAppVersion, type App, type AppVersionOut } from "./api";
import AppVersionsPage from "./AppVersionsPage";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    useApp: vi.fn(),
    useAppVersions: vi.fn(),
    useRestoreAppVersion: vi.fn(),
  };
});

const mockedUseApp = vi.mocked(useApp);
const mockedUseAppVersions = vi.mocked(useAppVersions);
const mockedUseRestoreAppVersion = vi.mocked(useRestoreAppVersion);

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
  draft_has_changes: true,
  draft_updated_at: null,
  usage_scope: { scope: "platform", orgs: [], users: [] },
  created_at: "2025-10-01T00:00:00+08:00",
  updated_at: "2025-10-20T10:12:00+08:00",
  editable: true,
  manageable: true,
};

const CONFIG: AppVersionOut["config"] = {
  schema_version: 1,
  app_type: "kb_qa",
  model: { chat_model_id: 12, temperature: 0.3, max_tokens: 1024, thinking: false },
  system_prompt: "你是公司的人事政策助手。",
  intent: { enabled: true, model_id: null },
  rewrite: { enabled: true, model_id: null },
  memory_turns: null,
  knowledge: {
    kbs: [{ kb_id: 101, retrieval: null, filter: null, qa_category_ids: [] }],
    doc: {
      top_k: 20,
      score_threshold: null,
      rerank: { enabled: true, model_id: 30, keep: 5 },
      neighbor_chunks: 0,
    },
    qa: {
      direct: { enabled: true, threshold: null },
      recall_threshold: null,
      recall_count: 3,
      rerank: { enabled: false, model_id: null },
    },
  },
  reply: {
    fallback_text: "未检索到相关知识，无法回答该问题。",
    opening: "",
    opening_questions: [],
    suggestions: false,
    allow_download: false,
  },
};

const VERSIONS: AppVersionOut[] = [
  {
    id: 9,
    version_no: 3,
    note: "加入“HR 常见问题”问答库",
    config: CONFIG,
    config_hash: "h3",
    published_by: { id: 1, username: "zhangsan", name: "张三" },
    published_at: "2025-10-21T16:20:00+08:00",
    is_current: true,
  },
  {
    id: 8,
    version_no: 2,
    note: "开启重排，分数阈值 0.3 → 0.35",
    config: CONFIG,
    config_hash: "h2",
    published_by: { id: 2, username: "lisi", name: "李四" },
    published_at: "2025-10-18T11:05:00+08:00",
    is_current: false,
  },
];

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <MemoryRouter initialEntries={["/apps/1/versions"]}>
          <Routes>
            <Route path="/apps/:id/versions" element={<AppVersionsPage />} />
          </Routes>
        </MemoryRouter>
      </AntApp>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockedUseApp.mockReturnValue({ data: APP, isLoading: false } as unknown as ReturnType<typeof useApp>);
  mockedUseAppVersions.mockReturnValue({
    data: { items: VERSIONS, total: 2 },
    isLoading: false,
  } as unknown as ReturnType<typeof useAppVersions>);
  mockedUseRestoreAppVersion.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useRestoreAppVersion>);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("AppVersionsPage", () => {
  it("列出发布历史并标出当前版本", async () => {
    renderPage();

    expect(await screen.findByText("v3")).toBeInTheDocument();
    expect(screen.getByText("当前")).toBeInTheDocument();
    expect(screen.getByText("v2")).toBeInTheDocument();
    expect(screen.getByText("开启重排，分数阈值 0.3 → 0.35")).toBeInTheDocument();
    expect(screen.getByText("李四")).toBeInTheDocument();
  });

  it("当前版本的“恢复为草稿”不可点", async () => {
    renderPage();

    const restoreButtons = await screen.findAllByRole("button", { name: /恢复为草稿/ });
    expect(restoreButtons[0]).toBeDisabled();
    expect(restoreButtons[1]).toBeEnabled();
  });

  it("恢复历史版本时传应用与版本号", async () => {
    const mutate = vi.fn();
    mockedUseRestoreAppVersion.mockReturnValue({ mutate, isPending: false } as unknown as ReturnType<
      typeof useRestoreAppVersion
    >);
    renderPage();

    const restoreButtons = await screen.findAllByRole("button", { name: /恢复为草稿/ });
    fireEvent.click(restoreButtons[1] as HTMLElement);

    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(mutate.mock.calls[0]?.[0]).toEqual({ id: 1, version: 2 });
  });

  it("查看配置显示版本配置摘要", async () => {
    renderPage();

    const viewButtons = await screen.findAllByRole("button", { name: /查看配置/ });
    fireEvent.click(viewButtons[1] as HTMLElement);

    expect(await screen.findByText("v2 的配置")).toBeInTheDocument();
    expect(screen.getByText("你是公司的人事政策助手。")).toBeInTheDocument();
  });
});
