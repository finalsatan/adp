import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useMe, type Me } from "../../api/me";
import { useOrgs } from "../../api/orgs";
import {
  buildKbListParams,
  KIND_OPTIONS,
  STATUS_OPTIONS,
  VISIBILITY_OPTIONS,
  useDeleteKb,
  useDisableKb,
  useEmbeddingModels,
  useEnableKb,
  useKbRefs,
  useKbs,
  type Kb,
} from "./api";
import KbListPage from "./KbListPage";

vi.mock("../../api/me", () => ({ useMe: vi.fn() }));
vi.mock("../../api/orgs", () => ({ useOrgs: vi.fn() }));
vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    useKbs: vi.fn(),
    useDeleteKb: vi.fn(),
    useEnableKb: vi.fn(),
    useEmbeddingModels: vi.fn(),
    useKbRefs: vi.fn(),
    useDisableKb: vi.fn(),
  };
});

const mockedUseMe = vi.mocked(useMe);
const mockedUseOrgs = vi.mocked(useOrgs);
const mockedUseKbs = vi.mocked(useKbs);
const mockedUseDeleteKb = vi.mocked(useDeleteKb);
const mockedUseEnableKb = vi.mocked(useEnableKb);
const mockedUseEmbeddingModels = vi.mocked(useEmbeddingModels);
const mockedUseKbRefs = vi.mocked(useKbRefs);
const mockedUseDisableKb = vi.mocked(useDisableKb);

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

function kb(partial: Partial<Kb> & Pick<Kb, "id" | "name">): Kb {
  return {
    kind: "doc",
    description: null,
    org: { id: 1, name: "研发中心" },
    owner: { id: 1, username: "zhangsan", name: "张三" },
    visibility: "org",
    status: "enabled",
    embedding_model: {
      id: 5,
      name: "text-embedding-v3",
      embedding_dim: 1024,
      embedding_max_input: 8192,
      status: "enabled",
    },
    doc_count: 128,
    chunk_count: 4316,
    created_at: "2025-10-01T00:00:00+08:00",
    updated_at: "2025-10-20T10:12:00+08:00",
    editable: true,
    manageable: true,
    ...partial,
  };
}

function mutationStub<T>(): T {
  return { mutate: vi.fn(), reset: vi.fn(), isPending: false } as unknown as T;
}

beforeEach(() => {
  mockedUseMe.mockReturnValue({ data: ME } as unknown as ReturnType<typeof useMe>);
  mockedUseOrgs.mockReturnValue({ data: [] } as unknown as ReturnType<typeof useOrgs>);
  mockedUseKbs.mockReturnValue({
    data: { items: [kb({ id: 1, name: "员工制度库" })], total: 5 },
    isLoading: false,
  } as unknown as ReturnType<typeof useKbs>);
  mockedUseDeleteKb.mockImplementation(() => mutationStub());
  mockedUseEnableKb.mockImplementation(() => mutationStub());
  mockedUseEmbeddingModels.mockReturnValue({
    data: { items: [] },
  } as unknown as ReturnType<typeof useEmbeddingModels>);
  mockedUseKbRefs.mockReturnValue({ data: [] } as unknown as ReturnType<typeof useKbRefs>);
  mockedUseDisableKb.mockImplementation(() => mutationStub());
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
          <KbListPage />
        </MemoryRouter>
      </AntApp>
    </QueryClientProvider>,
  );
}

describe("buildKbListParams", () => {
  it("“全部”项不下发，默认只带分页", () => {
    expect(
      buildKbListParams(
        { kind: "all", visibility: "all", orgId: undefined, status: "all", keyword: "" },
        1,
        10,
      ),
    ).toEqual({ page: 1, page_size: 10 });
  });

  it("选中的筛选项拼进查询参数，组织带上含下级", () => {
    expect(
      buildKbListParams(
        { kind: "doc", visibility: "org", orgId: 3, status: "enabled", keyword: "制度" },
        2,
        20,
      ),
    ).toEqual({
      page: 2,
      page_size: 20,
      kind: "doc",
      visibility: "org",
      org_id: 3,
      include_sub: true,
      status: "enabled",
      keyword: "制度",
    });
  });
});

describe("列表筛选项文案", () => {
  it("类型、可见范围、状态的选项与文案", () => {
    expect(KIND_OPTIONS).toEqual([
      { value: "all", label: "全部类型" },
      { value: "doc", label: "文档库" },
      { value: "qa", label: "问答库" },
    ]);
    expect(VISIBILITY_OPTIONS).toEqual([
      { value: "all", label: "全部可见范围" },
      { value: "private", label: "仅自己" },
      { value: "org", label: "本组织" },
      { value: "platform", label: "全平台" },
    ]);
    expect(STATUS_OPTIONS).toEqual([
      { value: "all", label: "全部状态" },
      { value: "enabled", label: "启用" },
      { value: "disabled", label: "已停用" },
    ]);
  });
});

describe("KbListPage", () => {
  it("列表显示知识库与“我能查看的”数量", async () => {
    renderPage();
    expect(await screen.findByText("员工制度库")).toBeInTheDocument();
    expect(screen.getByText("我能查看的 5 个")).toBeInTheDocument();
    expect(screen.getByText("新建知识库")).toBeInTheDocument();
  });

  it("管理权限能看到设置、权限、停用和删除", async () => {
    renderPage();
    const row = (await screen.findByText("员工制度库")).closest("tr");
    fireEvent.click(within(row as HTMLElement).getByLabelText("更多操作"));

    expect(await screen.findByText("设置")).toBeInTheDocument();
    expect(screen.getByText("权限")).toBeInTheDocument();
    expect(screen.getByText("停用")).toBeInTheDocument();
    expect(screen.getByText("删除")).toBeInTheDocument();
  });

  it("协作者（可编辑但不可管理）看不到权限、停用和删除", async () => {
    mockedUseKbs.mockReturnValue({
      data: {
        items: [kb({ id: 1, name: "员工制度库", manageable: false })],
        total: 1,
      },
      isLoading: false,
    } as unknown as ReturnType<typeof useKbs>);
    renderPage();
    const row = (await screen.findByText("员工制度库")).closest("tr");
    fireEvent.click(within(row as HTMLElement).getByLabelText("更多操作"));

    expect(await screen.findByText("设置")).toBeInTheDocument();
    expect(screen.queryByText("权限")).toBeNull();
    expect(screen.queryByText("停用")).toBeNull();
    expect(screen.queryByText("删除")).toBeNull();
  });

  it("只读用户看不到更多操作菜单", async () => {
    mockedUseKbs.mockReturnValue({
      data: {
        items: [kb({ id: 1, name: "员工制度库", editable: false, manageable: false })],
        total: 1,
      },
      isLoading: false,
    } as unknown as ReturnType<typeof useKbs>);
    renderPage();
    const row = (await screen.findByText("员工制度库")).closest("tr");
    expect(within(row as HTMLElement).queryByLabelText("更多操作")).toBeNull();
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
