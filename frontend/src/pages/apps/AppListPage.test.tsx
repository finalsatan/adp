import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useMe, type Me } from "../../api/me";
import { useOrgs } from "../../api/orgs";
import {
  APP_STATUS_OPTIONS,
  APP_TYPE_OPTIONS,
  APP_VISIBILITY_OPTIONS,
  buildAppListParams,
  usageScopeText,
  useApps,
  useCreateApp,
  useDeleteApp,
  useDisableApp,
  useEnableApp,
  type App,
} from "./api";
import AppListPage from "./AppListPage";

vi.mock("../../api/me", () => ({ useMe: vi.fn() }));
vi.mock("../../api/orgs", () => ({ useOrgs: vi.fn() }));
vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    useApps: vi.fn(),
    useCreateApp: vi.fn(),
    useDeleteApp: vi.fn(),
    useDisableApp: vi.fn(),
    useEnableApp: vi.fn(),
  };
});

const mockedUseMe = vi.mocked(useMe);
const mockedUseOrgs = vi.mocked(useOrgs);
const mockedUseApps = vi.mocked(useApps);
const mockedUseCreateApp = vi.mocked(useCreateApp);
const mockedUseDeleteApp = vi.mocked(useDeleteApp);
const mockedUseDisableApp = vi.mocked(useDisableApp);
const mockedUseEnableApp = vi.mocked(useEnableApp);

const ME: Me = {
  id: 1,
  username: "zhangsan",
  name: "张三",
  email: null,
  phone: null,
  role: "builder",
  org: { id: 1, name: "研发中心" },
  permissions: ["console.use", "resource.create"],
  must_change_password: false,
};

function app(partial: Partial<App> & Pick<App, "id" | "name">): App {
  return {
    app_type: "kb_qa",
    chat_path: `/chat/${partial.id}`,
    created_at: "2025-10-01T00:00:00+08:00",
    description: null,
    draft_has_changes: false,
    draft_updated_at: null,
    editable: true,
    manageable: true,
    org: { id: 1, name: "研发中心" },
    owner: { id: 1, username: "zhangsan", name: "张三" },
    published_version: null,
    status: "unpublished",
    updated_at: "2025-10-20T10:12:00+08:00",
    usage_scope: { scope: "platform", orgs: [], users: [] },
    visibility: "org",
    ...partial,
  };
}

function mutationStub<T>(): T {
  return { mutate: vi.fn(), reset: vi.fn(), isPending: false } as unknown as T;
}

beforeEach(() => {
  mockedUseMe.mockReturnValue({ data: ME } as unknown as ReturnType<typeof useMe>);
  mockedUseOrgs.mockReturnValue({ data: [] } as unknown as ReturnType<typeof useOrgs>);
  mockedUseApps.mockReturnValue({
    data: { items: [app({ id: 1, name: "HR 政策问答" })], total: 4 },
    isLoading: false,
  } as unknown as ReturnType<typeof useApps>);
  mockedUseDeleteApp.mockImplementation(() => mutationStub());
  mockedUseDisableApp.mockImplementation(() => mutationStub());
  mockedUseEnableApp.mockImplementation(() => mutationStub());
  mockedUseCreateApp.mockImplementation(() => mutationStub());
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
          <AppListPage />
        </MemoryRouter>
      </AntApp>
    </QueryClientProvider>,
  );
}

describe("buildAppListParams", () => {
  it("“全部”项不下发，默认只带分页", () => {
    expect(
      buildAppListParams(
        { appType: "all", status: "all", visibility: "all", orgId: undefined, keyword: "" },
        1,
        10,
      ),
    ).toEqual({ page: 1, page_size: 10 });
  });

  it("选中的筛选项拼进查询参数，组织带上含下级", () => {
    expect(
      buildAppListParams(
        {
          appType: "kb_qa",
          status: "published",
          visibility: "org",
          orgId: 3,
          keyword: "政策",
        },
        2,
        20,
      ),
    ).toEqual({
      page: 2,
      page_size: 20,
      app_type: "kb_qa",
      status: "published",
      visibility: "org",
      org_id: 3,
      include_sub: true,
      keyword: "政策",
    });
  });
});

describe("列表筛选项与文案", () => {
  it("类型、发布状态、可见范围的选项与文案", () => {
    expect(APP_TYPE_OPTIONS).toEqual([
      { value: "all", label: "全部类型" },
      { value: "kb_qa", label: "知识问答" },
    ]);
    expect(APP_STATUS_OPTIONS).toEqual([
      { value: "all", label: "全部发布状态" },
      { value: "unpublished", label: "未发布" },
      { value: "published", label: "已发布" },
      { value: "disabled", label: "已停用" },
    ]);
    expect(APP_VISIBILITY_OPTIONS).toEqual([
      { value: "all", label: "全部可见范围" },
      { value: "private", label: "仅自己" },
      { value: "org", label: "本组织" },
      { value: "platform", label: "全平台" },
    ]);
  });

  it("使用范围的中文展示：全平台 / 指定组织 / 指定用户", () => {
    expect(usageScopeText({ scope: "platform", orgs: [], users: [] })).toBe("全平台");
    expect(
      usageScopeText({
        scope: "orgs",
        orgs: [
          { id: 1, name: "研发中心" },
          { id: 2, name: "产品中心" },
        ],
        users: [],
      }),
    ).toBe("研发中心、产品中心");
    expect(
      usageScopeText({
        scope: "users",
        orgs: [],
        users: [{ id: 2, username: "lisi", name: "李四" }],
      }),
    ).toBe("李四");
  });
});

