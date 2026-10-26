import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useApp, useUpdateApp, type App } from "./api";
import AppSettingsPage from "./AppSettingsPage";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    useApp: vi.fn(),
    useUpdateApp: vi.fn(),
  };
});

const mockedUseApp = vi.mocked(useApp);
const mockedUseUpdateApp = vi.mocked(useUpdateApp);

const APP: App = {
  id: 1,
  name: "HR 政策问答",
  description: "员工制度、假期、社保相关问题",
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

function mutationStub<T>(): T {
  return { mutate: vi.fn(), reset: vi.fn(), isPending: false } as unknown as T;
}

beforeEach(() => {
  mockedUseApp.mockReturnValue({ data: APP, isLoading: false } as unknown as ReturnType<typeof useApp>);
  mockedUseUpdateApp.mockImplementation(() => mutationStub());
});

afterEach(() => {
  vi.clearAllMocks();
});

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <MemoryRouter initialEntries={["/apps/1/settings"]}>
          <Routes>
            <Route path="/apps/:id/settings" element={<AppSettingsPage />} />
          </Routes>
        </MemoryRouter>
      </AntApp>
    </QueryClientProvider>,
  );
}

describe("AppSettingsPage", () => {
  it("类型只读并提示创建后不可修改", async () => {
    renderPage();

    expect(await screen.findByText("HR 政策问答")).toBeInTheDocument();
    expect(screen.getAllByText("知识问答").length).toBeGreaterThan(0);
    expect(screen.getByText("创建后不可修改")).toBeInTheDocument();
    expect(screen.getByDisplayValue("员工制度、假期、社保相关问题")).toBeInTheDocument();
  });

  it("保存时调 PATCH，提交名称与描述", async () => {
    const mutate = vi.fn();
    mockedUseUpdateApp.mockReturnValue({ mutate, isPending: false } as unknown as ReturnType<
      typeof useUpdateApp
    >);

    renderPage();
    fireEvent.change(await screen.findByLabelText("名称"), {
      target: { value: "HR 政策问答 v2" },
    });
    fireEvent.change(screen.getByLabelText("描述"), { target: { value: "新的描述" } });
    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(mutate.mock.calls[0]![0]).toEqual({
      id: 1,
      body: { name: "HR 政策问答 v2", description: "新的描述" },
    });
  });

  it("只读用户看不到保存按钮、输入框禁用", async () => {
    mockedUseApp.mockReturnValue({
      data: { ...APP, editable: false },
      isLoading: false,
    } as unknown as ReturnType<typeof useApp>);
    renderPage();

    expect(await screen.findByLabelText("名称")).toBeDisabled();
    expect(screen.queryByRole("button", { name: /保\s*存/ })).toBeNull();
  });
});
