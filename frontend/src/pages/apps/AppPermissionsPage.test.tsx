import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useOrgTree } from "../../api/orgs";
import { useUserSearch } from "../../components/resource/api";
import {
  buildUsageScopeIn,
  USAGE_SCOPE_OPTIONS,
  useApp,
  useAppUsageScope,
  useDeleteApp,
  useDisableApp,
  useEnableApp,
  useUpdateApp,
  useUpdateAppUsageScope,
  type App,
} from "./api";
import AppPermissionsPage from "./AppPermissionsPage";

vi.mock("../../api/orgs", () => ({ useOrgTree: vi.fn() }));
vi.mock("../../components/resource/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../components/resource/api")>();
  return { ...actual, useUserSearch: vi.fn() };
});
vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    useApp: vi.fn(),
    useAppUsageScope: vi.fn(),
    useUpdateAppUsageScope: vi.fn(),
    useUpdateApp: vi.fn(),
    useDeleteApp: vi.fn(),
    useDisableApp: vi.fn(),
    useEnableApp: vi.fn(),
  };
});

const mockedUseOrgTree = vi.mocked(useOrgTree);
const mockedUseUserSearch = vi.mocked(useUserSearch);
const mockedUseApp = vi.mocked(useApp);
const mockedUseAppUsageScope = vi.mocked(useAppUsageScope);
const mockedUseUpdateAppUsageScope = vi.mocked(useUpdateAppUsageScope);
const mockedUseUpdateApp = vi.mocked(useUpdateApp);
const mockedUseDeleteApp = vi.mocked(useDeleteApp);
const mockedUseDisableApp = vi.mocked(useDisableApp);
const mockedUseEnableApp = vi.mocked(useEnableApp);

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
  mockedUseAppUsageScope.mockReturnValue({
    data: { scope: "platform", orgs: [], users: [] },
  } as unknown as ReturnType<typeof useAppUsageScope>);
  mockedUseUpdateAppUsageScope.mockImplementation(() => mutationStub());
  mockedUseUpdateApp.mockImplementation(() => mutationStub());
  mockedUseDeleteApp.mockImplementation(() => mutationStub());
  mockedUseDisableApp.mockImplementation(() => mutationStub());
  mockedUseEnableApp.mockImplementation(() => mutationStub());
  mockedUseOrgTree.mockReturnValue({
    data: [
      { id: 1, parent_id: null, name: "研发中心" },
      { id: 2, parent_id: 1, name: "平台组" },
    ],
  } as unknown as ReturnType<typeof useOrgTree>);
  mockedUseUserSearch.mockReturnValue({ data: [] } as unknown as ReturnType<typeof useUserSearch>);
});

afterEach(() => {
  vi.clearAllMocks();
});

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <MemoryRouter initialEntries={["/apps/1/permissions"]}>
          <Routes>
            <Route path="/apps/:id/permissions" element={<AppPermissionsPage />} />
          </Routes>
        </MemoryRouter>
      </AntApp>
    </QueryClientProvider>,
  );
}

/** 使用范围卡片的容器，避免和基本信息的“保存”按钮混淆。 */
function usageScopeCard(): HTMLElement {
  return screen.getByText("使用范围").closest(".ant-card") as HTMLElement;
}

describe("buildUsageScopeIn", () => {
  it("三档的提交结构：非当前档的名单置空", () => {
    expect(buildUsageScopeIn("platform", [1, 2], [3])).toEqual({
      scope: "platform",
      org_ids: [],
      user_ids: [],
    });
    expect(buildUsageScopeIn("orgs", [1, 2], [3])).toEqual({
      scope: "orgs",
      org_ids: [1, 2],
      user_ids: [],
    });
    expect(buildUsageScopeIn("users", [1, 2], [3])).toEqual({
      scope: "users",
      org_ids: [],
      user_ids: [3],
    });
  });
});

describe("使用范围文案", () => {
  it("三档的标题与说明照原型", () => {
    expect(USAGE_SCOPE_OPTIONS).toEqual([
      { value: "platform", label: "全平台", description: "所有登录用户都能使用" },
      {
        value: "orgs",
        label: "指定组织",
        description: "所选组织及其下级组织的用户能使用",
      },
      { value: "users", label: "指定用户", description: "只有所选用户能使用" },
    ]);
  });
});

describe("AppPermissionsPage", () => {
  it("管理者能看到使用范围三档、可见范围、协作者与停用删除", async () => {
    renderPage();

    expect(await screen.findByText("全平台")).toBeInTheDocument();
    expect(screen.getByText("指定组织")).toBeInTheDocument();
    expect(screen.getByText("指定用户")).toBeInTheDocument();
    expect(screen.getByText("所有登录用户都能使用")).toBeInTheDocument();
    expect(screen.getByText("所选组织及其下级组织的用户能使用")).toBeInTheDocument();
    expect(screen.getByText("只有所选用户能使用")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "修改可见范围" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "管理协作者" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /停\s*用/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /删\s*除/ })).toBeInTheDocument();
  });

  it("保存使用范围时提交当前档与名单", async () => {
    const mutate = vi.fn();
    mockedUseUpdateAppUsageScope.mockReturnValue({
      mutate,
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateAppUsageScope>);

    renderPage();
    fireEvent.click(
      await within(usageScopeCard()).findByRole("button", { name: /保\s*存/ }),
    );

    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(mutate.mock.calls[0]![0]).toEqual({
      id: 1,
      body: { scope: "platform", org_ids: [], user_ids: [] },
    });
  });

  it("切到“指定组织”时出现组织选择", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("radio", { name: /指定组织/ }));
    expect(await screen.findByText("选择组织（含下级组织）")).toBeInTheDocument();
  });

  it("指定组织用只读组织树接口取组织，展示上下级", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("radio", { name: /指定组织/ }));
    fireEvent.mouseDown(screen.getByRole("combobox"));
    expect(await screen.findByText("平台组")).toBeInTheDocument();
    expect(mockedUseOrgTree).toHaveBeenCalled();
  });

  it("切到“指定用户”时出现用户搜索", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("radio", { name: /指定用户/ }));
    expect(
      await screen.findByText("搜索用户名或姓名添加用户"),
    ).toBeInTheDocument();
  });

  it("协作者不能改使用范围、可见范围、协作者，也不能停用删除", async () => {
    mockedUseApp.mockReturnValue({
      data: { ...APP, manageable: false },
      isLoading: false,
    } as unknown as ReturnType<typeof useApp>);
    renderPage();

    expect(await screen.findByRole("radio", { name: /全平台/ })).toBeDisabled();
    expect(within(usageScopeCard()).queryByRole("button", { name: /保\s*存/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "修改可见范围" })).toBeNull();
    expect(screen.queryByRole("button", { name: "管理协作者" })).toBeNull();
    expect(screen.queryByRole("button", { name: /停\s*用/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /删\s*除/ })).toBeNull();
  });
});