describe("AppListPage", () => {
  it("列表显示应用、发布状态与“我能查看的”数量", async () => {
    mockedUseApps.mockReturnValue({
      data: {
        items: [
          app({
            id: 1,
            name: "HR 政策问答",
            description: "员工制度、假期、社保相关问题",
            status: "published",
            draft_has_changes: true,
            published_version: { id: 9, version_no: 3, published_at: "2025-10-21T10:40:00+08:00" },
            usage_scope: {
              scope: "orgs",
              orgs: [
                { id: 1, name: "研发中心" },
                { id: 2, name: "产品中心" },
              ],
              users: [],
            },
          }),
        ],
        total: 4,
      },
      isLoading: false,
    } as unknown as ReturnType<typeof useApps>);

    renderPage();

    expect(await screen.findByText("HR 政策问答")).toBeInTheDocument();
    expect(screen.getByText("已发布")).toBeInTheDocument();
    expect(screen.getByText("草稿有修改")).toBeInTheDocument();
    expect(screen.getByText("v3 · 10-21")).toBeInTheDocument();
    expect(screen.getByText("研发中心、产品中心")).toBeInTheDocument();
    expect(screen.getByText("我能查看的 4 个")).toBeInTheDocument();
    expect(screen.getByText("新建应用")).toBeInTheDocument();
  });

  it("管理者在“···”里看到权限与设置、停用和删除", async () => {
    renderPage();
    const row = (await screen.findByText("HR 政策问答")).closest("tr");
    fireEvent.click(within(row as HTMLElement).getByLabelText("更多操作"));

    expect(await screen.findByText("发布历史")).toBeInTheDocument();
    expect(screen.getByText("对话日志")).toBeInTheDocument();
    expect(screen.getByText("用户反馈")).toBeInTheDocument();
    expect(screen.getByText("权限与设置")).toBeInTheDocument();
    expect(screen.getByText("停用")).toBeInTheDocument();
    expect(screen.getByText("删除")).toBeInTheDocument();
  });

  it("协作者（可编辑但不可管理）看不到权限与设置、停用和删除", async () => {
    mockedUseApps.mockReturnValue({
      data: { items: [app({ id: 1, name: "HR 政策问答", manageable: false })], total: 1 },
      isLoading: false,
    } as unknown as ReturnType<typeof useApps>);
    renderPage();
    const row = (await screen.findByText("HR 政策问答")).closest("tr");
    fireEvent.click(within(row as HTMLElement).getByLabelText("更多操作"));

    expect(await screen.findByText("对话日志")).toBeInTheDocument();
    expect(screen.queryByText("权限与设置")).toBeNull();
    expect(screen.queryByText("停用")).toBeNull();
    expect(screen.queryByText("删除")).toBeNull();
  });

  it("只读用户只能看到发布历史，看不到编辑与管理操作", async () => {
    mockedUseApps.mockReturnValue({
      data: {
        items: [app({ id: 1, name: "HR 政策问答", editable: false, manageable: false })],
        total: 1,
      },
      isLoading: false,
    } as unknown as ReturnType<typeof useApps>);
    renderPage();
    const row = (await screen.findByText("HR 政策问答")).closest("tr");
    fireEvent.click(within(row as HTMLElement).getByLabelText("更多操作"));

    expect(await screen.findByText("发布历史")).toBeInTheDocument();
    expect(screen.queryByText("对话日志")).toBeNull();
    expect(screen.queryByText("用户反馈")).toBeNull();
    expect(screen.queryByText("权限与设置")).toBeNull();
  });

  it("无 org.manage 权限时不请求组织、不显示组织筛选", async () => {
    renderPage();
    expect(mockedUseOrgs).toHaveBeenCalledWith({ enabled: false });
    expect(screen.queryByText("全部组织")).toBeNull();
  });

  it("组织管理员显示组织筛选", async () => {
    mockedUseMe.mockReturnValue({
      data: { ...ME, permissions: ["console.use", "resource.create", "org.manage"] },
    } as unknown as ReturnType<typeof useMe>);
    renderPage();
    expect(mockedUseOrgs).toHaveBeenCalledWith({ enabled: true });
    expect(await screen.findByText("全部组织")).toBeInTheDocument();
  });
});
